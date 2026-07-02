import os

import httpx
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
PAGE_TITLE = os.getenv("STREAMLIT_PAGE_TITLE", "Enterprise RAG Platform")

st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title(f"🤖 {PAGE_TITLE}")
st.markdown("---")

# ── Live metrics ─────────────────────────────────────────────────────────────
doc_count = 0
health_checks: dict = {}
api_ok = False

try:
    health_resp = httpx.get(f"{API_BASE}/api/v1/health", timeout=3)
    if health_resp.status_code == 200:
        health_data = health_resp.json()
        health_checks = health_data.get("checks", {})
        api_ok = health_data.get("status") == "healthy"
except Exception:
    pass

try:
    docs_resp = httpx.get(f"{API_BASE}/api/v1/documents", timeout=3)
    if docs_resp.status_code == 200:
        doc_count = docs_resp.json().get("total", 0)
except Exception:
    pass

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("API Status", "Online ✅" if api_ok else "Offline ❌")
with col2:
    st.metric("Documents", doc_count)
with col3:
    st.metric("PostgreSQL", health_checks.get("postgres", "—"))
with col4:
    st.metric("Qdrant", health_checks.get("qdrant", "—"))
with col5:
    st.metric("Redis", health_checks.get("redis", "—"))

st.markdown("---")
st.info("Navigate using the sidebar pages. Start by uploading documents on the **Document Management** page.")
st.markdown("""
### Quick Start
1. **Documents** → Upload your PDFs, DOCX, TXT, MD, or HTML files
2. **Chat** → Ask questions against your indexed documents with real-time streaming
3. **Retrieval Inspector** → Debug and tune your retrieval pipeline
4. **Evaluation** → View RAG quality metrics
5. **Admin** → Configure LLM providers, embeddings, and retrieval settings
""")
