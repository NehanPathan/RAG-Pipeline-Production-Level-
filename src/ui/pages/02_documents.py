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
    if st.button("Refresh"):
        st.rerun()

    try:
        response = httpx.get(f"{API_BASE}/api/v1/documents", timeout=10)
        if response.status_code == 200:
            data = response.json()
            docs = data.get("items", [])
            if docs:
                for doc in docs:
                    status_color = {"indexed": "green", "processing": "orange", "failed": "red", "pending": "gray"}.get(doc["status"], "gray")
                    with st.expander(f"📄 {doc['file_name']} — :{status_color}[{doc['status'].upper()}]"):
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Type", doc.get("file_type", "?").upper())
                        col2.metric("Pages", doc.get("page_count", "?"))
                        col3.metric("Domain", doc.get("domain", "?"))
                        if doc.get("tags"):
                            st.write("**Tags:**", ", ".join(doc["tags"]))
                        st.caption(f"ID: {doc['id']}")
                        col_r, col_d = st.columns([1, 1])
                        if col_r.button("Reindex", key=f"reindex_{doc['id']}"):
                            st.warning("Reindex not yet connected.")
                        if col_d.button("Delete", key=f"delete_{doc['id']}", type="secondary"):
                            st.warning("Delete not yet connected.")
            else:
                st.info("No documents indexed yet. Upload some documents first.")
        else:
            st.error(f"Failed to fetch documents: {response.status_code}")
    except Exception as e:
        st.error(f"Cannot connect to API: {e}")
