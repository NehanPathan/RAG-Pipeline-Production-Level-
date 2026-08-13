# GM3 — Govern, Map, Measure, Manage

How this platform implements the four functions of the NIST AI Risk Management
Framework, and exactly where each control lives.

The organising idea: **Govern sets the rules, Map finds where they can break,
Measure proves whether they hold, Manage acts when they don't.** Map → Measure
→ Manage is the loop; Govern is what the loop is accountable to.

The test of any governance implementation is not whether the documents exist.
It is whether a control that gets deleted causes something to fail. Here,
`scripts/check_governance.py` runs in CI and fails the build when the risk
register names a metric that isn't registered, a control whose file no longer
exists, a metric nobody records, or a threshold with no alert rule.

---

## GOVERN — rules as code, not prose

| Control | Where | What it does |
|---|---|---|
| C-GOV-01/02 | [policy.py](../../src/governance/policy.py), [dependencies.py](../../src/api/dependencies.py) | Model and embedding allow-lists, checked at pipeline construction so an unapproved swap fails at startup, not silently in production |
| C-GOV-03/04 | [policy.py](../../src/governance/policy.py), [pipeline.py](../../src/retrieval/pipeline.py) | Grounding rules: refuse with no context, refuse an answer that cites nothing retrieved |
| C-GOV-05 | [admin.py](../../src/api/routes/admin.py) | Settings changes persist and are audited with before/after values |
| C-GOV-06 | [audit.py](../../src/governance/audit.py) | Append-only audit trail of every governed action |
| C-GOV-07 | [conversation_repository.py](../../src/infrastructure/database/postgres/conversation_repository.py) | Both turns of every exchange persisted, with citations and grounding text |
| C-GOV-08 | [trace_context.py](../../src/api/middleware/trace_context.py) | One trace id binding logs, spans, messages and audit entries |
| C-GOV-09/10 | [rbac.py](../../src/governance/rbac.py) | Role allow-lists on mutating routes; clearance derived from role by policy |

**The single policy object.** `AIPolicy` is a frozen dataclass built from
settings. Every threshold in the system is read from it — by the request path,
the evaluation runner, the CI gate, and the alert rules. One definition means
the build cannot pass while production is failing on the same criterion.

```python
policy.min_faithfulness          # 0.75 — one number, four consumers
policy.check_grounding(...)      # request path
policy.check_quality(...)        # eval runner, CI gate, /evaluation/gate
policy.clearance_for_role(...)   # retrieval filter
```

**Enforcement modes.** `governance_enforcement_mode` is `enforce` or
`monitor`. Monitor counts and audits violations but lets requests through —
the intended way to roll out a new control. Ship straight to `enforce` and a
well-meaning control takes down a working system. An alert fires if the policy
sits in monitor mode for more than 24 hours, because a policy left there
enforces nothing.

**Authentication (R-G03) is now closed.** Firebase ID tokens are
cryptographically verified — signature against Google's rotating keys, plus
expiry, issuer and audience — before any identity is trusted. New users are
provisioned at the least-privileged role, and an existing role is never
overwritten by a login, so a promotion is not silently undone.

`Principal.auth_provider` records how each identity was established
(`firebase` or `none`), so an audit entry from a development deployment is
distinguishable from verified attribution rather than being mistaken for it.

With `FIREBASE_ENABLED=false` the system still falls back to the unverified
`X-User-Id` header. That mode is for development only; any deployment
reachable by untrusted callers must enable Firebase. See
[AUTHENTICATION.md](AUTHENTICATION.md).

---

## MAP — know the data, the lineage, and the failure modes

**Data classification.** Four levels — `public` → `internal` → `confidential`
→ `restricted` — applied to a document at upload and inherited by every chunk
derived from it, into Postgres, Qdrant and Elasticsearch.

Enforcement happens at two points, and the second exists because the first can
be incomplete:

1. **Pre-filter (primary).** The clearance allow-list is pushed into the
   Qdrant and Elasticsearch queries, so over-classified chunks never enter the
   candidate set and cannot consume `top_k` slots.
2. **Post-filter ([SensitivityGuard](../../src/governance/sensitivity_guard.py)).**
   Re-checks everything returned. Anything it drops is a *defect*, not a
   routine filter — hence a counter and a critical alert rather than a silent
   discard.

Three concrete reasons the second layer earns its cost: chunks indexed before
the field existed carry no label for a backend to match on; a future retrieval
path could forget the filter; and the two backends express "field missing"
differently, so a mismatch would leak through whichever is more permissive.

**The allow-list is never LLM-derived.** `FilterGenerator` produces metadata
filters from the query, but `QueryPipeline` overwrites `sensitivity_in` from
the resolved principal on every retrieval. An access control an LLM can
influence is not an access control.

**Fail closed.** `Sensitivity.parse(None, INTERNAL)` returns `internal`, never
`public`. An unlabelled document is not a public one.

**Provenance.** The chain is answer → citation → chunk → document, now complete:
`Citation.document_id` was added so a cached answer can be invalidated when its
source document is deleted.

**The risk register.** [`risk_register.yaml`](risk_register.yaml) binds each
risk to the metric that watches it and the controls that mitigate it, with
file paths that CI verifies exist. A risk with no metric is unmanaged; a
metric watching no risk is noise.

---

## MEASURE — offline, online, and at request time

Five layers, each answering a different question:

| Layer | Question | Where |
|---|---|---|
| Logs | What happened? | structlog → JSON, every line carrying `trace_id` |
| Traces | In what order, what was slow? | `TracedStage` → OTel + Langfuse |
| Metrics | How often, across everyone? | Prometheus, 21 metrics |
| Offline eval | Was it good on the golden set? | `evaluation/offline/` |
| Online eval | Is it good *right now*, on real questions? | `evaluation/online/` |

**Offline and online use the same judge.** Writing a second, "lighter" set of
prompts for production would produce numbers on a different scale, and the
first time they disagreed nobody would know which to believe. Same prompts,
same model role, same 0–1 range — so online 0.62 vs offline 0.81 is a fact
about the traffic, not about the metric.

**Sampling is deterministic**, hashed on `trace_id` rather than random: the
same request always makes the same decision, coverage is uncorrelated with
time of day, and it is testable without patching a random source.

**Two fixed defects worth naming:**

- A failed or unparseable judge call used to return `0.0` and `0.5`. Both
  silently invented scores — a judge outage looked identical to terrible
  quality. They now return `None` and are excluded from aggregation.
- Evaluation runs recorded a hardcoded `{"reranker": "passthrough"}`, wrong
  for every real run. `pipeline.describe_config()` now records what actually
  ran, so a score change can be told apart from a config change.

**Evaluation retrieves as a system principal** — otherwise classification
would silently shrink the corpus being measured, and the scores would describe
whatever subset the default clearance happened to allow.

---

## MANAGE — act on what you measured

| Control | Where | What it does |
|---|---|---|
| C-MAN-01 | [runtime_flags.py](../../src/governance/runtime_flags.py) | Five DB-backed kill switches, 10s cache, mirrored into Prometheus |
| C-MAN-02 | [pipeline.py](../../src/retrieval/pipeline.py) | Disabled subsystems return a well-formed refusal, not an error |
| C-MAN-03 | [retention.py](../../src/governance/retention.py) | Retention deadlines enforced across all four stores; dry-run by default |
| C-MAN-04/05 | [stage_tracer.py](../../src/monitoring/stage_tracer.py), [main.py](../../src/api/main.py) | Per-stage Prometheus histograms; OTel actually configured and exported |
| C-MAN-06 | [feedback.py](../../src/evaluation/online/feedback.py) | Negative feedback promoted into the golden dataset, idempotently |

**Kill switches are database-backed, not environment variables**, because an
incident response that requires a redeploy is not an incident response.

**The feedback loop closes.** A thumbs-down becomes a permanent regression case
in a golden dataset that the CI quality gate measures against. Without that
step, feedback is a dashboard nobody acts on.

**Deletion now actually deletes.** It previously covered Postgres, Qdrant and
Elasticsearch but not the semantic cache — so a deleted document kept
answering questions. Reclassification has the same problem in reverse and
re-indexes every chunk plus invalidates cached answers.

---

## The one thing that ties it together: `trace_id`

Every request gets an id, bound in four places at once by
[TraceContextMiddleware](../../src/api/middleware/trace_context.py):

1. **structlog contextvars** — `merge_contextvars` was already the first
   processor, so every downstream log line picks it up with no call-site changes.
2. **A root OTel span** — every `traced_stage()` span nests under it, so the
   pipeline appears as one tree instead of a dozen orphans.
3. **A ContextVar** — readable from `audit.record()`, which has no request access.
4. **The `X-Trace-Id` response header** — so a user reporting a bad answer can
   quote an id rather than an approximate timestamp.

One value walks the whole path:

```
user complaint → trace_id → grep logs → Langfuse span tree
              → which chunks were retrieved → the stored message
              → the online eval score → the audit entries for that request
```

That thread is what turns five separate tools into one system.

---

## Operating it

```bash
make governance-check     # risk register vs metrics, controls, alert rules
make eval-gate            # fail if quality is below the policy floors
make retention-dry-run    # list documents past their retention deadline
```

```
GET  /api/v1/governance/status          one-shot GM3 readiness view
GET  /api/v1/governance/policy          the policy this process actually loaded
GET  /api/v1/governance/risks           risk register, filterable by function
GET  /api/v1/governance/audit           audit trail (steward/admin)
PUT  /api/v1/governance/flags/{flag}    kill switch (admin)
POST /api/v1/governance/retention/run   retention purge (admin, dry-run default)
GET  /api/v1/evaluation/gate            the same judgement CI makes
GET  /api/v1/evaluation/online          rolling live scores vs policy floors
POST /api/v1/feedback                   rate an answer
```

Dashboards: Grafana at `:3000` → *Governance* folder → *RAG Governance (GM3)*,
one row per function. Prometheus at `:9090`, alert rules labelled with the
`risk_id` they belong to.

**PII redaction (R-S03)** is implemented at two points, for different
reasons. Retrieved passages are scrubbed *before* they enter the prompt —
once a passage is in the prompt it has left the deployment, so answer-only
redaction would be too late. Generated answers are scrubbed again, catching
data the model reconstructed or carried in from conversation history.

Detectors validate rather than matching on shape alone (Luhn for cards,
structural checks for SSNs, length bounds for phone numbers), because
over-redaction is a real cost: a redacted passage the model needed produces a
worse answer. Counts are recorded per detector and surface; the values
themselves are never logged, since logging the PII you just redacted moves
the leak rather than closing it.

## What is deliberately not done

- **Unstructured PII.** Regex detection cannot find a name in prose, a home
  address, or a medical detail. For corpora where no passage may leave the
  perimeter at all, the control is a local provider (`ollama`), not the
  redactor.
- **Token revocation is not checked per request.** `check_revoked=False`
  avoids a Firebase round-trip on every call; with a ~1h token lifetime the
  exposure window after a revocation is bounded by that lifetime.
- **Rate limiting for anonymous callers.** Per-identity limiting is
  implemented and enforced (`src/governance/rate_limit.py`, controls
  C-MAN-12/13/14 under R-O06); a request with no principal is outside it and
  needs a per-IP limit at the reverse proxy.
- **Conversations are not clearance-filtered.** A message may quote
  `confidential` passages while the message row carries no classification of
  its own.
- **Semantic cache entries written before `source_document_ids` existed** carry
  no provenance and survive invalidation. The residue decays with cache churn.
