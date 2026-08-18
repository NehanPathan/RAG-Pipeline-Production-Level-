import os

import streamlit as st

from src.ui import http as httpx
from src.ui.auth import require_auth

st.set_page_config(page_title="Admin Panel", layout="wide")

# Gate before rendering anything: Streamlit's multipage nav lists every
# page regardless of sign-in state, so each page must check for itself.
require_auth()
st.title("Admin Panel")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

tab_models, tab_retrieval, tab_system = st.tabs(["Model Config", "Retrieval Settings", "System Status"])

with tab_models:
    st.subheader("LLM Configuration")
    col1, col2 = st.columns(2)
    with col1:
        st.write("**Small LLM** (query rewriting, compression, enrichment)")
        small_provider = st.selectbox("Provider", ["anthropic", "openai", "gemini", "ollama"], key="small_provider")
        small_model_map = {
            "anthropic": "claude-haiku-4-5-20251001",
            "openai": "gpt-4o-mini",
            "gemini": "gemini-1.5-flash",
            "ollama": "llama3.2:3b",
        }
        st.text_input("Model", value=small_model_map[small_provider], key="small_model")
    with col2:
        st.write("**Large LLM** (answer generation)")
        large_provider = st.selectbox("Provider", ["anthropic", "openai", "gemini", "ollama"], key="large_provider")
        large_model_map = {
            "anthropic": "claude-sonnet-4-6",
            "openai": "gpt-4o",
            "gemini": "gemini-1.5-pro",
            "ollama": "llama3.1:70b",
        }
        st.text_input("Model", value=large_model_map[large_provider], key="large_model")

    st.divider()
    st.write("**Embedding Model**")
    emb_provider = st.selectbox("Embedding Provider", ["openai", "bge_m3", "e5_large"])
    emb_model_map = {
        "openai": "text-embedding-3-large",
        "bge_m3": "BAAI/bge-m3",
        "e5_large": "intfloat/e5-large-v2",
    }
    st.text_input("Embedding Model", value=emb_model_map[emb_provider])

    st.divider()
    st.write("**Reranker**")
    reranker = st.selectbox("Reranker", ["bge", "cohere"])

    if st.button("Save Model Config", type="primary"):
        st.success("Config saved (API call not yet wired).")

with tab_retrieval:
    st.subheader("Retrieval Settings")
    vector_top_k = st.slider("Vector Search Top-K", 5, 100, 20)
    bm25_top_k = st.slider("BM25 Search Top-K", 5, 100, 20)
    rerank_top_n = st.slider("Rerank Top-N (final results)", 3, 30, 10)
    rrf_k = st.number_input("RRF Constant (k)", min_value=1, max_value=200, value=60)

    st.divider()
    st.write("**Chunking**")
    parent_size = st.number_input("Parent Chunk Size (tokens)", 256, 4096, 1024)
    child_size = st.number_input("Child Chunk Size (tokens)", 64, 1024, 256)
    overlap = st.number_input("Overlap (tokens)", 0, 128, 32)

    if st.button("Save Retrieval Config", type="primary"):
        st.success("Config saved (API call not yet wired).")

with tab_system:
    st.subheader("System Status")
    if st.button("Check Health"):
        try:
            response = httpx.get(f"{API_BASE}/api/v1/health", timeout=10)
            if response.status_code == 200:
                data = response.json()
                overall = data.get("status", "unknown")
                if overall == "healthy":
                    st.success(f"System: {overall.upper()}")
                else:
                    st.warning(f"System: {overall.upper()}")

                checks = data.get("checks", {})
                cols = st.columns(len(checks))
                for i, (service, status) in enumerate(checks.items()):
                    icon = "✅" if status == "ok" else "❌"
                    cols[i].metric(service.upper(), f"{icon} {status}")
            else:
                st.error(f"Health check failed: {response.status_code}")
        except Exception as e:
            st.error(f"Cannot reach API: {e}")
