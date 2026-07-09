import os
import time

import httpx
import streamlit as st

st.set_page_config(page_title="Document Management", layout="wide")
st.title("Document Management")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

tab_upload, tab_list = st.tabs(["Upload", "Manage"])

with tab_upload:
    st.subheader("Upload Document")
    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["pdf", "docx", "txt", "md", "html"],
        help="Max 100MB per file",
    )
    col1, col2 = st.columns(2)
    with col1:
        domain = st.selectbox("Domain", ["General", "HR", "Legal", "Finance", "Operations", "Engineering"])
    with col2:
        tags_input = st.text_input("Tags (comma-separated)", placeholder="policy, refund, returns")

    if st.button("Upload", type="primary", disabled=uploaded_file is None):
        with st.spinner("Uploading..."):
            try:
                response = httpx.post(
                    f"{API_BASE}/api/v1/documents",
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)},
                    data={"domain": domain, "tags": tags_input},
                    timeout=60,
                )
                if response.status_code == 202:
                    data = response.json()
                    st.success(f"Document accepted! ID: `{data['document_id']}`")
                    st.info("Ingestion is running in the background. Refresh the Manage tab to check status.")
                else:
                    st.error(f"Upload failed: {response.text}")
            except Exception as e:
                st.error(f"Connection error: {e}")

with tab_list:
    st.subheader("Indexed Documents")

    col_refresh, col_page = st.columns([1, 3])
    with col_refresh:
        if st.button("Refresh"):
            st.rerun()

    try:
        response = httpx.get(f"{API_BASE}/api/v1/documents", timeout=10)
        if response.status_code == 200:
            data = response.json()
            docs = data.get("items", [])
            total = data.get("total", 0)

            st.caption(f"Total: {total} document(s)")

            if docs:
                _status_icon = {
                    "indexed": "🟢",
                    "processing": "🟡",
                    "pending": "⏳",
                    "failed": "🔴",
                }
                for doc in docs:
                    icon = _status_icon.get(doc["status"], "⚪")
                    label = f"{icon} {doc['file_name']} — {doc['status'].upper()}"
                    with st.expander(label):
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Type", doc.get("file_type", "?").upper())
                        col2.metric("Pages", doc.get("page_count") or "—")
                        col3.metric("Domain", doc.get("domain") or "—")
                        if doc.get("tags"):
                            st.write("**Tags:**", ", ".join(doc["tags"]))
                        if doc.get("indexed_at"):
                            st.caption(f"Indexed: {doc['indexed_at']}")
                        st.caption(f"Created: {doc['created_at']}  |  ID: {doc['id']}")

                        _, col_d = st.columns([3, 1])
                        if col_d.button("Delete", key=f"delete_{doc['id']}", type="secondary"):
                            try:
                                del_response = httpx.delete(
                                    f"{API_BASE}/api/v1/documents/{doc['id']}", timeout=30
                                )
                                if del_response.status_code == 200:
                                    st.success("Document deleted.")
                                    time.sleep(0.5)
                                    st.rerun()
                                else:
                                    st.error(f"Delete failed: {del_response.text}")
                            except Exception as e:
                                st.error(f"Connection error: {e}")

                # Auto-refresh if any document is still being processed
                still_processing = any(
                    d["status"] in ("processing", "pending") for d in docs
                )
                if still_processing:
                    st.info("Some documents are still processing — refreshing in 5 seconds...")
                    time.sleep(5)
                    st.rerun()
            else:
                st.info("No documents yet. Upload some files in the Upload tab.")
        else:
            st.error(f"Failed to fetch documents: {response.status_code}")
    except Exception as e:
        st.error(f"Cannot connect to API: {e}")
