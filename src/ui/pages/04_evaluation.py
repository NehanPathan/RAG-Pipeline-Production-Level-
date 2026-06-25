import os

import httpx
import streamlit as st

st.set_page_config(page_title="Evaluation Dashboard", layout="wide")
st.title("Evaluation Dashboard")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

tab_metrics, tab_runs, tab_trigger = st.tabs(["Metrics Overview", "Evaluation Runs", "Trigger Eval"])

with tab_metrics:
    st.subheader("Latest Evaluation Scores")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Faithfulness", "N/A", help="Answer grounded in retrieved context")
    col2.metric("Answer Relevancy", "N/A", help="Answer addresses the question")
    col3.metric("Context Precision", "N/A", help="Retrieved context is relevant")
    col4.metric("Context Recall", "N/A", help="All relevant context was retrieved")
    col5.metric("Hallucination", "N/A", help="Rate of hallucinated content (lower=better)")

    st.info("Run an evaluation to see metrics here.")

with tab_runs:
    st.subheader("Evaluation Run History")
    try:
        response = httpx.get(f"{API_BASE}/api/v1/evaluation/runs", timeout=10)
        if response.status_code == 200:
            runs = response.json().get("items", [])
            if runs:
                for run in runs:
                    with st.expander(f"{run['name']} — {run['status'].upper()} — {run.get('completed_at', 'in progress')}"):
                        metrics = run.get("metrics", {})
                        if metrics:
                            cols = st.columns(len(metrics))
                            for i, (k, v) in enumerate(metrics.items()):
                                cols[i].metric(k.replace("_", " ").title(), f"{v:.3f}")
            else:
                st.info("No evaluation runs yet.")
        else:
            st.warning("Could not load evaluation runs from API.")
    except Exception as e:
        st.error(f"Connection error: {e}")

with tab_trigger:
    st.subheader("Run Evaluation")
    run_name = st.text_input("Run name", value="manual_eval")
    dataset = st.selectbox("Dataset", ["golden_set_v1", "golden_set_v2"])
    evaluators = st.multiselect("Evaluators", ["ragas", "deepeval"], default=["ragas"])
    if st.button("Start Evaluation", type="primary"):
        try:
            response = httpx.post(
                f"{API_BASE}/api/v1/evaluation/runs",
                json={"name": run_name, "dataset_name": dataset, "evaluators": evaluators},
                timeout=30,
            )
            if response.status_code == 202:
                data = response.json()
                st.success(f"Evaluation started! Run ID: `{data['run_id']}`")
            else:
                st.error(f"Failed: {response.text}")
        except Exception as e:
            st.error(f"Connection error: {e}")
