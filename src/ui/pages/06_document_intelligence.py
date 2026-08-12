import os
from collections import defaultdict

from src.ui import http as httpx
from src.ui.auth import require_auth
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Document Intelligence", layout="wide")

# Gate before rendering anything: Streamlit's multipage nav lists every
# page regardless of sign-in state, so each page must check for itself.
require_auth()
st.title("Document Intelligence")
st.caption(
    "OCR output, extracted layout, semantic chunk boundaries, chunk hierarchy, "
    "and embedding models for a single indexed document (Phase 4A)."
)

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


@st.cache_data(ttl=10)
def _list_documents():
    response = httpx.get(f"{API_BASE}/api/v1/documents", params={"size": 100}, timeout=10)
    response.raise_for_status()
    return response.json().get("items", [])


def _fetch(path: str):
    response = httpx.get(f"{API_BASE}{path}", timeout=15)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


try:
    documents = _list_documents()
except Exception as e:
    st.error(f"Cannot reach the API: {e}")
    st.stop()

indexed_docs = [d for d in documents if d.get("status") == "indexed"]
if not indexed_docs:
    st.info("No indexed documents yet. Upload one on the **Document Management** page first.")
    st.stop()

labels = {f"{d['file_name']} ({d['id'][:8]})": d["id"] for d in indexed_docs}
selected_label = st.selectbox("Choose a document", list(labels.keys()))
document_id = labels[selected_label]

intelligence = _fetch(f"/api/v1/documents/{document_id}/intelligence")
chunks_data = _fetch(f"/api/v1/documents/{document_id}/chunks")
chunks = (chunks_data or {}).get("items", [])

if intelligence is None:
    st.warning(
        "No Document Intelligence record for this document yet -- it may have been "
        "indexed before Phase 4A shipped, or ingestion is still running."
    )
    st.stop()

tab_ocr, tab_layout, tab_chunks, tab_embeddings, tab_graph = st.tabs(
    ["OCR", "Layout", "Chunk Hierarchy", "Embeddings", "Semantic Graph"]
)

# ── OCR ──────────────────────────────────────────────────────────────────────
with tab_ocr:
    if intelligence["ocr_ran"]:
        col1, col2, col3 = st.columns(3)
        col1.metric("Engine", intelligence["ocr_engine"])
        conf = intelligence.get("ocr_confidence_avg")
        col2.metric("Avg. Confidence", f"{conf:.2%}" if conf is not None else "—")
        col3.metric("Processing Time", f"{intelligence['ocr_processing_time_ms']:.0f} ms")
        st.caption(f"Detected language: {intelligence.get('ocr_language') or 'unknown'}")
    else:
        st.success("OCR was skipped -- this document had sufficient extractable text (searchable PDF, DOCX, or plain text).")

# ── Layout ───────────────────────────────────────────────────────────────────
with tab_layout:
    layout = intelligence["layout"]
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Tables", layout["tables_count"])
    col2.metric("Figures", layout["figures_count"])
    col3.metric("Lists", layout["lists_count"])
    col4.metric("Forms", layout["forms_count"])
    col5.metric("Footnotes", layout["footnotes_count"])

    st.subheader("Document Outline")

    def render_outline(nodes, depth=0):
        for node in nodes:
            st.markdown(f"{'&nbsp;&nbsp;&nbsp;&nbsp;' * depth}📄 **{node['title']}** (p.{node.get('page_number') or '—'})", unsafe_allow_html=True)
            render_outline(node.get("children", []), depth + 1)

    if layout["outline"]:
        render_outline(layout["outline"])
    else:
        st.info("No heading structure detected for this document.")

# ── Chunk Hierarchy ──────────────────────────────────────────────────────────
with tab_chunks:
    if not chunks:
        st.info("No chunks found for this document.")
    else:
        st.caption(f"{len(chunks)} chunks total.")
        parents = [c for c in chunks if c["chunk_type"] == "parent"]
        children_by_parent = defaultdict(list)
        standalone = []
        for c in chunks:
            if c["chunk_type"] == "child" and c["parent_chunk_id"]:
                children_by_parent[c["parent_chunk_id"]].append(c)
            elif c["chunk_type"] in ("table", "standalone"):
                standalone.append(c)

        for parent in parents:
            section = parent.get("section_title") or "(no heading)"
            with st.expander(f"📁 {section} — page {parent.get('page_number') or '—'} — {parent['token_count']} tokens"):
                st.text(parent["content"][:400])
                for child in children_by_parent.get(parent["id"], []):
                    st.markdown(f"  ↳ **child** ({child['token_count']} tok, page {child.get('page_number') or '—'})")
                    st.caption(child["content"][:200])

        if standalone:
            st.subheader("Tables / Standalone Chunks")
            for c in standalone:
                with st.expander(f"🔲 {c['chunk_type']} — page {c.get('page_number') or '—'}"):
                    st.text(c["content"][:400])

# ── Embeddings ───────────────────────────────────────────────────────────────
with tab_embeddings:
    col1, col2 = st.columns(2)
    col1.metric("Chunking-role model", intelligence["embedding_model_chunking"])
    col2.metric("Retrieval-role model", intelligence["embedding_model_retrieval"])
    st.caption(
        "Chunking-role embeddings power SemanticChunker's topic-boundary detection during "
        "ingestion; retrieval-role embeddings are what's actually stored in Qdrant and searched."
    )

# ── Semantic Graph ───────────────────────────────────────────────────────────
with tab_graph:
    edges = intelligence.get("semantic_graph", [])
    if not edges:
        st.info("No semantic similarity graph recorded for this document.")
    else:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=list(range(len(edges))),
                y=[e["similarity"] for e in edges],
                mode="lines+markers",
                name="Consecutive chunk similarity",
            )
        )
        fig.update_layout(
            xaxis_title="Consecutive chunk pair (by index)",
            yaxis_title="Cosine similarity",
            yaxis_range=[0, 1],
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Cosine similarity between each pair of consecutive chunks' retrieval embeddings. "
            "Dips indicate likely topic shifts between adjacent chunks."
        )
