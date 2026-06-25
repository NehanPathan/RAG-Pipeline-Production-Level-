import streamlit as st

st.set_page_config(
    page_title="Enterprise RAG Platform",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Enterprise Agentic RAG Platform")
st.markdown("---")

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Documents", "0", "0")
with col2:
    st.metric("Chunks", "0", "0")
with col3:
    st.metric("Queries Today", "0")
with col4:
    st.metric("Avg Faithfulness", "N/A")
with col5:
    st.metric("Avg Latency", "N/A")

st.markdown("---")
st.info("Navigate using the sidebar pages. Start by uploading documents on the **Document Management** page.")
st.markdown("""
### Quick Start
1. **Documents** → Upload your PDFs, DOCX, TXT, MD, or HTML files
2. **Chat** → Ask questions against your indexed documents
3. **Retrieval Inspector** → Debug and tune your retrieval pipeline
4. **Evaluation** → Run RAGAS evaluation and view quality metrics
5. **Admin** → Configure LLM providers, embeddings, and retrieval settings
""")
