#!/usr/bin/env python
"""Full end-to-end test against the running stack -- with almost no LLM spend.

    python scripts/integration_test.py

Deliberately exercises the *whole* system while keeping model calls near zero.
That is possible because of the query router: greetings, arithmetic and
date questions are answered with no model call at all, so the entire request
path -- auth, RBAC, clearance, routing, tools, caching, persistence, audit,
metrics -- can be driven for free.

Authentication is tested against real Firebase. Rather than needing a human
password, this mints a test user with the Admin SDK (inside the API
container, which holds the service account) and exchanges a custom token for
a real ID token. That means the auth path under test is the production one,
not a bypass.

Add --with-llm to additionally run one document ingestion + one RAG answer,
which does cost a small number of API calls.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
import uuid

BASE = "http://localhost:8000/api/v1"
results: list[tuple[str, str, str]] = []
_SECTION = ""


def section(name: str) -> None:
    global _SECTION
    _SECTION = name
    print(f"\n{'-' * 72}\n{name}\n{'-' * 72}")


def record(name: str, ok: bool | None, detail: str = "") -> None:
    status = "PASS" if ok is True else ("FAIL" if ok is False else "SKIP")
    results.append((f"{_SECTION} / {name}", status, detail))
    icon = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[status]
    print(f"[{icon}] {name}" + (f"  -- {detail}" if detail else ""))


def dc(*args: str, timeout: int = 180) -> tuple[int, str]:
    """Run a docker compose command."""
    proc = subprocess.run(
        ["docker", "compose", *args], capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def in_api(code: str, timeout: int = 180) -> tuple[int, str]:
    return dc("exec", "-T", "api", "python", "-c", code, timeout=timeout)


# ---------------------------------------------------------- auth bootstrap


def mint_test_token(email: str, role: str) -> str | None:
    """Create a verified Firebase user and return a real ID token.

    Uses the Admin SDK for creation (server-side, no password needed), then
    the public REST endpoint to exchange a custom token for an ID token --
    which is exactly what a browser does, so the token is indistinguishable
    from a real sign-in.
    """
    code = f"""
import json, os, requests
import firebase_admin
from firebase_admin import auth
from src.auth.firebase import get_verifier

app = get_verifier()._ensure_app()
email = {email!r}
try:
    user = auth.get_user_by_email(email, app=app)
except Exception:
    user = auth.create_user(email=email, password="Test!{uuid.uuid4().hex[:12]}",
                            email_verified=True, app=app)
auth.update_user(user.uid, email_verified=True, app=app)

custom = auth.create_custom_token(user.uid, app=app).decode()
key = os.environ["FIREBASE_WEB_API_KEY"]
r = requests.post(
    f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={{key}}",
    json={{"token": custom, "returnSecureToken": True}}, timeout=20)
print("TOKEN:" + r.json().get("idToken", "") + "|UID:" + user.uid)
"""
    rc, out = in_api(code)
    for line in out.splitlines():
        if line.startswith("TOKEN:"):
            token, _, uid = line[6:].partition("|UID:")
            if token:
                # Order matters. The local `users` row does not exist until the
                # API first sees this token and provisions it, so setting the
                # role beforehand updates zero rows and the account silently
                # stays a viewer. Make one authenticated call first.
                api("GET", "/documents", token=token)
                if not _set_role(email, role):
                    print(f"  (warning: role update matched no row for {email})")
                return token
    print(f"  (token mint failed: {out[-300:]})")
    return None


def _set_role(email: str, role: str) -> bool:
    """Set the role, returning whether a row was actually updated."""
    rc, out = dc(
        "exec", "-T", "postgres", "psql", "-U", "raguser", "-d", "ragdb", "-tAc",
        f"UPDATE users SET role='{role}' WHERE email='{email}';",
    )
    return "UPDATE 1" in out


def api(method: str, path: str, token: str | None = None, **kwargs):
    import requests

    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(
        method, f"{BASE}{path}", headers=headers, timeout=kwargs.pop("timeout", 90), **kwargs
    )


def chat(query: str, token: str) -> dict:
    """Non-streaming chat, so the full done-event is returned as JSON."""
    r = api("POST", "/chat", token=token, json={"query": query, "stream": False})
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------- the tests


def test_infrastructure() -> None:
    section("1. INFRASTRUCTURE")
    try:
        health = api("GET", "/health", timeout=15).json()
    except Exception as exc:
        record("API reachable", False, str(exc))
        return
    record("API reachable", health.get("status") == "healthy", f"status={health.get('status')}")
    for dep, state in health.get("checks", {}).items():
        record(f"dependency {dep}", state == "ok", state[:50])

    import requests

    for name, url in [
        ("Prometheus", "http://localhost:9090/-/healthy"),
        ("Grafana", "http://localhost:3000/api/health"),
    ]:
        try:
            record(name, requests.get(url, timeout=8).status_code == 200)
        except Exception as exc:
            record(name, False, str(exc)[:60])

    try:
        targets = requests.get(
            "http://localhost:9090/api/v1/targets", timeout=8
        ).json()["data"]["activeTargets"]
        up = [t for t in targets if t["health"] == "up"]
        record("Prometheus scraping API", any("rag-api" in t["labels"].get("job", "") for t in up),
               f"{len(up)}/{len(targets)} targets up")
    except Exception as exc:
        record("Prometheus scraping API", False, str(exc)[:60])


def test_auth(viewer: str | None, admin: str | None) -> None:
    section("2. AUTHENTICATION  (threat: impersonation)")

    r = api("GET", "/documents")
    record("no token -> 401", r.status_code == 401, f"got {r.status_code}")

    r = api("GET", "/documents", token="not-a-real-token")
    record("garbage token -> 401", r.status_code == 401, f"got {r.status_code}")

    r = api("GET", "/documents", token="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoYWNrZXIifQ.x")
    record("forged JWT -> 401", r.status_code == 401, f"got {r.status_code}")

    if viewer:
        r = api("GET", "/documents", token=viewer)
        record("valid token -> 200", r.status_code == 200, f"got {r.status_code}")
    else:
        record("valid token -> 200", None, "no token minted")

    if admin:
        r = api("GET", "/governance/policy", token=admin)
        principal = r.json().get("principal", {}) if r.status_code == 200 else {}
        record("principal resolved", principal.get("authenticated") is True,
               f"role={principal.get('role')} clearance={principal.get('clearance')}")
        record("auth provider recorded", principal.get("auth_provider") == "firebase",
               principal.get("auth_provider", "?"))


def test_rbac(viewer: str | None, admin: str | None) -> None:
    section("3. AUTHORIZATION  (threat: privilege escalation)")
    if not (viewer and admin):
        record("RBAC", None, "tokens unavailable")
        return

    # A viewer must be refused every privileged route.
    for label, method, path in [
        ("audit log", "GET", "/governance/audit"),
        ("admin settings", "GET", "/admin/settings"),
        ("retention run", "POST", "/governance/retention/run?dry_run=true"),
        ("kill switch", "PUT", "/governance/flags/answering_enabled"),
    ]:
        kwargs = {"json": {"enabled": True}} if method == "PUT" else {}
        r = api(method, path, token=viewer, **kwargs)
        record(f"viewer denied: {label}", r.status_code == 403, f"got {r.status_code}")

    # ...and an admin allowed.
    r = api("GET", "/governance/audit", token=admin)
    record("admin allowed: audit log", r.status_code == 200,
           f"{len(r.json()) if r.status_code == 200 else '?'} entries")

    r = api("GET", "/admin/settings", token=admin)
    record("admin allowed: settings", r.status_code == 200)


def test_router(token: str | None) -> None:
    section("4. QUERY ROUTER  (zero LLM calls)")
    if not token:
        record("router", None, "no token")
        return

    # Arithmetic operands are randomised per run. A fixed expression is
    # legitimately served from the semantic cache on the second run
    # (route="cached"), which would look like a routing failure while actually
    # being the cache doing its job. Fresh numbers exercise the calculator
    # every time.
    a, b = random.randint(21, 99), random.randint(21, 99)
    c, d = random.randint(101, 899), random.randint(101, 899)

    cases = [
        ("hi", "greeting", None),
        ("thanks", "greeting", None),
        (f"{a} * {b}", "calculator", str(a * b)),
        (f"what is {c} + {d}?", "calculator", str(c + d)),
        (f"sqrt({a * a})", "calculator", str(a)),
        ("what time is it", "datetime", None),
    ]
    for query, expected_route, expected_in_answer in cases:
        try:
            body = chat(query, token)
        except Exception as exc:
            record(f"{query!r}", False, str(exc)[:70])
            continue

        route_ok = body.get("route") == expected_route
        detail = f"route={body.get('route')} grounded={body.get('grounded')}"
        if expected_in_answer and expected_in_answer not in body.get("answer", ""):
            record(f"{query!r}", False, f"{detail} answer={body.get('answer','')[:40]!r}")
        else:
            record(f"{query!r}", route_ok, detail)

    body = chat("hi", token)
    record("greeting marked ungrounded", body.get("grounded") is False,
           "grounded=False as expected -- no documents were consulted")
    record("trace id returned", bool(body.get("trace_id")), body.get("trace_id", "")[:16])


def test_calculator_security() -> None:
    section("5. INPUT SAFETY  (threat: code execution)")
    payloads = [
        "__import__('os').system('id')",
        "open('/app/config/firebase-service-account.json').read()",
        "().__class__.__bases__[0].__subclasses__()",
        "exec('x=1')",
        "eval('1+1')",
        "2 ** 10000000",
        "factorial(999999)",
    ]
    code = (
        "import json;from src.tools.builtin.calculator import evaluate, CalculatorError;"
        "p=" + json.dumps(payloads) + ";"
        "out={};\n"
        "for x in p:\n"
        "    try:\n"
        "        evaluate(x); out[x]='EXECUTED'\n"
        "    except CalculatorError: out[x]='blocked'\n"
        "    except Exception as e: out[x]='blocked('+type(e).__name__+')'\n"
        "print('RESULT'+json.dumps(out))"
    )
    rc, out = in_api(code)
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT")), "")
    if not line:
        record("exploit payloads", False, out[-150:])
        return
    verdicts = json.loads(line[6:])
    executed = [k for k, v in verdicts.items() if v == "EXECUTED"]
    record("all exploit payloads blocked", not executed,
           f"{len(verdicts)} payloads, {len(executed)} executed" + (f" {executed}" if executed else ""))

    rc, out = in_api(
        "from src.tools.builtin.calculator import evaluate;print('OK', evaluate('2+2'), evaluate('sqrt(16)'))"
    )
    record("legitimate maths still works", "OK 4 4.0" in out, out.splitlines()[-1][:50] if out else "")


def test_sql_validation() -> None:
    section("6. SQL TOOL  (threat: injection)")
    code = (
        "import json;from src.tools.builtin.sql_query import _validate;"
        "from src.tools.base import ToolError;"
        "cases=['DROP TABLE users','DELETE FROM documents','SELECT 1 FROM documents; DROP TABLE x',"
        "'UPDATE documents SET x=1','SELECT * FROM users'];"
        "out={};\n"
        "for c in cases:\n"
        "    try:\n"
        "        _validate(c, ['documents'], 10); out[c]='ALLOWED'\n"
        "    except ToolError: out[c]='blocked'\n"
        "ok=_validate('SELECT id FROM documents', ['documents'], 10);"
        "print('RESULT'+json.dumps({'blocked':out,'valid':ok}))"
    )
    rc, out = in_api(code)
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT")), "")
    if not line:
        record("sql validation", False, out[-150:])
        return
    data = json.loads(line[6:])
    allowed = [k for k, v in data["blocked"].items() if v == "ALLOWED"]
    record("dangerous SQL blocked", not allowed, f"{len(data['blocked'])} cases" + (f", LEAKED: {allowed}" if allowed else ""))
    record("valid SELECT gets a LIMIT", "LIMIT" in data["valid"], data["valid"])


def test_pii() -> None:
    section("7. PII REDACTION  (threat: data egress)")
    sample = (
        "Contact jane.doe@acme.com or call 555-123-4567. "
        "Card 4111 1111 1111 1111. Key sk-abcdefghijklmnopqrstuvwxyz123456. "
        "Order 1234567812345678 shipped."
    )
    code = (
        "import json;from src.governance.pii import get_redactor;"
        f"r=get_redactor().redact({sample!r}, surface='test');"
        "print('RESULT'+json.dumps({'text':r.text,'counts':r.counts}))"
    )
    rc, out = in_api(code)
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT")), "")
    if not line:
        record("pii redaction", False, out[-150:])
        return
    data = json.loads(line[6:])
    text, counts = data["text"], data["counts"]

    record("email masked", "jane.doe@acme.com" not in text)
    record("credit card masked", "4111 1111 1111 1111" not in text)
    record("API key masked", "sk-abcdefghijklmnopqrstuvwxyz123456" not in text)
    record("phone masked", "555-123-4567" not in text)
    # The discriminating test: a 16-digit order number fails Luhn, so a naive
    # detector would mask it and degrade the answer.
    record("order number NOT masked (Luhn works)", "1234567812345678" in text,
           "correctly left alone")
    record("detectors fired", len(counts) >= 4, str(counts))


def test_flags_and_killswitch(admin: str | None, viewer: str | None) -> None:
    section("8. FEATURE FLAGS & KILL SWITCH  (manage)")
    if not (admin and viewer):
        record("flags", None, "tokens unavailable")
        return

    r = api("GET", "/governance/flags", token=admin)
    flags = r.json().get("flags", [])
    record("flags readable", isinstance(flags, list) and len(flags) >= 10, f"{len(flags)} flags")
    record("flags carry scope+source", all("scope" in f and "source" in f for f in flags))

    # Flip the master kill switch, prove it takes effect, restore it.
    api("PUT", "/governance/flags/answering_enabled", token=admin,
        json={"enabled": False, "reason": "integration test"})
    time.sleep(12)  # cache TTL is 10s
    try:
        body = chat("hi", viewer)
        record("kill switch stops answering", body.get("refused") is True,
               f"refused={body.get('refused')} reason={body.get('refusal_reason')}")
    except Exception as exc:
        record("kill switch stops answering", False, str(exc)[:60])

    api("PUT", "/governance/flags/answering_enabled", token=admin,
        json={"enabled": True, "reason": "integration test restore"})
    time.sleep(12)
    body = chat("hi", viewer)
    record("kill switch restored", body.get("refused") is not True, "answering again")


def test_audit(admin: str | None) -> None:
    section("9. AUDIT TRAIL  (threat: repudiation)")
    if not admin:
        record("audit", None, "no token")
        return

    payload = api("GET", "/governance/audit", token=admin, params={"hours": 1, "limit": 200}).json()
    if not isinstance(payload, list):
        # A 403/500 returns {"detail": ...}; iterating that yields strings and
        # crashes the run several lines later, hiding the real cause.
        record("audit readable", False, f"unexpected payload: {str(payload)[:90]}")
        return
    entries = payload
    record("entries written", len(entries) > 0, f"{len(entries)} in the last hour")

    actions = {e["action"] for e in entries}
    record("kill-switch flips recorded", "kill_switch_toggled" in actions, str(sorted(actions))[:90])

    with_trace = [e for e in entries if e.get("trace_id")]
    record("entries carry trace ids", len(with_trace) > 0,
           f"{len(with_trace)}/{len(entries)} -- links audit to logs and traces")

    toggles = [e for e in entries if e["action"] == "kill_switch_toggled"]
    if toggles:
        record("before/after captured", bool(toggles[0].get("actor_role")),
               f"actor_role={toggles[0].get('actor_role')}")


def test_governance_endpoints(admin: str | None) -> None:
    section("10. GOVERNANCE SURFACE")
    status = api("GET", "/governance/status").json()
    record("status", status.get("enforcement_mode") == "enforce",
           f"policy v{status.get('policy_version')} / {sum(status.get('risks_by_function',{}).values())} risks / {status.get('controls')} controls")

    risks = api("GET", "/governance/risks").json()
    record("risk register served", risks.get("count", 0) >= 18, f"{risks.get('count')} risks")
    every_bound = all(r["metric"] and r["controls"] for r in risks.get("risks", []))
    record("every risk has metric + controls", every_bound)

    plugins = api("GET", "/governance/plugins").json()
    record("tools registered", len(plugins.get("tools", [])) >= 5,
           ", ".join(t["name"] for t in plugins.get("tools", [])))
    record("llm providers registered", len(plugins.get("llm_providers", [])) >= 5)
    record("pii detectors registered", len(plugins.get("pii_detectors", [])) >= 9)

    if admin:
        gw = api("GET", "/governance/gateway", token=admin).json()
        record("gateway describes its chain", "small" in gw,
               str(gw.get("small", {}).get("chain", ""))[:60])

        retention = api("POST", "/governance/retention/run?dry_run=true", token=admin).json()
        record("retention dry run", retention.get("dry_run") is True,
               f"scanned={retention.get('scanned')} purged={retention.get('purged')}")


def test_web_search() -> None:
    section("11. WEB SEARCH TOOL  (external, no LLM)")
    code = (
        "import asyncio, json, logging; logging.disable(logging.CRITICAL);"
        "from src.governance import feature_flags;"
        "from src.tools.registry import get_tool;"
        "from src.tools.base import ToolRequest;"
        "en=feature_flags.is_enabled('enable_web_search');"
        "r=asyncio.run(get_tool('web_search').run(ToolRequest(query='what is vector search'))) if en else None;"
        "print('RESULT'+json.dumps({'enabled':en,'ok':bool(r and r.succeeded),"
        "'n':len((r.data or {}).get('results',[])) if r else 0,'err':(r.error if r else '')}))"
    )
    rc, out = in_api(code, timeout=120)
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT")), "")
    if not line:
        record("web search", False, out[-150:])
        return
    d = json.loads(line[6:])
    if not d["enabled"]:
        record("web search", None, "disabled by flag")
    else:
        record("live Tavily search", d["ok"], f"{d['n']} results" if d["ok"] else d["err"][:70])


def test_metrics() -> None:
    section("12. METRICS")
    import requests

    body = requests.get(f"{BASE}/metrics", timeout=15).text
    for metric in [
        "rag_router_decisions_total",
        "rag_retrieval_skipped_total",
        "rag_tool_invocations_total",
        "rag_answers_total",
        "rag_auth_verifications_total",
        "rag_access_denied_total",
        "rag_audit_events_total",
        "rag_feature_flag_state",
        "rag_kill_switch_state",
    ]:
        present = any(
            ln.startswith(metric) and not ln.startswith("#") for ln in body.splitlines()
        )
        record(metric, present)

    routed = [ln for ln in body.splitlines() if ln.startswith("rag_router_decisions_total{")]
    record("router decisions recorded", bool(routed), f"{len(routed)} series")
    skipped = [ln for ln in body.splitlines() if ln.startswith("rag_retrieval_skipped_total{")]
    record("retrieval actually skipped", bool(skipped),
           "; ".join(s.split("}")[0].split("{")[1] + " = " + s.split()[-1] for s in skipped[:4]))


def test_rag(token: str | None, enabled: bool) -> None:
    section("13. FULL RAG PATH  (uses LLM + embeddings)")
    if not enabled:
        record("document ingest + RAG answer", None, "skipped -- pass --with-llm to run")
        return
    if not token:
        record("rag", None, "no token")
        return

    import requests

    # A unique window per run, so the question has never been asked before.
    # With a fixed question the semantic cache answers it on the second run and
    # the RAG path is never actually exercised -- the test would pass while
    # only testing the cache.
    window = random.randint(40, 90)
    content = (
        "ACME Refund Policy\n\n"
        "Customers may request a refund within 30 days of purchase. "
        "Refunds for defective items are processed within 5 business days. "
        f"Enterprise customers in the EU have a {window} day refund window.\n"
    )
    files = {"file": (f"refund_policy_{window}.txt", content.encode(), "text/plain")}
    r = requests.post(
        f"{BASE}/documents", files=files,
        data={"domain": "Operations", "sensitivity": "public"},
        headers={"Authorization": f"Bearer {token}"}, timeout=120,
    )
    if r.status_code not in (200, 202):
        record("upload", False, f"{r.status_code} {r.text[:100]}")
        return
    doc_id = r.json()["document_id"]
    record("upload accepted", True, f"doc={doc_id[:8]}")

    for _ in range(60):
        time.sleep(3)
        status = api("GET", f"/documents/{doc_id}", token=token).json().get("status")
        if status in ("indexed", "failed"):
            break
    record("ingestion completed", status == "indexed", f"status={status}")
    if status != "indexed":
        return

    body = chat(
        f"What is the exact refund window in days for EU enterprise customers "
        f"as of policy revision {window}?",
        token,
    )
    record("RAG route chosen", body.get("route") == "rag", f"route={body.get('route')}")
    record("answer is grounded", body.get("grounded") is True)
    record("answer has citations", len(body.get("citations", [])) > 0,
           f"{len(body.get('citations', []))} citations")
    record(f"answer states {window} days", str(window) in body.get("answer", ""),
           body.get("answer", "")[:90])



def test_p0_feedback(token: str | None) -> None:
    section("14. FEEDBACK LOOP  (P0)")
    if not token:
        record("feedback", None, "no token")
        return

    body = chat("hi", token)
    message_id = body.get("message_id")
    record("chat returns message_id", bool(message_id),
           "feedback cannot attach to an answer without it")
    if not message_id:
        return

    r = api("POST", "/feedback", token=token,
            json={"message_id": message_id, "rating": 1, "tags": ["incomplete"]})
    record("negative feedback accepted", r.status_code == 201, f"got {r.status_code}")

    r = api("POST", "/feedback", token=token,
            json={"message_id": message_id, "rating": 5})
    record("positive feedback accepted", r.status_code == 201, f"got {r.status_code}")

    r = api("POST", "/feedback", token=token,
            json={"message_id": message_id, "rating": 3, "tags": ["not-a-real-tag"]})
    ignored = r.json().get("ignored_tags", []) if r.status_code == 201 else []
    record("unknown tags rejected", "not-a-real-tag" in ignored,
           "closed vocabulary keeps metric cardinality bounded")

    summary = api("GET", "/feedback/summary", token=token).json()
    record("feedback summary counts it", summary.get("total", 0) > 0,
           f"total={summary.get('total')} negative={summary.get('negative')}")


def test_p0_classification(token: str | None) -> None:
    section("15. UPLOAD CLASSIFICATION  (P0)")
    if not token:
        record("classification", None, "no token")
        return

    import requests

    files = {"file": ("public_note.txt", b"A public note for classification testing.", "text/plain")}
    r = requests.post(f"{BASE}/documents", files=files,
                      data={"domain": "General", "sensitivity": "public"},
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 202:
        record("upload with sensitivity", False, f"{r.status_code} {r.text[:80]}")
        return
    doc_id = r.json()["document_id"]
    record("upload accepts sensitivity", True, "sensitivity=public")

    doc = api("GET", f"/documents/{doc_id}", token=token).json()
    record("classification persisted", doc.get("sensitivity") == "public",
           f"stored as {doc.get('sensitivity')}")
    record("retention deadline set", bool(doc.get("retention_until")),
           str(doc.get("retention_until"))[:10])

    listing = api("GET", "/documents", token=token).json()
    has_sens = all("sensitivity" in d for d in listing.get("items", []))
    record("listing exposes sensitivity", has_sens, "UI can show who may read each doc")

    # Above own clearance must be refused (this token is admin, so use the
    # viewer path instead if available).
    api("DELETE", f"/documents/{doc_id}", token=token)


def test_p0_conversations(token: str | None) -> None:
    section("16. CONVERSATION HISTORY  (P0)")
    if not token:
        record("conversations", None, "no token")
        return

    body = chat("hi", token)
    conversation_id = body.get("conversation_id")
    record("chat returns conversation_id", bool(conversation_id))

    convos = api("GET", "/conversations", token=token).json()
    record("history lists conversations", isinstance(convos, list) and len(convos) > 0,
           f"{len(convos) if isinstance(convos, list) else '?'} conversations")

    if isinstance(convos, list) and convos:
        first = convos[0]
        record("summary has title + count", bool(first.get("title")) and first.get("message_count", 0) > 0,
               f"{first.get('title','')[:30]!r} ({first.get('message_count')} msgs)")

        msgs = api("GET", f"/conversations/{first['id']}", token=token).json()
        record("messages readable", isinstance(msgs, list) and len(msgs) >= 2,
               f"{len(msgs) if isinstance(msgs, list) else '?'} messages")
        if isinstance(msgs, list) and msgs:
            roles = {m["role"] for m in msgs}
            record("both turns persisted", {"user", "assistant"} <= roles, str(sorted(roles)))

    # Another user's conversation must be invisible.
    r = api("GET", f"/conversations/{uuid.uuid4()}", token=token)
    record("unknown conversation -> 404", r.status_code == 404, f"got {r.status_code}")


def test_p0_conversation_isolation(viewer: str | None, admin: str | None) -> None:
    section("17. CONVERSATION ISOLATION  (P0 security)")
    if not (viewer and admin):
        record("isolation", None, "tokens unavailable")
        return

    chat("hi", admin)
    admin_convos = api("GET", "/conversations", token=admin).json()
    if not admin_convos:
        record("isolation", None, "admin has no conversations")
        return

    admin_convo_id = admin_convos[0]["id"]
    r = api("GET", f"/conversations/{admin_convo_id}", token=viewer)
    record("viewer cannot read admin's conversation", r.status_code == 404,
           f"got {r.status_code} -- 404 not 403, so existence is not confirmed")

    # limit=200 so the comparison is against the viewer's *whole* history.
    # At the default page size of 50 the admin conversation could be absent
    # merely through truncation, and the test would pass for the wrong reason.
    viewer_convos = api("GET", "/conversations", token=viewer,
                        params={"limit": 200}).json()
    viewer_ids = {c["id"] for c in viewer_convos} if isinstance(viewer_convos, list) else set()
    record("history is scoped per user", admin_convo_id not in viewer_ids,
           f"viewer sees {len(viewer_ids)} of their own")


def test_p1_rate_limit(token: str | None) -> None:
    section("18. RATE LIMITING  (P1 security)")
    if not token:
        record("rate limit", None, "no token")
        return

    # Greetings cost zero model calls, so the limit can be exercised for free.
    r = api("POST", "/chat", token=token, json={"query": "hi", "stream": False})
    record("limit headers on a normal response", "X-RateLimit-Limit" in r.headers,
           f"limit={r.headers.get('X-RateLimit-Limit')} "
           f"remaining={r.headers.get('X-RateLimit-Remaining')} "
           "-- a client can slow down before being refused")

    limit = int(r.headers.get("X-RateLimit-Limit", "30"))
    statuses = [r.status_code]
    for _ in range(limit + 3):
        statuses.append(
            api("POST", "/chat", token=token, json={"query": "hi", "stream": False}).status_code
        )

    record("excess requests are refused", 429 in statuses,
           f"{statuses.count(200)} allowed, {statuses.count(429)} refused (limit {limit}/min)")

    refused = api("POST", "/chat", token=token, json={"query": "hi", "stream": False})
    if refused.status_code == 429:
        record("429 carries Retry-After", "Retry-After" in refused.headers,
               f"retry in {refused.headers.get('Retry-After')}s")
    else:
        record("429 carries Retry-After", None, "window rolled over")

    # A different bucket must be unaffected by an exhausted chat budget.
    listing = api("GET", "/documents", token=token)
    record("other routes unaffected", listing.status_code == 200,
           "buckets are independent")


def test_p1_question_redaction(token: str | None) -> None:
    section("19. QUESTION REDACTION  (P1 egress)")
    if not token:
        record("question redaction", None, "no token")
        return

    secret = "sk-abcdefghijklmnopqrstuvwxyz987654"
    body = chat(f"is {secret} still a valid key?", token)
    conversation_id = body.get("conversation_id")

    msgs = api("GET", f"/conversations/{conversation_id}", token=token).json()
    if not isinstance(msgs, list) or not msgs:
        record("stored question inspectable", False, str(msgs)[:80])
        return

    user_turns = [m for m in msgs if m["role"] == "user"]
    stored = user_turns[-1]["content"] if user_turns else ""

    record("secret not stored in clear text", secret not in stored,
           "the raw key never reaches the messages table")
    record("redaction placeholder present", "SECRET" in stored, stored[:60])

    # Contact details stay searchable -- the question is also the search key.
    body = chat("what did jane.doe@acme.com agree to?", token)
    msgs = api("GET", f"/conversations/{body.get('conversation_id')}", token=token).json()
    user_turns = [m for m in msgs if m["role"] == "user"] if isinstance(msgs, list) else []
    stored = user_turns[-1]["content"] if user_turns else ""
    record("searchable email preserved", "jane.doe@acme.com" in stored,
           "masking it would silently break the search")


def test_p2_document_search(token: str | None) -> None:
    section("20. DOCUMENT SEARCH  (P2)")
    if not token:
        record("document search", None, "no token")
        return

    import requests

    marker = "zzsearchprobe"
    uploaded = []
    for name in (f"{marker}_alpha.txt", f"{marker}_beta.txt", "unrelated_gamma.txt"):
        r = requests.post(
            f"{BASE}/documents",
            files={"file": (name, b"search probe body", "text/plain")},
            data={"domain": "General", "sensitivity": "public"},
            headers={"Authorization": f"Bearer {token}"}, timeout=60,
        )
        if r.status_code == 202:
            uploaded.append(r.json()["document_id"])
    if len(uploaded) != 3:
        record("search fixtures uploaded", False, f"only {len(uploaded)}/3 accepted")
        return
    record("search fixtures uploaded", True, "2 matching + 1 control")

    try:
        hit = api("GET", "/documents", token=token, params={"search": marker}).json()
        names = [d["file_name"] for d in hit.get("items", [])]
        record("search narrows the listing", len(names) == 2, f"{len(names)} match(es)")
        record("non-matching document excluded",
               all(marker in n for n in names),
               "unrelated_gamma.txt stayed out")

        # The count must be recomputed under the filter. A total that still
        # reflects the unfiltered corpus is the bug that makes pagination lie.
        record("total reflects the filter", hit.get("total") == 2,
               f"total={hit.get('total')}")

        # A literal % must not behave as a wildcard. If escaping were missing
        # this returns the whole corpus rather than nothing.
        wild = api("GET", "/documents", token=token, params={"search": "%"}).json()
        record("percent is escaped, not a wildcard", wild.get("total") == 0,
               f"total={wild.get('total')} -- a wildcard would match everything")

        # `_` is LIKE's single-character wildcard; escaped, it matches only a
        # real underscore, so the two probe files match and nothing else does.
        under = api("GET", "/documents", token=token,
                    params={"search": f"{marker}_"}).json()
        record("underscore is escaped", under.get("total") == 2,
               f"total={under.get('total')}")

        # Searching must not become a way around clearance: the filter is an
        # extra AND, never a replacement for the sensitivity predicate.
        page = api("GET", "/documents", token=token,
                   params={"search": marker, "size": 1}).json()
        record("search composes with paging", len(page.get("items", [])) == 1
               and page.get("total") == 2,
               "page is short but the total is honest")
    finally:
        for doc_id in uploaded:
            api("DELETE", f"/documents/{doc_id}", token=token)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-llm", action="store_true",
                        help="also run document ingestion + one RAG answer")
    args = parser.parse_args()

    print("=" * 72)
    print("INTEGRATION TEST -- live stack")
    print("=" * 72)

    rc, _ = dc("ps", "-q", "api", timeout=30)
    if rc != 0:
        print("\nDocker is not running. Start Docker Desktop, then:")
        print("  docker compose up -d")
        return 1

    test_infrastructure()

    print("\n  minting test identities via Firebase Admin SDK...")
    viewer = mint_test_token("itest-viewer@gmail.com", "viewer")
    admin = mint_test_token("itest-admin@gmail.com", "admin")
    print(f"  viewer token: {'ok' if viewer else 'FAILED'}   admin token: {'ok' if admin else 'FAILED'}")

    test_auth(viewer, admin)
    test_rbac(viewer, admin)
    test_router(viewer)
    test_calculator_security()
    test_sql_validation()
    test_pii()
    test_flags_and_killswitch(admin, viewer)
    test_audit(admin)
    test_governance_endpoints(admin)
    test_web_search()
    test_metrics()
    test_p0_feedback(admin)
    test_p0_classification(admin)
    test_p0_conversations(admin)
    test_p0_conversation_isolation(viewer, admin)
    test_p1_question_redaction(admin)
    test_p2_document_search(admin)
    # Its own identity: this test deliberately exhausts a budget, and the
    # window outlives the run. Sharing an identity would leave the next run
    # pre-throttled and fail every section that chats.
    burner = mint_test_token("itest-ratelimit@gmail.com", "viewer")
    test_p1_rate_limit(burner)
    test_rag(admin, args.with_llm)

    print("\n" + "=" * 72)
    passed = [r for r in results if r[1] == "PASS"]
    failed = [r for r in results if r[1] == "FAIL"]
    skipped = [r for r in results if r[1] == "SKIP"]
    print(f"{len(passed)} passed | {len(failed)} failed | {len(skipped)} skipped")
    if failed:
        print("\nFAILURES:")
        for name, _, detail in failed:
            print(f"  x {name}\n      {detail}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
