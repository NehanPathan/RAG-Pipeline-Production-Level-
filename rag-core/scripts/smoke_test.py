#!/usr/bin/env python
"""End-to-end smoke test against a running stack.

    python scripts/smoke_test.py

Checks the things that are easy to get wrong and silent when they are:
whether the Firebase credential actually reached the container, whether the
plugin registries populated, whether auth is really rejecting anonymous
callers, and whether the new metric series exist.

Run from the host — it only needs `requests`, which every dev box has. The
parts that require credentials or in-process state (Tavily, the router) are
delegated to `docker compose exec`, because with FIREBASE_REQUIRE_AUTH=true
an HTTP smoke test cannot reach /chat without a real signed-in user's token.
"""
from __future__ import annotations

import json
import subprocess
import sys

BASE = "http://localhost:8000/api/v1"
PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    icon = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn "}[status]
    print(f"[{icon}] {name}" + (f"  — {detail}" if detail else ""))


def get(path: str, timeout: int = 20):
    import requests

    return requests.get(f"{BASE}{path}", timeout=timeout)


def post(path: str, payload: dict, timeout: int = 60):
    import requests

    return requests.post(f"{BASE}{path}", json=payload, timeout=timeout)


def in_container(code: str, timeout: int = 180) -> tuple[int, str]:
    """Run Python inside the api container."""
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ----------------------------------------------------------------- checks


def check_health() -> None:
    try:
        r = get("/health")
    except Exception as exc:
        record("API reachable", FAIL, str(exc))
        return
    if r.status_code != 200:
        record("API reachable", FAIL, f"HTTP {r.status_code}")
        return

    data = r.json()
    record("API reachable", PASS, f"status={data['status']}")
    for dep, state in data.get("checks", {}).items():
        record(f"  dependency: {dep}", PASS if state == "ok" else FAIL, state[:60])


def check_governance() -> None:
    try:
        data = get("/governance/status").json()
    except Exception as exc:
        record("Governance status", FAIL, str(exc))
        return
    record(
        "Governance status",
        PASS,
        f"policy v{data['policy_version']} / {data['enforcement_mode']} / "
        f"{sum(data['risks_by_function'].values())} risks / {data['controls']} controls",
    )


def check_plugins() -> None:
    try:
        data = get("/governance/plugins").json()
    except Exception as exc:
        record("Plugin registries", FAIL, str(exc))
        return

    tools = [t["name"] for t in data["tools"]]
    providers = [p["name"] for p in data["llm_providers"]]
    detectors = [d["name"] for d in data["pii_detectors"]]

    record("Tools registered", PASS if len(tools) >= 5 else FAIL, ", ".join(tools))
    record("LLM providers", PASS if "openai" in providers else FAIL, ", ".join(providers))
    record("PII detectors", PASS if len(detectors) >= 8 else FAIL, f"{len(detectors)} loaded")

    web = next((t for t in data["tools"] if t["name"] == "web_search"), None)
    if web:
        record(
            "  web_search enabled",
            PASS if web["enabled"] else WARN,
            "on" if web["enabled"] else "off (FEATURE_ENABLE_WEB_SEARCH=false)",
        )


def check_auth_enforced() -> None:
    """With Firebase on, an anonymous /chat must be rejected.

    This is the check that proves R-G03 is actually closed. A 200 here would
    mean the whole authentication layer is inert.
    """
    try:
        r = post("/chat", {"query": "hello", "stream": False})
    except Exception as exc:
        record("Auth rejects anonymous", FAIL, str(exc))
        return

    if r.status_code == 401:
        record("Auth rejects anonymous", PASS, "401 as expected — Firebase is enforcing")
    elif r.status_code == 200:
        record(
            "Auth rejects anonymous",
            WARN,
            "200 — auth is OFF (FIREBASE_ENABLED=false). Fine for local dev.",
        )
    else:
        record("Auth rejects anonymous", WARN, f"HTTP {r.status_code}")


def check_firebase_credential() -> None:
    """Did the service-account JSON actually reach the container?

    The failure this catches is nasty: without the volume mount the file is
    simply absent, Firebase falls back to (empty) env vars, and every request
    401s with a message that looks like a client problem.
    """
    code = (
        "import json,pathlib,os;"
        "p=pathlib.Path(os.getenv('FIREBASE_SERVICE_ACCOUNT','config/firebase-service-account.json'));"
        "print('EXISTS' if p.exists() else 'MISSING', end=' ');"
        "print(json.loads(p.read_text())['project_id'] if p.exists() else '')"
    )
    rc, out = in_container(code)
    if rc != 0:
        record("Firebase credential in container", FAIL, out[-160:])
    elif out.startswith("EXISTS"):
        record("Firebase credential in container", PASS, f"project={out.split()[-1]}")
    else:
        record(
            "Firebase credential in container",
            FAIL,
            "file not found inside container — is ./config mounted in compose?",
        )


def check_router() -> None:
    """Routing decisions, exercised in-process so auth is not in the way."""
    code = (
        "import logging;logging.disable(logging.CRITICAL);"
        "from src.routing.rules import apply_rules;"
        "import json;"
        "print(json.dumps({q:(apply_rules(q).route.value if apply_rules(q) else 'rag(classifier)')"
        " for q in ['hi','12 * 7','what time is it','what is our refund policy']}))"
    )
    rc, out = in_container(code)
    if rc != 0:
        record("Query router", FAIL, out[-200:])
        return
    try:
        routes = json.loads(out.splitlines()[-1])
    except Exception:
        record("Query router", FAIL, out[-200:])
        return

    expected = {
        "hi": "greeting",
        "12 * 7": "calculator",
        "what time is it": "datetime",
        "what is our refund policy": "rag(classifier)",
    }
    ok = all(routes.get(q) == r for q, r in expected.items())
    record(
        "Query router",
        PASS if ok else FAIL,
        " | ".join(f"{q!r}->{v}" for q, v in routes.items()),
    )


def check_tavily() -> None:
    """Actually call Tavily. A key that is present but wrong looks identical
    to a working one until something searches with it."""
    code = (
        "import asyncio,logging;logging.disable(logging.CRITICAL);"
        "from src.governance import feature_flags;"
        "from src.tools.registry import get_tool;"
        "from src.tools.base import ToolRequest;"
        "print('FLAG_OFF') if not feature_flags.is_enabled('enable_web_search') else None;"
        "r=asyncio.run(get_tool('web_search').run(ToolRequest(query='what is retrieval augmented generation')))"
        " if feature_flags.is_enabled('enable_web_search') else None;"
        "print('OK' if (r and r.succeeded) else ('ERR:'+(r.error if r else 'disabled')))"
    )
    rc, out = in_container(code, timeout=120)
    last = out.splitlines()[-1] if out else ""
    if "FLAG_OFF" in out:
        record("Tavily web search", WARN, "FEATURE_ENABLE_WEB_SEARCH=false")
    elif last.startswith("OK"):
        record("Tavily web search", PASS, "live search returned results")
    else:
        record("Tavily web search", FAIL, last[:180] or out[-180:])


def check_metrics() -> None:
    try:
        body = get("/metrics").text
    except Exception as exc:
        record("Prometheus metrics", FAIL, str(exc))
        return

    wanted = [
        "rag_router_decisions_total",
        "rag_retrieval_skipped_total",
        "rag_tool_invocations_total",
        "rag_pii_redactions_total",
        "rag_feature_flag_state",
        "rag_llm_gateway_requests_total",
        "rag_auth_verifications_total",
    ]
    missing = [m for m in wanted if m not in body]
    record(
        "New metric series exported",
        PASS if not missing else WARN,
        "all present" if not missing else f"not yet emitted: {missing}",
    )


def main() -> int:
    print("=" * 70)
    print("SMOKE TEST")
    print("=" * 70)

    check_health()
    print()
    check_governance()
    check_plugins()
    check_metrics()
    print()
    check_firebase_credential()
    check_auth_enforced()
    print()
    check_router()
    check_tavily()

    print()
    print("=" * 70)
    failed = [r for r in results if r[1] == FAIL]
    warned = [r for r in results if r[1] == WARN]
    print(f"{len(results) - len(failed) - len(warned)} passed, {len(warned)} warnings, {len(failed)} failed")
    for name, _, detail in failed:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
