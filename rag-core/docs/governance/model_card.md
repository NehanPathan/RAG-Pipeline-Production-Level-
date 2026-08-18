# Model Card — Enterprise Agentic RAG Platform

Covers the *system*, not a single model: this platform composes several models
whose failure modes interact, and describing any one of them alone would
understate the risk.

Last reviewed: 2026-08-11 · Policy version: 1.0.0 · Owner: platform-team

---

## What it does

Answers natural-language questions over a corpus of user-uploaded documents,
returning an answer with inline `[n]` citations to the passages it used.

**Intended use.** Internal knowledge retrieval over documents the operator
controls — policy lookup, procedure questions, document summarisation, "where
does it say X".

**Out of scope.** Not for clinical, legal or financial *advice*; not an
authority on anything outside its indexed corpus; not a decision-maker. It
retrieves and summarises what its documents say. If the corpus is wrong, the
answer is wrong and confidently formatted.

---

## Components

| Role | Default | Configured by | Approved list |
|---|---|---|---|
| Answer generation | `gpt-4o` | `large_llm_provider` | `governance_allowed_llm_providers` |
| Query intelligence, compression, judging | `gpt-4o-mini` | `small_llm_provider` | same |
| Retrieval embeddings | `text-embedding-3-large` (3072d) | `embedding_provider` | `governance_allowed_embedding_models` |
| Chunking embeddings | `BAAI/bge-m3` | `chunking_embedding_provider` | same |
| Reranking | `BAAI/bge-reranker-large` | `reranker_provider` | — |

The allow-lists are enforced at pipeline construction: an unapproved provider
raises `PolicyViolation` at startup rather than serving traffic until someone
notices. See C-GOV-01/02.

Two embedding roles are deliberate. Chunking only needs relative similarity for
topic-boundary detection, so it can use a cheaper local model without touching
the vectors stored in Qdrant.

---

## How an answer is produced

```
query → intent classification, rewriting, expansion (3 variants)
      → hybrid retrieval: vector (Qdrant, top 20) + BM25 (Elasticsearch, top 20)
        ...filtered by the caller's clearance BEFORE search runs
      → RRF fusion → rerank to top 10
      → dedup → LLM compression → 6000-token budget → citation indices
      → streamed generation → citation validation → grounding policy check
```

A semantic cache short-circuits near-duplicate queries above 0.95 similarity.

---

## Evaluation

Four LLM-judge metrics, 0–1, scored offline on a golden dataset and online on
a deterministic sample of live traffic using **the same prompts**:

| Metric | Measures | Policy floor |
|---|---|---|
| `faithfulness` | Answer uses only the retrieved contexts | **0.75 (gated)** |
| `answer_relevancy` | Answer addresses the question | **0.70 (gated)** |
| `context_relevancy` | Retrieved contexts are relevant | **0.60 (gated)** |
| `answer_correctness` | Matches ground truth | measured, not gated |

Gated metrics block CI (`scripts/eval_gate.py`) and fire Prometheus alerts at
the same thresholds, read from the same `AIPolicy` object.

**Be honest about what these numbers are.** They are one LLM's opinion of
another LLM's output, parsed from a single digit. They are useful for detecting
*change* — a drop from 0.85 to 0.68 is a real signal — and weak as absolute
statements of quality. An unparseable or failed judge call is recorded as `None`
and excluded, never silently scored as 0.0 or 0.5.

---

## Limitations and failure modes

**Hallucination (R-T01, critical).** The system can produce a fluent,
well-cited, wrong answer. Mitigated by required citations, context compression,
a grounding refusal, and faithfulness scoring. `CitationValidator` catches the
specific case of a citation marker pointing at a passage never retrieved —
the clearest hallucination signal available without a judge. It does *not*
catch an unsupported claim attached to a real citation. Residual risk: moderate.

**Retrieval miss (R-T02, high).** If hybrid search does not surface the right
passage, the system answers incompletely or refuses even though the corpus
holds the answer. Query expansion and hybrid search reduce this; they do not
eliminate it.

**Refusals are a designed outcome.** With no retrieved context, or an answer
citing nothing, the system returns a refusal rather than guessing. Counted as
`rag_answers_total{outcome="refused"}`, not as an error.

**Source quality is inherited.** No claim is verified against anything but the
uploaded documents. Outdated, contradictory or wrong source material produces
outdated, contradictory or wrong answers.

**Language.** The BM25 analyser is English. Non-English corpora will retrieve
measurably worse.

**Bias.** No bias evaluation has been performed. The system inherits whatever
is in the corpus and whatever the underlying models carry. This is a stated
gap, not a claim of neutrality.

---

## Data handling

Every document carries a classification (`public` / `internal` /
`confidential` / `restricted`), inherited by its chunks and enforced at
retrieval time — the model never receives passages the caller cannot read.
Unlabelled data is treated as `internal`, never `public`.

Documents, chunk text, conversations, and the grounding text behind each answer
are retained for `governance_retention_days` (default 365). See
[data_card.md](data_card.md).

---

## Operational envelope

- Costs roughly one large-model generation plus three small-model calls per
  query, plus three judge calls per sampled answer (default 5% of traffic).
- p95 latency alerts at 10s. Answer generation dominates.
- Degrades to a refusal, not an error, when subsystems are disabled.

## Governance

Full control mapping in [GM3_FRAMEWORK.md](GM3_FRAMEWORK.md); risks and their
watching metrics in [risk_register.yaml](risk_register.yaml).

**Authentication is implemented** (R-G03, closed). Firebase ID tokens are
cryptographically verified before any identity is trusted; new users are
provisioned at the least-privileged role, and an existing role is never
overwritten by a login. `Principal.auth_provider` records how each identity
was established, so a development request is distinguishable from a verified
one in the audit trail.

Setting `FIREBASE_ENABLED=false` falls back to an unverified `X-User-Id`
header and is for local development only. Any deployment reachable by
untrusted callers must enable it — see
[AUTHENTICATION.md](AUTHENTICATION.md).

Per-identity rate limits apply to the expensive routes (chat, upload); see
R-O06. Unauthenticated endpoints are not limited, so a reverse proxy should
still carry a coarse per-IP limit in front of this.
