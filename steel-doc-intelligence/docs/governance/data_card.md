# Data Card — Enterprise Agentic RAG Platform

What data this system holds, where it lives, who can read it, and when it goes
away.

Last reviewed: 2026-08-06 · Policy version: 1.0.0 · Owner: security-team

---

## Data this system stores

| Data | Store | Contains | Classification |
|---|---|---|---|
| Uploaded documents | filesystem (`./uploads`) | Original files as supplied | Per-document |
| Document metadata | Postgres `documents`, `document_metadata` | Filename, type, size, LLM-generated summary, tags, entities | Per-document |
| Chunk text | Postgres `document_chunks` | Full passage text | Inherited from document |
| Chunk vectors + payload | Qdrant | Embedding plus a copy of the passage text | Inherited |
| Chunk text (searchable) | Elasticsearch | A second copy of the passage text | Inherited |
| Conversations | Postgres `conversations`, `messages` | User questions, generated answers, citations, **the exact grounding text** | Not classified |
| Cached answers | Qdrant `semantic_query_cache` | Whole generated answers plus source document ids | Not classified |
| Audit trail | Postgres `audit_log` | Who did what, before/after values | Restricted in practice |
| Online eval samples | Postgres `online_eval_samples` | Live questions, answers, judge scores | Not classified |
| Feedback | Postgres `user_feedback` | Ratings, free-text comments | Not classified |

**Passage text exists in three places** — Postgres, Qdrant and Elasticsearch —
by design, for durability, vector search and lexical search respectively. Any
deletion or reclassification must therefore fan out across all three plus the
cache. That fan-out is implemented; the cache leg was the gap that made
deletion incomplete until governance work closed it.

**A user's question is stored verbatim.** If users paste sensitive content into
a query, that content lands in `messages`, potentially in
`online_eval_samples`, and is sent to the LLM provider. Nothing redacts it —
see *Known gaps*.

---

## Classification

Four levels, ordered:

| Level | Meaning | Minimum role |
|---|---|---|
| `public` | No restriction | viewer |
| `internal` | Default for unlabelled data | analyst |
| `confidential` | Restricted business content | steward |
| `restricted` | Highest sensitivity | admin |

Applied at upload and inherited by every derived chunk. **Unlabelled data
resolves to `internal`, never `public`** — a document nobody classified is not
a public one, and every parse path fails in that direction.

A caller cannot classify a document above their own clearance; otherwise upload
would be a way to create data you are then unable to review.

Reclassification is steward/admin only, always audited with both values, and
re-indexes every chunk across all stores plus invalidates cached answers. A
label change that did not re-index would appear to work while behaving
unchanged — the most dangerous kind of failure for an access control.

---

## Who can read what

Clearance is derived from role by policy, not stored per user, so
re-classifying what a role may read is one config change and one audit entry
rather than a migration across every user row.

```
viewer   → public
analyst  → internal
steward  → confidential
admin    → restricted
```

Enforced at two points: pushed into the Qdrant/Elasticsearch queries before
search runs (so unreadable passages never enter the candidate set), and
re-checked in process afterwards. Anything the second check drops is treated
as a defect and alerts.

Document-level endpoints (`GET /documents/{id}`, `/chunks`, `/intelligence`)
return **404, not 403**, when clearance fails — a 403 would confirm the
document exists, which is itself information the caller is not cleared for.

Listing applies the clearance filter **inside the SQL query**, before both the
`COUNT` and the `LIMIT`/`OFFSET`. Filtering the page after the database
returned it would still disclose an accurate total of documents the caller
may not read, and would return short pages whose length reveals how many were
withheld.

---

## Retention and deletion

Documents get `retention_until = created_at + governance_retention_days`
(default 365). The retention job purges expired documents across Postgres,
Qdrant, Elasticsearch and the semantic cache.

**It defaults to a dry run.** Irreversible bulk deletion driven by a date
column should require someone to opt in after reading what it intends to
remove: `POST /governance/retention/run?dry_run=false`.

Manual deletion (`DELETE /documents/{id}`) is steward/admin only, audited, and
covers the same four stores.

**Not covered by deletion:**
- `messages` rows quoting a deleted document's content in a past answer.
- `online_eval_samples` rows containing that answer.
- `audit_log` entries — deliberately. An audit trail that deletion can erase is
  not an audit trail.
- Cache entries written before `source_document_ids` existed carry no
  provenance and survive invalidation. This residue decays with cache churn.
  Clearing the whole cache on every delete was the alternative: it trades a
  small decaying leak for a guaranteed latency cliff on every deletion.

---

## Third-party data flow

Sent outside the deployment on every query:

| Recipient | What | When |
|---|---|---|
| OpenAI (or configured provider) | The user's query, retrieved passage text, the prompt | Every query |
| OpenAI | Passage text for embedding | Ingestion, and every uncached query |
| Cohere | Query + passages | Only if `reranker_provider=cohere` |
| Langfuse | Span inputs/outputs, including passage text | If keys are configured |

**Retrieved passages leave the deployment.** Classification controls who can
*trigger* retrieval of a passage; it does not stop that passage reaching the
LLM provider. If `restricted` content may not leave the perimeter, use a local
provider (`ollama`) — the allow-list supports it — rather than relying on the
clearance filter.

---

## PII redaction

`GOVERNANCE_PII_REDACTION_ENABLED` is now functional. Nine validated
detectors — email, credit card (Luhn-checked), SSN (structure-checked), phone
(length-bounded), IBAN, IP address, provider API keys, passport numbers and
labelled dates of birth — run at two points:

| Surface | When | Why |
|---|---|---|
| Retrieved context | Before the prompt is built | Once a passage is in the prompt it has left the deployment |
| Generated answers | Before display | Catches PII the model reconstructed or carried in from history |

Detectors validate rather than matching on shape alone. Without a Luhn check
a 16-digit regex flags every order number; without a structure check, dates
like `123-45-6789` become SSNs. Over-redaction is a real cost — a redacted
passage the model needed produces a worse answer.

Counts are recorded per detector and surface (`rag_pii_redactions_total`).
**Values are never logged** — logging the PII you just redacted moves the
leak rather than closing it.

Tune per deployment with `GOVERNANCE_PII_DETECTORS`. Corpora containing many
identifier-like strings usually want to drop `passport` and `ip_address`.

## Known gaps

- **Unstructured PII is not detected.** Regex cannot find a name in prose, a
  home address, or a medical detail. Where no passage may leave the perimeter
  at all, the control is a local LLM provider (`ollama`), not the redactor.
- **Conversations are not classified.** A message may quote `confidential`
  passages while the message row carries no classification of its own, so
  conversation history is not clearance-filtered.
- **Uploaded originals are not encrypted at rest** beyond whatever the
  underlying volume provides.
- **`FIREBASE_ENABLED=false` disables authentication.** In that mode every
  control above rests on an unverified `X-User-Id` header. Development only.

Related: [GM3_FRAMEWORK.md](GM3_FRAMEWORK.md) ·
[risk_register.yaml](risk_register.yaml) · [model_card.md](model_card.md)
