import json
import os

import httpx
import streamlit as st

st.set_page_config(page_title="Retrieval Inspector", layout="wide")
st.title("Retrieval Inspector")
st.caption("Debug and inspect the full retrieval pipeline for any query.")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

query = st.text_input("Enter a query to inspect:", placeholder="What is the refund policy?")
col1, col2, col3 = st.columns(3)
with col1:
    domain_filter = st.selectbox("Domain filter", ["(none)", "HR", "Legal", "Finance", "Operations"])
with col2:
    top_k = st.slider("Top-K per search", 5, 50, 20)
with col3:
    include_content = st.toggle("Include chunk content", value=True)

if st.button("Inspect", type="primary", disabled=not query):
    with st.spinner("Running retrieval pipeline..."):
        try:
            response = httpx.post(
                f"{API_BASE}/api/v1/retrieval/inspect",
                json={
                    "query": query,
                    "filters": {"domain": domain_filter if domain_filter != "(none)" else None},
                    "include_content": include_content,
                },
                timeout=60,
            )
            if response.status_code == 200:
                data = response.json()
                pq = data.get("processed_query", {})

                st.subheader("Query Processing")
                col1, col2, col3 = st.columns(3)
                col1.info(f"**Intent:** {pq.get('intent', 'N/A')}")
                col2.info(f"**Domain:** {pq.get('domain', 'N/A')}")
                col3.info(f"**Rewritten:** {pq.get('rewritten', query)}")

                if pq.get("expanded"):
                    st.write("**Expanded queries:**", pq["expanded"])

                lat = data.get("latency_breakdown", {})
                if lat:
                    st.subheader("Latency Breakdown")
                    lat_cols = st.columns(len(lat))
                    for i, (k, v) in enumerate(lat.items()):
                        lat_cols[i].metric(k.replace("_", " ").title(), f"{v}ms")

                tab_vec, tab_bm25, tab_fused, tab_reranked = st.tabs([
                    "Vector Results", "BM25 Results", "Fused (RRF)", "Reranked"
                ])

                def render_results(tab, results, score_key):
                    with tab:
                        if not results:
                            st.info("No results.")
                            return
                        for r in results[:10]:
                            with st.expander(f"Rank {r.get('rank', '?')} — Score: {r.get(score_key, 0):.4f} | {r.get('document_name', '')} p.{r.get('page_number', '?')}"):
                                if include_content:
                                    st.markdown(r.get("content", "")[:500])
                                st.caption(f"chunk_id: {r.get('chunk_id', '')}")

                render_results(tab_vec, data.get("vector_results", []), "vector_score")
                render_results(tab_bm25, data.get("bm25_results", []), "bm25_score")
                render_results(tab_fused, data.get("fused_results", []), "rrf_score")
                render_results(tab_reranked, data.get("reranked_results", []), "rerank_score")

            else:
                st.error(f"API error {response.status_code}: {response.text}")
        except Exception as e:
            st.error(f"Connection error: {e}")
