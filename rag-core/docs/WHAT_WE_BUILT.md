# What We Built — A Plain-English Walkthrough

*Read this first. It explains every change we made, why we made it, and how it
works — in ordinary language, with no assumed background. If you can follow
this document, you can explain the whole project to someone else.*

---

## Table of contents

1. [The one-paragraph summary](#1-the-one-paragraph-summary)
2. [What the system was before](#2-what-the-system-was-before)
3. [The five things that were quietly broken](#3-the-five-things-that-were-quietly-broken)
4. [Part One: Governance (GM3)](#4-part-one-governance-gm3)
5. [Part Two: The Query Router](#5-part-two-the-query-router)
6. [Part Three: Tools](#6-part-three-tools)
7. [Part Four: The AI Gateway](#7-part-four-the-ai-gateway)
8. [Part Five: Plugins, Workflows, Feature Flags](#8-part-five-plugins-workflows-feature-flags)
9. [Part Six: Authentication](#9-part-six-authentication)
10. [Part Seven: PII Redaction](#10-part-seven-pii-redaction)
11. [How it all fits together](#11-how-it-all-fits-together)
12. [The bugs we found along the way](#12-the-bugs-we-found-along-the-way)
13. [How to explain this to someone else](#13-how-to-explain-this-to-someone-else)

---

## 1. The one-paragraph summary

We took a RAG system that worked, and made it **trustworthy, efficient, and
controllable**. Trustworthy: you can now prove who asked what, what the system
answered, and whether that answer was any good. Efficient: it no longer pays
for an expensive document search when someone just types "hi". Controllable:
you can turn features on and off, stop it in an incident, and see everything
it's doing — without a redeploy.

---

## 2. What the system was before

The project was already good. It could:

- Take your documents (PDFs, Word files, etc.) and break them into pieces
- Turn those pieces into numbers ("embeddings") that capture meaning
- Search them two ways at once — by meaning (Qdrant) and by keyword
  (Elasticsearch) — then merge the results
- Feed the best pieces to an AI model and stream back an answer with citations

That's a genuinely solid RAG pipeline. Nothing was wrong with it.

The problems were all in the layer *around* it — the parts that let you
operate a system rather than just run it.

---

## 3. The five things that were quietly broken

These are worth understanding, because they're the reason for most of what
follows. Each one **looked fine** from the outside, which is what made them
dangerous.

### 3.1 The tracing was switched off

The code had beautiful instrumentation. Every stage of the pipeline was
wrapped in a `traced_stage(...)` block that recorded how long it took.

But the one function that turns tracing **on** — `configure_tracing()` — was
never called. So every trace was written to nowhere.

> **The analogy:** the whole building was wired for CCTV, cameras in every
> room, and nobody had plugged in the recorder.

**Fix:** one call at startup. Every existing trace instantly started working.

### 3.2 Six of seven metrics were never recorded

The system declared metrics like `rag_query_latency_seconds` and
`rag_llm_tokens_total` — but nothing ever wrote to them.

This is **worse than having no metric at all.** A missing metric is obviously
missing. A metric that always reads zero looks like good news.

> **The analogy:** a fuel gauge that's painted on. It's not broken — it says
> "full" — it just isn't connected to the tank.

**Fix:** wired every one of them to a real recording point. And we added a CI
check that now *fails the build* if anyone declares a metric and forgets to
record it, so this can't come back.

### 3.3 There was no way to connect anything to anything

Logs existed. Traces existed. Answers existed. But nothing tied them together.
If a user said "the answer I got at 3pm was wrong", you had no way to find
that specific request.

**Fix:** one **trace ID** per request, bound in four places at once — the logs,
the trace, the saved message, and a response header the user can quote back.

Now one ID walks you the whole way:

```
user complaint → trace ID → find the logs → open the trace tree
              → see which documents were retrieved → read the stored answer
              → check its quality score → see the audit entries
```

### 3.4 Nothing was ever saved

The system answered questions and then forgot them. `chat.py` literally
generated a random ID for each message and threw it away.

This means: no audit trail, nothing for users to give feedback on, and no
population of real answers to measure quality against.

**Fix:** both sides of every conversation are now saved — the question, the
answer, the citations, and the exact text the model was looking at.

### 3.5 Deleting a document didn't delete it

Deleting removed the document from Postgres, Qdrant and Elasticsearch — three
of the four places it lived. The fourth was the **semantic cache**, which
stores whole finished answers.

So a "deleted" document would keep answering questions.

**Fix:** deletion now covers all four stores. To make that possible we had to
add `document_id` to citations, so a cached answer knows which documents it
came from.

---

## 4. Part One: Governance (GM3)

### What "GM3" means

It's the **NIST AI Risk Management Framework** — an international standard for
running AI systems responsibly. Four functions:

| Function | The question it answers | In one word |
|---|---|---|
| **Govern** | What are our rules? | Rules |
| **Map** | Where can things go wrong? | Risks |
| **Measure** | Are the rules actually holding? | Proof |
| **Manage** | What do we do when they don't? | Action |

Map → Measure → Manage is a loop. Govern is what the loop is accountable to.

### The core idea: rules as code, not prose

Most "AI governance" is a document nobody reads. We made it a **Python object
that the system actually obeys**.

`src/governance/policy.py` holds one frozen `AIPolicy`:

```python
policy.min_faithfulness        # 0.75 — one number
policy.allowed_llm_providers   # which models are approved
policy.require_citations       # must answers cite sources?
```

The important part: **four different things read that same number.**

- The code that decides whether to release an answer
- The evaluation runner
- The CI pipeline that blocks a bad merge
- The alert that pages someone at 3am

If they each had their own copy, they'd drift — and eventually the build would
pass while production was failing on the identical criterion.

### Govern — the rules

**Approved models.** You list which AI providers are allowed. If someone
changes a config to use an unapproved one, the system **refuses to start**.
Not "logs a warning" — refuses. Because embeddings and reranking are invisible
in the output, a swap could otherwise run for weeks unnoticed.

**Grounding rules.** An answer that cites nothing gets refused. There are two
distinct failures here, and we treat them separately:

- Nothing was retrieved → the model has no grounds → refuse *before* calling
  the model (saves money too)
- Something was retrieved but the answer cites none of it → the model ignored
  its grounds → refuse *after*

**The audit log.** An append-only record of every consequential action: who
changed a setting, who deleted a document, who flipped a switch — with before
and after values.

There's no update path in the code. **An audit trail you can edit is not an
audit trail.**

**Enforcement modes.** Every rule runs in one of two modes:

- `monitor` — counts and logs violations, lets requests through
- `enforce` — actually blocks

You roll a new rule out in `monitor` first, watch the number for a week, then
switch. Shipping straight to `enforce` is how a well-meaning safety rule takes
down a working product.

### Map — knowing your data and your risks

**Data classification.** Every document gets one of four labels:

```
public  →  internal  →  confidential  →  restricted
(least sensitive)                      (most sensitive)
```

Each user has a **clearance** derived from their role. You can only see
documents at or below your clearance.

The clever part is *where* this is enforced. There are two layers:

1. **Before the search runs.** The allow-list is pushed down into the Qdrant
   and Elasticsearch queries, so forbidden documents never even enter the
   candidate pool.
2. **After results come back.** A second check in Python re-examines
   everything.

Why both? Because layer one can be incomplete — documents indexed before this
feature existed have no label, a future code path might forget the filter, and
the two search engines express "field is missing" differently.

**Anything layer two catches is treated as a bug, not a routine filter** — it
increments a counter and fires a critical alert, because it means layer one
failed.

**Fail closed.** An unlabelled document is treated as `internal`, never
`public`. A document nobody classified is not a public document.

**The allow-list never comes from the AI.** The system has an AI component that
generates search filters. We deliberately **overwrite** its sensitivity filter
with the one derived from the logged-in user. An access control that a
language model can influence is not an access control.

**The risk register** (`docs/governance/risk_register.yaml`) is the piece
people find most interesting. It's a YAML file listing 20 risks. Each one names:

- What can go wrong
- **The metric that watches it**
- **The controls that mitigate it, with file paths**

And here's the part that makes it real: a script (`scripts/check_governance.py`)
runs in CI and **fails the build** if:

- A risk names a metric nobody records
- A control names a file that no longer exists
- A metric is declared but never written to
- A risk has a threshold but no alert rule

> **Why this matters:** normally a risk document drifts from the code within
> weeks and starts claiming controls that were deleted. This one *can't*,
> because the claim is checked on every push.

When we first ran it, it immediately found four real problems — including a
metric that was permanently zero.

### Measure — proving it works

Five layers, each answering a different question:

| Layer | Question |
|---|---|
| Logs | What happened? |
| Traces | In what order, what was slow? |
| Metrics | How often, across everyone? |
| Offline eval | Was it good on our test questions? |
| Online eval | Is it good *right now*, on real questions? |

**Offline evaluation** runs a set of known questions through the system and has
an AI judge score the answers on faithfulness, relevancy and correctness.

**Online evaluation** does the same on a small sample of real traffic — using
**the exact same judge prompts**. That matters: if we'd written a separate
"lighter" judge for production, the two numbers would be on different scales,
and the first time they disagreed nobody would know which to believe.

Sampling is **deterministic** — decided by hashing the trace ID, not by random
chance. Same request, same decision. Reproducible, and testable without
faking a random number generator.

**Two real bugs we fixed here:**

- A failed judge call used to score **0.0**, and an unparseable one **0.5**.
  Both silently invented a number. A judge outage looked identical to terrible
  quality. Now they return "no score" and are excluded from the average.
- Every evaluation run recorded a hardcoded configuration
  (`{"reranker": "passthrough"}`) that was wrong for every real run. Now it
  records what actually ran — otherwise you can't tell a quality change from a
  config change.

### Manage — acting when things go wrong

**Kill switches.** Five switches stored in the database, readable in ~10
seconds everywhere:

```
answering_enabled       ← the master switch
retrieval_enabled
ingestion_enabled
enable_semantic_cache
enable_online_eval
```

Why the database and not a config file? Because **an incident response that
requires a redeploy is not an incident response.** If the system starts
misbehaving at 2am, you flip a switch.

**Retention.** Documents get an expiry date, and a job enforces it across all
four stores. It defaults to a **dry run** — irreversible bulk deletion driven
by a date column should require someone to explicitly opt in after reading
what it's about to remove.

**The feedback loop.** This is the satisfying part. A user gives a thumbs-down
→ that question is automatically added to the golden test set → the next
evaluation run measures it → the CI gate blocks a merge if it's still broken.

Without that last step, feedback is just a dashboard nobody acts on.

---

## 5. Part Two: The Query Router

### The problem, in one line

Every single question took the same expensive path.

```
"hi"  →  embed  →  vector search  →  keyword search  →  merge
      →  rerank  →  compress with AI  →  generate answer
```

Typing "hi" cost the same as asking a genuine research question: roughly one
embedding call, two database searches, a reranking pass, one AI call to
compress the context, and one AI call to answer.

Most real traffic doesn't need any of that.

### The solution

Decide **how** to answer before paying to answer.

```
                        your question
                             │
                 ┌───────────▼────────────┐
                 │  1. Rules              │  free, instant
                 └───────────┬────────────┘
                             │ no match?
                 ┌───────────▼────────────┐
                 │  2. Heuristics         │  cheap hints
                 └───────────┬────────────┘
                             │ still no match?
                 ┌───────────▼────────────┐
                 │  3. AI classifier      │  one small AI call
                 └───────────┬────────────┘
                             │
  ┌────────┬─────────┬───────┴───┬──────────┬─────────┬────────┬──────┐
  ▼        ▼         ▼           ▼          ▼         ▼        ▼      ▼
greeting  maths    date/time  translate  general    web      SQL    full
                                          knowledge search          RAG
  │        │         │           │          │         │       │      │
 0 AI     0 AI      0 AI       1 small    1 large   external read  everything
 calls    calls     calls       call       call              only
```

### Three design decisions worth explaining

**1. Rules run before the AI.**

A rule that matches is free, instant, and gives the same answer every time.
The AI classifier only runs for questions no rule recognises — which turns out
to be the interesting minority.

**2. When unsure, do the expensive thing.**

If the classifier isn't confident (below 60%), the router chooses full
document search. The two failure directions are **not equally bad**:

| Wrong direction | What it costs |
|---|---|
| Searched when it didn't need to | a bit of money, ~1 second |
| Didn't search when it needed to | a confident, wrong answer |

So the fallback is deliberately the expensive-but-safe one.

**3. Non-document answers are labelled.**

If the answer came from the model's own knowledge rather than your documents,
the response says `grounded: false` and the UI shows a warning.

An ungrounded answer that *looks* identical to a cited one is exactly the
confusion a RAG system exists to prevent.

### Seeing the benefit

Watch the metric `rag_retrieval_skipped_total`. Every time it goes up, that's
one embedding call, two searches, a rerank and a compression call that
**didn't happen**.

### If it goes wrong

`FEATURE_ENABLE_QUERY_ROUTER=false` restores the old always-search behaviour
instantly. A routing mistake should be fixable by flipping a switch, not by
rolling back a deployment.

---

## 6. Part Three: Tools

The router needs somewhere to send things. Tools are those destinations.

| Tool | What it does | Default |
|---|---|---|
| `calculator` | Arithmetic | ON |
| `datetime` | Current date/time | ON |
| `translation` | Translate text | ON |
| `web_search` | Live web results | **OFF** |
| `sql_query` | Read-only database queries | **OFF** |

Adding a new tool means dropping one file into `src/tools/builtin/`. It
registers itself, and the AI classifier's menu updates automatically — there's
no prompt to keep in sync.

### The security thinking

Tool input comes from user text that has usually passed through an AI model.
So it's untrusted **twice over**.

**The calculator never uses `eval()`.**

This is worth understanding. In Python, `eval("2 + 2")` gives 4. It also
happily runs:

```python
__import__("os").system("rm -rf /")
```

That's a perfectly valid Python *expression*. If you `eval()` text that an AI
model influenced, you have handed over your server.

Instead we **parse** the expression into a tree and walk it, allowing only
arithmetic nodes. Anything else — imports, attribute access, function
definitions — simply cannot be represented. It's not discouraged; it's
impossible.

We also cap things: expression length, exponent size (`2 ** 10000000` would
allocate until the machine dies), and factorial size.

**The SQL tool has four independent locks:**

1. You must be an analyst or above
2. Only `SELECT` statements, only one statement at a time
3. An explicit list of allowed tables — **empty by default**, so the tool is
   unusable until someone deliberately configures it
4. `SET TRANSACTION READ ONLY` — **this is the real one**

Why four? Because keyword filtering on SQL is famously easy to sneak past.
Anyone who relies on it alone eventually gets caught out. The read-only
transaction is a guarantee from PostgreSQL itself: it will reject a write
regardless of what slipped through the text checks.

**Risky tools ship off.** Web search sends your users' questions to a third
party and costs money per call. SQL turns AI output into database queries.
Both should be a decision someone makes, not a default they inherit.

---

## 7. Part Four: The AI Gateway

### Before

Every part of the code that needed an AI model got handed one directly. That
meant:

- Switching providers = editing every one of those places
- A provider outage = the whole product fails
- Nobody could measure cost

### After

One object that every AI call goes through:

```
your code → LLMGateway → try OpenAI → if it fails, try Anthropic → ...
```

Because the gateway *pretends to be* a normal provider (it implements the same
interface), every existing piece of code got fallback and cost tracking with
**no changes at all**.

| What you get | How |
|---|---|
| Swap providers | Change one config line |
| Survive an outage | Falls through to the next provider |
| Know your spend | Priced in the one place that sees model *and* tokens |
| Enforce approved models | One place to check, not twenty |

**Supported:** OpenAI, Anthropic (Claude), Ollama (local), OpenRouter, Azure.

One class covers three of those, because Azure, OpenRouter and Ollama all
speak OpenAI's API shape — they differ only in URL and key. Writing a separate
class for each would be the same file copied three times.

**Two details that show care:**

- **Streaming only fails over before the first word.** Once text has started
  reaching the user, switching providers would splice two different answers
  together. So a mid-stream failure is reported rather than papered over.
- **Unknown models record no cost, rather than zero.** A silent zero would
  understate spend on exactly the models nobody has reviewed the price of yet
  — the opposite of what a cost control should do.

---

## 8. Part Five: Plugins, Workflows, Feature Flags

### Plugins — everything replaceable

The project had **four** hand-written registries (for AI providers, embedders,
OCR engines, rerankers). Each was a long `if/elif` chain you had to edit to add
anything, and each behaved slightly differently when something went wrong.
Adding tools would have made five.

We replaced them with one generic `PluginRegistry`:

```python
@tools.register("calculator", requires_flag="enable_calculator")
def build_calculator():
    return CalculatorTool()
```

An implementation now **declares itself** rather than being listed somewhere
else. That's what makes a plugin *folder* work — drop a file in, and it exists.

The `requires_flag` bit is enforced **inside** the registry, so a disabled
plugin can't be built by some code path that forgot to check.

### Workflows — pipelines as diagrams, not nested ifs

The main answer function had grown to hold the kill-switch check, cache
lookup, routing, retrieval, two policy checks and the streaming loop — with
the logic expressed as early returns tangled through it. Every new feature made
it longer, and none of it could be tested on its own.

A workflow expresses the same thing as **boxes and arrows**:

```python
workflow = (
    Workflow("answer")
    .add(CheckKillSwitchNode(), next_="cache")
    .add(CacheLookupNode(), next_=decide_what_next)
    .add(RetrieveNode(), next_="generate")
)
```

Each box is testable alone and traced alone. A box can also decide where to go
next — which is exactly what routing needs and what an `if/elif` chain can't
express cleanly.

There's a step limit, so a mis-wired arrow that loops back on itself produces a
clear error instead of a mysterious hang.

### Feature flags — switches with two personalities

```
Capability  → "is this feature on?"        → set per environment
Kill switch → "stop this, it's misbehaving" → flipped in seconds, always audited
```

How a value gets decided:

```
operator override (database)  →  environment default  →  code default
```

So staging can enable web search while production doesn't, *and* an operator
can override either during an incident.

Two small decisions that matter:

- A **typo'd flag name in code raises an error**. Silently reading as `False`
  would disable a feature and take a long time to find.
- But an **unknown flag in the database is ignored with a warning**. A stale
  row naming a deleted flag must never break flag loading — that would take
  out the kill switches at exactly the moment someone needs them.

---

## 9. Part Six: Authentication

### The gap this closed

The system had real *authorization* — clearance filtering, role checks, audit
attribution. All of it rested on an HTTP header (`X-User-Id`) that anyone
could type anything into.

> **The analogy:** a very good lock on a door, and the key is a sticky note
> saying "I am the manager".

This was tracked honestly in the risk register as **R-G03**, with residual
risk written as `HIGH AND ACCEPTED` — not quietly omitted.

### Why Firebase

Google/email sign-in, password reset, email verification and token issuing are
weeks of security-sensitive work to build and a permanent liability to
maintain. Handing that to Firebase shrinks our job to *verify a signed token* —
small and well-defined.

The lock-in worry is mild: all the coupling lives in one file. Swapping to
Auth0 or Cognito means one new class.

### How it flows

```
You sign in (browser or Streamlit)
        │
        ▼
Firebase gives you an ID token — a signed pass, valid ~1 hour
        │
        ▼
Every request sends it:  Authorization: Bearer <token>
        │
        ▼
Our backend checks the signature against Google's public keys
        │
        ▼
Finds or creates your user record
        │
        ▼
You become a "Principal": user id + role + clearance
        │
        ▼
That clearance decides which documents you can retrieve
```

We use Firebase's official library rather than checking the token by hand,
because it handles **key rotation** — the part hand-rolled implementations
reliably get wrong.

### Two decisions worth explaining

**New users get the lowest role.**

Signing in proves *who you are*. It says nothing about *what you should be
allowed to read*. Conflating the two is how "sign in with Google" quietly
becomes "anyone with a Google account is an analyst". Promotion is a separate,
audited action.

Also: an existing user's role is **never** overwritten by logging in. Otherwise
a promotion would silently vanish the next morning.

**You almost always want a domain allow-list.**

Without `FIREBASE_ALLOWED_DOMAINS`, "sign in with Google" means *literally
anyone on earth with a Google account*. Unverified emails are rejected for the
same reason — without verification anyone can claim any address, which makes
the domain list meaningless.

### About the two different Firebase keys

This confuses everybody, so it's worth being precise:

| | Web API Key | Service Account JSON |
|---|---|---|
| Used by | Browser / Streamlit | Backend only |
| Purpose | Ask Firebase to sign a user in | Verify tokens |
| Secret? | **No** | **Yes — extremely** |
| If leaked | Nothing much | Attacker can impersonate **any user** |

The Web API key is visible in any browser's network tab by design. The service
account signs tokens for every user in your project — treat it like a root
password. That's why `config/` is git-ignored wholesale, plus wildcards for
`*service-account*.json`, `*.pem` and `*.key`.

---

## 10. Part Seven: PII Redaction

### The problem

Your documents are real business documents. They contain names, emails, phone
numbers, sometimes card numbers or API keys. Every time the system retrieves a
passage, it sends that text to an AI provider — a third party.

The setting `pii_redaction_enabled` existed and **did nothing**. It was a flag
that looked like a feature.

### What we built

Nine detectors that find and mask personal data at **two** points:

| Where | When | Why there |
|---|---|---|
| Retrieved passages | Before building the prompt | Once text is in the prompt, it has already left your servers |
| Generated answers | Before showing the user | Catches data the model reconstructed or remembered |

Doing only the second would be too late. Doing only the first would miss what
the model recalls from earlier in the conversation.

### The detail that makes it usable

Detectors **validate**, they don't just pattern-match.

- A 16-digit number could be a credit card — or an order number. We run the
  **Luhn checksum** (the real card-number check) to tell them apart.
- `123-45-6789` looks like a US SSN — but so do plenty of reference codes. We
  check the structural rules that real SSNs follow.
- Phone numbers get length bounds, so invoice numbers aren't swept up.

Why bother? Because **over-redaction has a real cost.** If you black out a
passage the model needed, you get a worse answer. Redaction isn't free safety —
it's a trade, and the validators keep it honest.

### One small thing we were careful about

The system records **how many** items it redacted, per type. It never logs the
values.

Logging the personal data you just redacted would move the leak, not close it.

---

## 11. How it all fits together

Here's a full request, end to end:

```
 You type a question in the browser
        │
        ▼
 Streamlit attaches your Firebase token
        │
        ▼
 ┌──────────────────────────────────────────────────┐
 │ TraceContextMiddleware                           │
 │ gives this request a trace ID, binds it to logs  │
 └──────────────────┬───────────────────────────────┘
        │
        ▼
 ┌──────────────────────────────────────────────────┐
 │ resolve_principal                                │
 │ verifies the token → who you are → your clearance│
 └──────────────────┬───────────────────────────────┘
        │
        ▼
 ┌──────────────────────────────────────────────────┐
 │ Kill switch check — is answering even on?        │
 └──────────────────┬───────────────────────────────┘
        │
        ▼
 ┌──────────────────────────────────────────────────┐
 │ Semantic cache — has someone asked this already? │
 └──────────────────┬───────────────────────────────┘
        │ miss
        ▼
 ┌──────────────────────────────────────────────────┐
 │ QUERY ROUTER — how should this be answered?      │
 └──────────────────┬───────────────────────────────┘
        │
        ├── greeting/maths/date → answer directly, 0 AI calls ──┐
        │                                                        │
        └── needs documents                                      │
                 │                                               │
                 ▼                                               │
        ┌────────────────────────────────────────┐               │
        │ Search, filtered by YOUR clearance      │               │
        │ (applied inside the database query)     │               │
        └────────────────┬───────────────────────┘               │
                 │                                               │
                 ▼                                               │
        ┌────────────────────────────────────────┐               │
        │ Second clearance check (defence in depth)│              │
        └────────────────┬───────────────────────┘               │
                 │                                               │
                 ▼                                               │
        ┌────────────────────────────────────────┐               │
        │ PII redaction on retrieved passages     │               │
        └────────────────┬───────────────────────┘               │
                 │                                               │
                 ▼                                               │
        ┌────────────────────────────────────────┐               │
        │ AI Gateway → generate the answer        │               │
        └────────────────┬───────────────────────┘               │
                 │                                               │
                 ▼                                               │
        ┌────────────────────────────────────────┐               │
        │ Grounding check — did it cite anything? │               │
        └────────────────┬───────────────────────┘               │
                 │                                               │
                 ▼                                               │
        ┌────────────────────────────────────────┐               │
        │ PII redaction on the answer             │               │
        └────────────────┬───────────────────────┘               │
                 │                                               │
                 ├───────────────────────────────────────────────┘
                 ▼
        ┌────────────────────────────────────────┐
        │ Save both messages + audit + sample for │
        │ quality scoring                          │
        └────────────────┬───────────────────────┘
                 ▼
             Answer to you
```

### Where things live

```
src/
├── routing/       ← the query router (rules, classifier)
├── tools/         ← calculator, web search, SQL, etc.
├── workflow/      ← the node-graph engine
├── plugins/       ← the generic registry everything uses
├── auth/          ← Firebase verification
├── governance/    ← policy, RBAC, audit, PII, flags, retention
├── llm/           ← the AI gateway + providers + pricing
├── monitoring/    ← logs, metrics, tracing
├── evaluation/    ← offline (test set) + online (real traffic)
├── retrieval/     ← the original RAG pipeline
└── ingestion/     ← the original document processing
```

---

## 12. The bugs we found along the way

These make good stories, because each one was invisible until something
specifically looked for it.

**The governance checker found four problems on its very first run** —
including `rag_active_users`, a metric that was declared but never written to.
Any dashboard using it would show a confident, permanent zero. We deleted it
rather than fake it.

**A test caught a real API design flaw.** Our plugin registry's `create(name,
**kwargs)` broke for any plugin whose factory had a parameter called `name` —
a perfectly reasonable thing to want. Fixed with a positional-only marker.

**The Docker build would have failed.** We'd added `firebase-admin` to the
dependency list but not regenerated the lock file, and the Dockerfile uses
`--locked`, which refuses stale locks.

**The risk register was excluded from the Docker image.** It lives in `docs/`,
and `.dockerignore` excludes `docs/`. But that file isn't documentation — the
API *reads it at runtime*. Classic case of a file being in the wrong
conceptual box.

**The Firebase credential never reached the container.** Not copied, not
mounted. It would have failed with a message looking like a client problem.
We mounted it read-only rather than copying it in, because a secret baked into
an image layer lives there forever and `docker history` will show it to anyone
who can pull the image.

**The database migration trap.** The API crash-looped with
`column "email_verified" does not exist`. The `users` table already existed, and
`create_all()` only creates **missing tables** — it never alters existing ones.
This exact trap is documented in an earlier migration's comments. The lesson:
after any schema change, run migrations *before* starting.

**The stale token puzzle** — the most interesting one. After signing in,
everything returned 403. The metrics said: 23 successful token verifications,
23 rejections for unverified email. But the user *had* verified.

The cause: **a Firebase token is a snapshot.** The token was issued before the
verification link was clicked. Verifying updated the account, but the token in
hand still said "unverified" for its full hour — and the backend trusts the
token (correctly; checking live on every request would mean calling Google
constantly).

Fixed on both sides: the UI now re-mints the token when it spots the mismatch,
and the backend does one live check *only when it's about to refuse someone* —
the moment when being right actually matters.

---

## 13. How to explain this to someone else

If you have **30 seconds**:

> "We made a RAG system trustworthy and efficient. It now knows who's asking,
> only shows people documents they're allowed to see, doesn't run an expensive
> search when someone just says hello, strips personal data before sending
> anything to OpenAI, and keeps a record of everything it did."

If you have **2 minutes**, add:

> "The interesting part is that the governance isn't a document — it's
> enforced. There's a YAML file listing 20 risks, each naming the metric that
> watches it and the code file that mitigates it. A CI script fails the build
> if any of those stops being true. So the safety documentation physically
> can't drift away from the code."

If you have **10 minutes**, walk through three things:

1. **The router** — show the diagram in section 5. "Most questions don't need
   a document search. We check first."
2. **The two-layer clearance check** — section 4, Map. "One filter in the
   database, one in the code. If the second one ever catches something, that's
   a bug and it alerts."
3. **The stale-token bug** — section 12. It shows real debugging: metrics
   pointed at the exact line, and the fix went in on both sides.

### Questions people will ask

**"Isn't the router just a chatbot intent classifier?"**
Partly — but the important bit is the *fallback direction*. When it's unsure,
it does the expensive safe thing, because a wrong-source answer is worse than
a wasted search.

**"Why not just use `eval()` for the calculator?"**
Because `__import__("os").system(...)` is a valid Python expression, and the
input comes from AI output. That's remote code execution.

**"Why two clearance checks? Isn't one enough?"**
The first can be incomplete — old documents have no label, and the two search
engines handle missing fields differently. The second catches that, and
anything it catches raises an alert because it means the first one failed.

**"Is the Firebase key in the repo a security problem?"**
There are two keys. The Web API key is public by design. The service account
is git-ignored and never committed — that one signs tokens for every user.

**"What's still not done?"**
Conversations aren't clearance-filtered. Unstructured PII (a name written in a
sentence) isn't detected — regex can't do that. Rate limiting is per-process
fixed-window, so a burst that lands exactly on a window boundary can briefly
get through twice the budget. All of it is written down in the risk register
rather than left as a pleasant surprise.

---

## Last round: making it usable, not just correct

The security and governance work was invisible from the screen. This round was
about the gap between "the system does the right thing" and "a person can tell
that it did".

**The empty chat used to be a blank box.** A first-time user had no way to know
what the thing knows or what shape of question it wants. It now opens with a
short explanation and four example prompts — one document question, one sum,
one clock question — which double as a demonstration that not every question
triggers a search.

**Finding a document meant scrolling.** The list was paged but had no search,
and a filter written in the page could only ever search *the page you were
looking at* — so a document on page 3 was effectively invisible. Search now
happens in the database, before the count and before the page is cut, which is
the same rule the clearance filter follows and for the same reason: a total
computed after filtering is the only total that doesn't lie.

That search needed one careful detail. `%` and `_` are wildcards in SQL's
`LIKE`, so a user typing `%` would otherwise match every document. They're
escaped — and escaping needs the `escape=` argument, because without it
Postgres has no escape character and the backslashes are matched literally, so
the escaping silently does nothing at all. There's an integration test that
searches for `%` and asserts it finds *nothing*.

**You couldn't see what was loaded.** The plugin registries and the AI gateway
chain were readable over the API but had no screen. A registry filled by import
side effects can quietly lose an entry when a module fails to import, and that
shows up as "the router never picks that tool" rather than as a missing plugin.
The Governance console now has a **System** tab listing every tool, LLM
provider and PII detector, plus the provider fallback chain in force — with a
warning when a chain has only one link, because that's an outage away from
failing rather than degrading.

---

## Further reading

| Document | What's in it |
|---|---|
| [governance/GM3_FRAMEWORK.md](governance/GM3_FRAMEWORK.md) | Every control mapped to its NIST function |
| [governance/risk_register.yaml](governance/risk_register.yaml) | The 20 risks, machine-checked |
| [governance/AUTHENTICATION.md](governance/AUTHENTICATION.md) | Firebase setup, start to finish |
| [governance/model_card.md](governance/model_card.md) | What the system is, and its limits |
| [governance/data_card.md](governance/data_card.md) | What data it holds and for how long |
| [architecture/13_query_routing_and_tools.md](architecture/13_query_routing_and_tools.md) | Router, tools, gateway in technical depth |
| [../config/README.md](../config/README.md) | Where secrets go and what to do if one leaks |

---

*If something here doesn't make sense, that's a fault in the document, not in
you. The whole point is that it should be explainable.*
