# Query Routing, Tools, Workflows and the AI Gateway

Four systems that arrived together because they solve one problem: the
pipeline did exactly one thing, the same way, for every query.

---

## 1. The Query Router

**Before.** Every query took the same path:

```
query → embed → vector search → BM25 search → RRF fusion
      → rerank → dedup → LLM compression → generate
```

`"hi"` cost the same as `"what is our EU refund policy for enterprise
customers"`: roughly one embedding call, two searches, a cross-encoder pass,
one small-model compression call and one large-model generation. In real
traffic, the majority of queries do not need any of it.

**After.**

```
                          query
                            │
                  ┌─────────▼──────────┐
                  │   1. Rules         │  free, instant, deterministic
                  └─────────┬──────────┘
                            │ no match
                  ┌─────────▼──────────┐
                  │   2. Heuristics    │  recency vs document signal
                  └─────────┬──────────┘
                            │ no match
                  ┌─────────▼──────────┐
                  │   3. Classifier    │  one small-model call
                  └─────────┬──────────┘
                            │
   ┌──────────┬─────────┬───┴────┬──────────┬──────────┬─────────┐
   ▼          ▼         ▼        ▼          ▼          ▼         ▼
greeting  calculator  datetime translation  llm      web      sql    rag
   │          │         │        │        knowledge  search    │      │
   0 calls   0 calls  0 calls  1 small    1 large   external  read   full
                                                              only  pipeline
```

### Why rules run before the LLM

A deterministic rule that matches is free, instant, reproducible, and
testable without mocking a model. The classifier call only happens for
queries no rule recognises — which in practice is the interesting minority.

### Why ambiguity resolves to RAG

Below `ROUTER_MIN_CONFIDENCE` (default 0.6) the router chooses full
retrieval. The two failure directions are not symmetric:

| Wrong direction | Cost |
|---|---|
| Retrieved when it didn't need to | money and ~1s of latency |
| Didn't retrieve when it needed to | a confident, ungrounded, possibly wrong answer |

So the fallback is deliberately the expensive-but-safe one. The same logic
drives `mentions_documents()` suppressing the recency heuristic: *"what does
our **latest** policy say"* contains a recency signal but is unambiguously a
document question.

### Non-RAG answers are marked

Every routed answer carries `"grounded": false` and its `route`. A client
must not present model knowledge with the same authority as a cited passage —
that confusion is precisely what a RAG system exists to prevent.

### The kill switch

`enable_query_router=false` restores the previous always-RAG behaviour
instantly. A routing regression should be recoverable by flipping a flag, not
by a rollback.

### What it's worth

Watch `rag_retrieval_skipped_total`. Every increment is an embedding call, two
searches, a rerank and a compression call that did not happen.

---

## 2. The Tool Layer

```
src/tools/
├── base.py           Tool ABC — run() wraps execute() with tracing + metrics
├── registry.py       PluginRegistry[Tool] + the classifier's tool menu
└── builtin/
    ├── calculator.py    AST evaluation, never eval()
    ├── datetime_tool.py clock
    ├── translation.py   small-model, no retrieval
    ├── web_search.py    Tavily / Serper — DISABLED by default
    └── sql_query.py     read-only, allow-listed — DISABLED by default
```

Adding a capability is dropping a module into `builtin/`. The classifier's
menu is generated from the live registry, so a new tool becomes selectable
the moment it registers — no prompt to keep in sync.

### Security posture

Tool input originates from user text that has usually passed through an LLM,
so it is untrusted twice over.

**The calculator parses an AST.** `eval()` on model-influenced text is remote
code execution — `__import__("os").system(...)` is a valid Python expression.
An explicit node allow-list makes that unrepresentable rather than merely
discouraged. Resource bounds (expression length, exponent ceiling, factorial
ceiling) stop `2 ** 10**9` becoming a denial of service.

**The SQL tool has four independent controls**, because any one can be wrong:

1. Role gate — analyst or above, checked by the tool against the principal it
   is given, not delegated to the router.
2. Statement shape — SELECT/WITH only, single statement.
3. Table allow-list — empty by default, so the tool is unusable until someone
   configures it. Never "all tables".
4. `SET TRANSACTION READ ONLY` — **the actual guarantee**. Keyword filtering
   on SQL is famously bypassable; anyone relying on it alone eventually gets
   burned. Postgres rejects writes here regardless of what slipped past.

**Blast-radius tools ship disabled.** `enable_web_search` and
`enable_sql_tool` default false, and the flag is enforced inside the registry
so a disabled tool cannot be constructed by a path that forgot to check.

---

## 3. The Workflow Engine

`QueryPipeline.answer()` had grown to hold the kill-switch check, cache
lookup, routing, retrieval, context processing, two policy checks and the
streaming loop — with control flow expressed as early returns threaded
through an async generator.

`src/workflow/` states the same logic as nodes and edges:

```python
workflow = (
    Workflow("answer")
    .add(CheckKillSwitchNode(), next_="cache")
    .add(CacheLookupNode(), next_=lambda ctx: "done" if ctx.get("hit") else "route")
    .add(RouteNode(), next_="retrieve")
    ...
)
```

Each node is independently testable and independently traced
(`rag_workflow_node_executions_total`). A node can name its own successor,
which is what branching needs and an if/elif chain cannot express cleanly.

Deliberately sequential: workflows express *decision flow*, not parallelism.
The place that genuinely needs concurrency — vector and BM25 search together —
uses `asyncio.gather` inside a single node, keeping concurrency where it is
actually understood.

Cycles are caught by a step cap. Without it a misconfigured backward edge
reads as a hang rather than the configuration error it is.

---

## 4. The AI Gateway

**Before.** `get_llm_provider()` returned a bare provider. Every call site was
bound to whichever one it was handed, so switching vendors meant editing
every call site, an outage meant a failed request, and cost was measurable
nowhere.

**After.** One object every LLM call passes through:

```
caller → LLMGateway(role) → [openai] → [anthropic] → [ollama] → …
                                ↑ fallback chain, config-driven
```

`LLMGateway` implements `LLMProvider`, so it is a drop-in replacement — every
existing agent, compressor and generator gained fallback and cost tracking
with no call-site change.

| Concern | How the gateway makes it possible |
|---|---|
| **Swappability** | `LLM_FALLBACK_PROVIDERS=anthropic,openai` — a config change |
| **Resilience** | Provider error falls through to the next vendor |
| **Cost** | Priced at the only place that sees model *and* tokens |
| **Governance** | The approved-model check has exactly one home |

### Providers

| Name | Implementation |
|---|---|
| `openai` | Native |
| `anthropic` | Native (Messages API; usage arrives on stream events) |
| `ollama` | OpenAI-compatible `/v1` |
| `openrouter` | OpenAI-compatible |
| `azure` | OpenAI-compatible (addresses a *deployment*, not a model) |

One `OpenAICompatibleProvider` covers three vendors, plus vLLM, Groq and
Together. Writing one class per vendor would be the same file with different
constants. It negotiates `stream_options` rather than assuming it: several
compatible servers reject the unknown field, so a first failure disables it
for that instance and retries.

### Streaming fails over only before the first token

Once a token reaches the client, switching providers would splice two
different answers together. A mid-stream failure propagates instead.

### Unpriced models record nothing, not zero

A silent zero would understate spend on exactly the models nobody has
reviewed the price of yet — the opposite of what a cost control should do.

---

## 5. Plugin Architecture

The project had four hand-rolled registries (LLM, embedders, OCR, rerankers),
each an if/elif chain with subtly different error behaviour. Adding tools
would have made five.

`PluginRegistry[T]` is one generic implementation:

```python
tools: PluginRegistry[Tool] = PluginRegistry("tool")

@tools.register("calculator", requires_flag="enable_calculator")
def _build() -> Tool: ...
```

Registration is a decorator, so an implementation declares itself rather than
being enumerated elsewhere — which is what makes a plugin *directory* work.
`requires_flag` is enforced inside `create()`, so a disabled plugin cannot be
instantiated by a path that forgot to check.

`GET /governance/plugins` reports what is actually loaded. A registry
populated by import side effects can silently lose an entry when a module
fails to import; this endpoint is how that becomes visible instead of
presenting as "the router never picks that tool".

---

## 6. Feature Flags

Two scopes in one system:

| Scope | Purpose | Changed by |
|---|---|---|
| `capability` | Is this feature on? | environment default, or admin |
| `kill_switch` | Stop something misbehaving | admin, in seconds, always audited |

Resolution order: **operator override (database) → environment default →
code default.** Environment defaults let staging enable web search while
production does not; database overrides let an incident be handled without a
redeploy.

Unknown flag names raise rather than reading as `False` — a typo'd flag
silently disabling a feature is a bug that takes a long time to find. But an
*unknown stored override* is ignored with a warning: a stale row naming a
removed flag must never break flag loading, since that would take out the
kill switches at exactly the moment someone needs them.

---

## Cross-references

- Authentication: [docs/governance/AUTHENTICATION.md](../governance/AUTHENTICATION.md)
- Controls and risks: [docs/governance/GM3_FRAMEWORK.md](../governance/GM3_FRAMEWORK.md)
- Risk register: [docs/governance/risk_register.yaml](../governance/risk_register.yaml)
