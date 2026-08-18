from __future__ import annotations

import os
import time

import plotly.graph_objects as go
import streamlit as st

from src.ui import http as httpx
from src.ui.auth import require_auth

st.set_page_config(page_title="Evaluation Dashboard", layout="wide")

# Gate before rendering anything: Streamlit's multipage nav lists every
# page regardless of sign-in state, so each page must check for itself.
require_auth()
st.title("Evaluation Dashboard")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

METRIC_LABELS = {
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Answer Relevancy",
    "context_relevancy": "Context Relevancy",
    "answer_correctness": "Answer Correctness",
}
METRIC_HELP = {
    "faithfulness": "Answer only uses information from retrieved contexts (no hallucination)",
    "answer_relevancy": "Answer directly addresses the question",
    "context_relevancy": "Retrieved contexts are relevant to the question",
    "answer_correctness": "Answer matches the ground-truth reference (requires ground_truth in dataset)",
}


def _fetch_runs() -> list[dict]:
    try:
        r = httpx.get(f"{API_BASE}/api/v1/evaluation/runs", timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def _fetch_datasets() -> list[str]:
    try:
        r = httpx.get(f"{API_BASE}/api/v1/evaluation/datasets", timeout=5)
        if r.status_code == 200:
            return r.json().get("datasets", [])
    except Exception:
        pass
    return []


tab_overview, tab_runs, tab_trigger, tab_system = st.tabs(
    ["Metrics Overview", "Run History", "Trigger Evaluation", "System Metrics"]
)

# ─── Metrics Overview ─────────────────────────────────────────────────────────
with tab_overview:
    runs = _fetch_runs()
    completed = [r for r in runs if r["status"] == "completed"]

    if not completed:
        st.info(
            "No completed evaluation runs yet. Go to **Trigger Evaluation** to run one."
        )
    else:
        latest = completed[0]
        metrics = {m["name"]: m["value"] for m in latest.get("metrics", [])}

        st.subheader(f"Latest run: `{latest['name']}`")
        st.caption(
            f"Dataset: **{latest['dataset_name']}** · "
            f"Samples: **{latest['completed_items']}** · "
            f"Completed: **{latest.get('completed_at', '—')}**"
        )

        kpi_keys = list(METRIC_LABELS.keys())
        cols = st.columns(len(kpi_keys))
        for col, key in zip(cols, kpi_keys, strict=False):
            val = metrics.get(key)
            col.metric(
                METRIC_LABELS[key],
                f"{val:.3f}" if val is not None else "N/A",
                help=METRIC_HELP[key],
            )

        # Bar chart
        if metrics:
            st.divider()
            fig = go.Figure(
                go.Bar(
                    x=[METRIC_LABELS.get(k, k) for k in metrics],
                    y=list(metrics.values()),
                    marker_color=["#4CAF50" if v >= 0.7 else "#FF9800" if v >= 0.4 else "#F44336"
                                  for v in metrics.values()],
                    text=[f"{v:.3f}" for v in metrics.values()],
                    textposition="outside",
                )
            )
            fig.update_layout(
                yaxis=dict(range=[0, 1.1], title="Score (0–1)"),
                xaxis_title="Metric",
                title="RAG Quality Scores — Latest Run",
                height=380,
                margin=dict(t=50, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Trend chart (all completed runs)
        if len(completed) > 1:
            st.subheader("Score Trends Across Runs")
            trend_names = [r["name"] for r in reversed(completed)]
            fig2 = go.Figure()
            for key, label in METRIC_LABELS.items():
                y_vals = []
                for r in reversed(completed):
                    m = {m["name"]: m["value"] for m in r.get("metrics", [])}
                    y_vals.append(m.get(key))
                if any(v is not None for v in y_vals):
                    fig2.add_trace(go.Scatter(x=trend_names, y=y_vals, name=label, mode="lines+markers"))
            fig2.update_layout(
                yaxis=dict(range=[0, 1.1], title="Score"),
                title="Metric Trends Over Time",
                height=350,
                margin=dict(t=50, b=20),
            )
            st.plotly_chart(fig2, use_container_width=True)

# ─── Run History ──────────────────────────────────────────────────────────────
with tab_runs:
    st.subheader("All Evaluation Runs")
    if st.button("Refresh"):
        st.rerun()

    runs = _fetch_runs()
    if not runs:
        st.info("No evaluation runs yet.")
    else:
        _STATUS_ICON = {"completed": "✅", "running": "⏳", "failed": "❌"}
        for run in runs:
            icon = _STATUS_ICON.get(run["status"], "⚪")
            label = f"{icon} {run['name']} — {run['status'].upper()} — {run['started_at'][:19]}"
            with st.expander(label):
                col1, col2, col3 = st.columns(3)
                col1.metric("Dataset", run["dataset_name"])
                col2.metric("Progress", f"{run['completed_items']} / {run['total_items']}")
                col3.metric("Status", run["status"])

                if run.get("error_message"):
                    st.error(f"Error: {run['error_message']}")

                metrics = run.get("metrics", [])
                if metrics:
                    m_cols = st.columns(len(metrics))
                    for col, m in zip(m_cols, metrics, strict=False):
                        col.metric(
                            METRIC_LABELS.get(m["name"], m["name"]),
                            f"{m['value']:.3f}",
                            help=f"n={m.get('sample_size', '?')}",
                        )
                elif run["status"] == "running":
                    st.info("Scoring in progress…")

        # Auto-refresh if any run is still running
        if any(r["status"] == "running" for r in runs):
            st.info("A run is in progress — refreshing in 10 seconds…")
            time.sleep(10)
            st.rerun()

# ─── Trigger Evaluation ───────────────────────────────────────────────────────
with tab_trigger:
    st.subheader("Start a New Evaluation Run")

    available_datasets = _fetch_datasets()

    run_name = st.text_input("Run name", value="eval_run_1", key="run_name")

    use_inline = st.toggle("Use inline questions (no dataset file needed)", value=not bool(available_datasets))

    if use_inline:
        st.caption("Enter one question per line:")
        inline_text = st.text_area(
            "Questions",
            height=150,
            placeholder="What is machine learning?\nHow does RAG work?\n...",
            key="inline_questions",
        )
        dataset_name = "inline"
        questions = [q.strip() for q in inline_text.splitlines() if q.strip()]
    else:
        dataset_name = st.selectbox(
            "Dataset",
            options=available_datasets or ["golden_set_v1"],
            key="dataset_select",
        )
        questions = None

    st.caption(
        "Each question is sent through the full RAG pipeline. "
        "The LLM then scores faithfulness, answer relevancy, and context relevancy. "
        "With ground_truth in the dataset, answer correctness is also scored."
    )

    can_start = (questions and len(questions) > 0) if use_inline else bool(dataset_name)
    if st.button("Start Evaluation", type="primary", disabled=not can_start):
        payload = {"name": run_name, "dataset_name": dataset_name}
        if use_inline:
            payload["questions"] = questions

        try:
            r = httpx.post(f"{API_BASE}/api/v1/evaluation/runs", json=payload, timeout=10)
            if r.status_code == 202:
                data = r.json()
                st.success(
                    f"Evaluation started! Run ID: `{data['run_id']}` · "
                    f"{data['total_items']} sample(s) queued. "
                    "Switch to **Run History** to track progress."
                )
            else:
                st.error(f"API error {r.status_code}: {r.text}")
        except Exception as e:
            st.error(f"Cannot reach API: {e}")

# ─── System Metrics ───────────────────────────────────────────────────────────
with tab_system:
    st.subheader("Live Prometheus Metrics")
    st.caption("Raw counters from `/api/v1/metrics`.")
    if st.button("Fetch", key="fetch_metrics"):
        try:
            r = httpx.get(f"{API_BASE}/api/v1/metrics", timeout=5)
            if r.status_code == 200:
                lines = [ln for ln in r.text.splitlines() if not ln.startswith("#") and ln.strip()]
                if lines:
                    st.code("\n".join(lines[:100]), language="text")
                    if len(lines) > 100:
                        st.caption(f"…and {len(lines) - 100} more lines.")
                else:
                    st.info("No metric samples yet.")
            else:
                st.warning(f"Metrics endpoint returned {r.status_code}.")
        except Exception as e:
            st.error(f"Cannot reach metrics endpoint: {e}")
