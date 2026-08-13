"""Governance console — the GM3 controls, made operable.

Deliberately organised by NIST function rather than by API endpoint: an
operator asking "are our controls working" thinks in terms of govern / map /
measure / manage, not in terms of which route returns which JSON.
"""

import os

import pandas as pd
import streamlit as st

from src.ui import http as httpx
from src.ui.auth import require_auth

st.set_page_config(page_title="Governance", layout="wide")

# Gate before rendering anything: Streamlit's multipage nav lists every
# page regardless of sign-in state, so each page must check for itself.
require_auth()
st.title("Governance Console")
st.caption("NIST AI RMF — Govern · Map · Measure · Manage")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

# Identity now comes from the verified Firebase token that `src.ui.http`
# attaches to every call. The `X-User-Id` box that used to live here let you
# type any user id -- harmless when nothing verified identity anyway, but
# actively misleading now that something does, since the backend ignores it
# whenever FIREBASE_ENABLED is true.

# Several views below are steward/admin only. Rendering a 403 as a plain
# message beats a traceback: for a viewer, being refused *is* the correct
# outcome, not an error.
DENIED = "Forbidden — this view needs a higher role (steward or admin)."


def _get(path: str, **params):
    try:
        r = httpx.get(f"{API_BASE}{path}", params=params or None, timeout=15)
        if r.status_code in (401, 403):
            return {"__error__": DENIED if r.status_code == 403 else "Session expired — sign in again."}
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"__error__": str(exc)}


def _put(path: str, payload: dict):
    try:
        r = httpx.put(f"{API_BASE}{path}", json=payload, timeout=15)
        if r.status_code in (401, 403):
            return {"__error__": DENIED}
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"__error__": str(exc)}


def _post(path: str, **params):
    try:
        r = httpx.post(f"{API_BASE}{path}", params=params or None, timeout=60)
        if r.status_code in (401, 403):
            return {"__error__": DENIED}
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"__error__": str(exc)}


def _bail(payload) -> bool:
    if isinstance(payload, dict) and "__error__" in payload:
        st.error(payload["__error__"])
        return True
    return False


tab_status, tab_govern, tab_map, tab_measure, tab_manage, tab_system = st.tabs(
    ["Status", "Govern", "Map", "Measure", "Manage", "System"]
)

# ---------------------------------------------------------------- Status

with tab_status:
    status = _get("/api/v1/governance/status")
    if not _bail(status):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Policy", status["policy_version"])
        mode = status["enforcement_mode"]
        c2.metric("Enforcement", mode)
        c3.metric("Risks tracked", sum(status["risks_by_function"].values()))
        c4.metric("Controls", status["controls"])

        if mode == "monitor":
            st.warning(
                "Enforcement is in **monitor** mode: violations are counted and "
                "audited, but nothing is blocked. This is the intended way to "
                "roll out a new control — it is not a steady state."
            )

        st.subheader("Risks by function")
        st.bar_chart(pd.Series(status["risks_by_function"], name="risks"))

        st.subheader("Quality floors in force")
        st.dataframe(
            pd.DataFrame(
                [{"metric": k, "floor": v} for k, v in status["quality_floors"].items()]
            ),
            hide_index=True,
            use_container_width=True,
        )

        st.subheader("Kill switches")
        flags = status["flags"]
        cols = st.columns(len(flags))
        for col, (name, enabled) in zip(cols, flags.items(), strict=False):
            col.metric(name.replace("_enabled", ""), "ON" if enabled else "OFF")

# ---------------------------------------------------------------- Govern

with tab_govern:
    st.subheader("Active policy")
    st.caption(
        "What this process actually loaded — which can differ from what is in "
        "the repository if a container is stale or a config change was never "
        "applied. That difference is the reason this view exists."
    )
    policy = _get("/api/v1/governance/policy")
    if not _bail(policy):
        st.json(policy["policy"], expanded=False)
        st.info(
            f"You are resolved as role **{policy['principal']['role']}** with "
            f"clearance **{policy['principal']['clearance']}**."
        )

    st.divider()
    st.subheader("Audit trail")
    col1, col2 = st.columns([1, 3])
    hours = col1.number_input("Window (hours)", 1, 720, 168)
    action = col2.text_input("Filter by action (optional)", placeholder="document_deleted")

    entries = _get("/api/v1/governance/audit", hours=hours, action=action or None, limit=200)
    if not _bail(entries):
        if not entries:
            st.info("No audit entries in this window.")
        else:
            df = pd.DataFrame(entries)
            st.dataframe(
                df[["created_at", "action", "outcome", "actor_role", "resource_type",
                    "resource_id", "control_id", "reason", "trace_id"]],
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "Copy a `trace_id` into your tracing backend to expand any entry "
                "into the full span tree for the request that caused it."
            )

# ------------------------------------------------------------------- Map

with tab_map:
    st.subheader("Risk register")
    st.caption(
        "Every risk is bound to the metric that watches it and the controls "
        "that mitigate it. CI fails if a control names a file that no longer "
        "exists, or a risk names a metric nobody records."
    )
    fn = st.selectbox("Function", ["(all)", "govern", "map", "measure", "manage"])
    risks = _get("/api/v1/governance/risks", function=None if fn == "(all)" else fn)

    if not _bail(risks):
        st.write(f"Register v{risks['version']} — {risks['count']} risk(s)")
        for risk in risks["risks"]:
            severity = risk["severity"]
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(severity, "⚪")
            with st.expander(
                f"{icon} {risk['id']} — {risk['title']}  ·  {risk['function'].upper()}"
            ):
                c1, c2, c3 = st.columns(3)
                c1.write(f"**Severity:** {severity}")
                c2.write(f"**Likelihood:** {risk['likelihood']}")
                c3.write(f"**Owner:** {risk['owner']}")
                st.write(f"**Watched by:** `{risk['metric']}`")
                if risk["threshold"] is not None:
                    st.write(
                        f"**Threshold:** {risk['threshold']} "
                        f"({risk['threshold_direction']})"
                    )
                st.write("**Controls**")
                for control in risk["controls"]:
                    st.markdown(f"- `{control['id']}` — {control['description']}")
                    for path in control["implemented_in"]:
                        st.caption(f"     ↳ {path}")
                if risk["residual_risk"]:
                    st.warning(f"**Residual risk:** {risk['residual_risk']}")

# --------------------------------------------------------------- Measure

with tab_measure:
    st.subheader("Offline quality gate")
    st.caption("The same judgement CI makes, from the same policy object.")
    dataset = st.text_input("Dataset", value="golden_set_v1")
    gate = _get("/api/v1/evaluation/gate", dataset_name=dataset)

    if not _bail(gate):
        if gate.get("status") == "no_completed_run":
            st.info(
                "No completed evaluation run for this dataset. The gate fails "
                "rather than passing on absent evidence."
            )
        else:
            (st.success if gate["passing"] else st.error)(
                f"Gate {'PASSING' if gate['passing'] else 'FAILING'} "
                f"(run {gate['run_id'][:8]}, policy v{gate['policy_version']})"
            )
            st.dataframe(pd.DataFrame(gate["metrics"]), hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Online quality — live traffic")
    st.caption(
        "Same judge prompts as the offline runner, so the two are directly "
        "comparable. A gap between them is a fact about the traffic, not the metric."
    )
    window = st.slider("Window (hours)", 1, 168, 24)
    online = _get("/api/v1/evaluation/online", window_hours=window)

    if not _bail(online):
        if not online["averages"]:
            st.info(
                f"No scored samples in the last {window}h. Sampling is a fraction "
                "of traffic — widen the window or raise the sample rate."
            )
        else:
            rows = [
                {
                    "metric": name,
                    "value": value,
                    "floor": online["floors"].get(name),
                    "status": "BREACH" if name in online["breaches"] else "ok",
                }
                for name, value in online["averages"].items()
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.caption(f"{online['sample_count']} sample(s) scored.")
            if online["breaches"]:
                st.error(f"Below policy floor: {', '.join(online['breaches'])}")

    st.divider()
    st.subheader("User feedback")
    summary = _get("/api/v1/feedback/summary")
    if not _bail(summary):
        c1, c2, c3 = st.columns(3)
        c1.metric("Total", summary["total"])
        c2.metric("Average rating", summary["average_rating"] or "—")
        c3.metric("Negative rate", f"{summary['negative_rate']:.1%}")

# ---------------------------------------------------------------- Manage

with tab_manage:
    st.subheader("Feature flags & kill switches")
    st.warning(
        "These take effect within ~10 seconds everywhere, without a redeploy. "
        "`answering_enabled = off` stops the product. Every change is audited."
    )
    flags = _get("/api/v1/governance/flags")
    if not _bail(flags):
        # The endpoint returns a list of descriptors (name, enabled, scope,
        # source, description), not a name->bool map: the extra fields are
        # what let an operator see *why* a flag is off — an environment
        # default and a deliberate override look identical without them.
        entries = flags.get("flags", [])
        if isinstance(entries, dict):  # tolerate the older shape
            entries = [{"name": k, "enabled": v, "scope": "", "source": "", "description": ""}
                       for k, v in entries.items()]

        for scope in ("kill_switch", "capability", ""):
            group = [e for e in entries if (e.get("scope") or "") == scope]
            if not group:
                continue
            st.markdown(
                {"kill_switch": "#### Kill switches", "capability": "#### Capabilities"}
                .get(scope, "#### Other")
            )
            for entry in group:
                name, enabled = entry["name"], entry["enabled"]
                col1, col2, col3 = st.columns([3, 1, 2])
                col1.write(f"**{name}**")
                if entry.get("description"):
                    col1.caption(entry["description"])
                source = entry.get("source", "")
                col2.write("🟢 enabled" if enabled else "🔴 disabled")
                if source:
                    col2.caption(source)
                reason = col3.text_input(
                    "Reason", key=f"reason_{name}", label_visibility="collapsed",
                    placeholder="Reason (audited)",
                )
                if col3.button(
                    "Disable" if enabled else "Enable",
                    key=f"btn_{name}",
                    use_container_width=True,
                ):
                    result = _put(
                        f"/api/v1/governance/flags/{name}",
                        {"enabled": not enabled, "reason": reason},
                    )
                    if not _bail(result):
                        st.success(f"{name} → {'disabled' if enabled else 'enabled'}")
                        st.rerun()

    st.divider()
    st.subheader("Retention")
    st.caption(
        "Purges documents past their retention deadline across Postgres, "
        "Qdrant, Elasticsearch and the semantic cache."
    )
    col1, col2 = st.columns(2)
    if col1.button("Dry run — list what would be purged", use_container_width=True):
        result = _post("/api/v1/governance/retention/run", dry_run="true")
        if not _bail(result):
            st.info(f"{result['scanned']} document(s) past their deadline.")
            if result["document_ids"]:
                st.code("\n".join(result["document_ids"]))

    if col2.button("Purge now (irreversible)", type="primary", use_container_width=True):
        result = _post("/api/v1/governance/retention/run", dry_run="false")
        if not _bail(result):
            st.success(f"Purged {result['purged']}, failed {result['failed']}.")

    st.divider()
    st.subheader("Feedback loop")
    st.caption(
        "Promotes negative feedback into a golden dataset the CI quality gate "
        "measures against, so a reported failure becomes a permanent regression "
        "case. Idempotent — safe to run repeatedly."
    )
    ds = st.text_input("Target dataset", value="regression_from_feedback")
    if st.button("Promote negative feedback"):
        result = _post("/api/v1/feedback/promote", dataset_name=ds)
        if not _bail(result):
            st.success(f"Added {result['added']}, skipped {result['skipped']} → {result['dataset']}")


# ---------------------------------------------------------------- System

with tab_system:
    st.subheader("What is actually loaded")
    st.caption(
        "A registry populated by import side effects can silently lose an "
        "entry when a module fails to import. Without this view that presents "
        "as 'the router never picks that tool' rather than as a missing plugin."
    )

    plugins = _get("/api/v1/governance/plugins")
    if not _bail(plugins):
        col_tools, col_llm, col_pii = st.columns(3)

        with col_tools:
            st.markdown("**Tools**")
            for tool in plugins.get("tools", []):
                state = "🟢" if tool.get("enabled", True) else "⚪"
                st.write(f"{state} `{tool['name']}`")
                if tool.get("requires_flag"):
                    st.caption(f"flag: {tool['requires_flag']}")

        with col_llm:
            st.markdown("**LLM providers**")
            for provider in plugins.get("llm_providers", []):
                st.write(f"• `{provider['name']}`")

        with col_pii:
            st.markdown("**PII detectors**")
            for detector in plugins.get("pii_detectors", []):
                st.write(f"• `{detector['name']}`")

    st.divider()
    st.subheader("AI gateway")
    st.caption(
        "The provider fallback chain in force. A provider error falls through "
        "to the next entry, so a single vendor outage degrades rather than "
        "fails. Privileged: the chain reveals which vendors hold this "
        "deployment's data."
    )
    gateway = _get("/api/v1/governance/gateway")
    if not _bail(gateway):
        for role, described in gateway.items():
            chain = described.get("chain", [])
            arrow = "  →  ".join(
                f"{link['provider']} ({link['model']})" for link in chain
            )
            st.markdown(f"**{role}**: {arrow or '—'}")
            if len(chain) == 1:
                st.caption(
                    "Single provider — an outage fails the request rather than "
                    "falling back. Add one to LLM_FALLBACK_PROVIDERS to change that."
                )
        st.caption(
            f"fallback enabled: {gateway.get('small', {}).get('fallback_enabled')}"
        )
