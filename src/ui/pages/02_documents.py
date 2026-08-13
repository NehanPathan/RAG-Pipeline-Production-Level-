import os
import time

import streamlit as st

from src.ui import http as httpx
from src.ui.auth import require_auth

st.set_page_config(page_title="Document Management", layout="wide")

# Gate before rendering anything: Streamlit's multipage nav lists every
# page regardless of sign-in state, so each page must check for itself.
require_auth()
st.title("Document Management")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

SENSITIVITY_ORDER = ["public", "internal", "confidential", "restricted"]
SENSITIVITY_HELP = {
    "public": "Anyone signed in can read it",
    "internal": "Analysts and above",
    "confidential": "Stewards and above",
    "restricted": "Admins only",
}


@st.cache_data(ttl=60)
def _my_clearance() -> str:
    """The caller's clearance, so the upload form cannot offer a level they
    are not allowed to create."""
    try:
        return httpx.get(f"{API_BASE}/api/v1/governance/policy", timeout=10).json()[
            "principal"
        ]["clearance"]
    except Exception:
        return "public"


tab_upload, tab_list = st.tabs(["Upload", "Manage"])

with tab_upload:
    st.subheader("Upload Document")
    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["pdf", "docx", "txt", "md", "html"],
        help="Max 100MB per file",
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        domain = st.selectbox("Domain", ["General", "HR", "Legal", "Finance", "Operations", "Engineering"])
    with col2:
        tags_input = st.text_input("Tags (comma-separated)", placeholder="policy, refund, returns")
    with col3:
        # Classification was previously not settable here at all, so every
        # upload silently defaulted to `internal`. A viewer (public clearance)
        # would upload a file, be told it succeeded, and then never see it in
        # the list or find it in chat -- with no error explaining why.
        clearance = _my_clearance()
        allowed = SENSITIVITY_ORDER[: SENSITIVITY_ORDER.index(clearance) + 1]
        sensitivity = st.selectbox(
            "Sensitivity",
            allowed,
            index=len(allowed) - 1,
            help="Who may retrieve this document. You cannot classify a "
            "document above your own clearance.",
        )
        st.caption(SENSITIVITY_HELP.get(sensitivity, ""))

    if clearance == "public":
        st.info(
            "Your clearance is **public**, so uploads are readable by anyone "
            "signed in. An administrator can raise your role to classify "
            "documents more narrowly."
        )

    if st.button("Upload", type="primary", disabled=uploaded_file is None):
        with st.spinner("Uploading and queueing for ingestion..."):
            try:
                response = httpx.post(
                    f"{API_BASE}/api/v1/documents",
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)},
                    data={
                        "domain": domain,
                        "tags": tags_input,
                        "sensitivity": sensitivity,
                    },
                    timeout=60,
                )
                if response.status_code == 202:
                    data = response.json()
                    st.success(f"Accepted as **{sensitivity}** — ID `{data['document_id']}`")
                    st.info(
                        "Ingestion runs in the background. Check the Manage tab; "
                        "the document is searchable once its status is `indexed`."
                    )
                elif response.status_code == 403:
                    st.error(
                        "Your role may not upload documents. Ask an "
                        "administrator for the analyst role or above."
                    )
                else:
                    st.error(f"Upload failed ({response.status_code}): {response.text[:200]}")
            except Exception as e:
                st.error(f"Connection error: {e}")

with tab_list:
    st.subheader("Indexed Documents")

    if "doc_page" not in st.session_state:
        st.session_state.doc_page = 1

    col_search, col_size, col_refresh = st.columns([4, 1, 1])
    search = col_search.text_input(
        "Search by file name",
        key="doc_search",
        placeholder="refund, handbook, contract...",
        label_visibility="collapsed",
    )
    page_size = col_size.selectbox("Per page", [10, 20, 50], index=1, label_visibility="collapsed")
    if col_refresh.button("Refresh", use_container_width=True):
        st.rerun()

    # A new search must return to page 1, otherwise a narrower result set
    # leaves you on a page that no longer exists and the list reads as empty.
    if st.session_state.get("_last_search") != search:
        st.session_state._last_search = search
        st.session_state.doc_page = 1

    try:
        response = httpx.get(
            f"{API_BASE}/api/v1/documents",
            params={
                "page": st.session_state.doc_page,
                "size": page_size,
                # Searching server-side rather than filtering the fetched page:
                # a client-side filter only ever searches the current page, so
                # a document on page 3 is unfindable.
                **({"search": search} if search else {}),
            },
            timeout=15,
        )
        if response.status_code == 200:
            data = response.json()
            docs = data.get("items", [])
            total = data.get("total", 0)
            pages = max(1, -(-total // page_size))  # ceiling division

            st.caption(
                f"{total} document(s) readable at your clearance "
                f"(**{_my_clearance()}**)"
                + (f" · matching “{search}”" if search else "")
                + (f" · page {st.session_state.doc_page} of {pages}" if pages > 1 else "")
            )

            if not docs and search:
                st.info(
                    f"No documents match “{search}”. Note this searches file "
                    "names — to search *inside* documents, ask a question in Chat."
                )
            elif not docs:
                st.info(
                    "No documents yet. Upload one on the **Upload** tab — it "
                    "becomes searchable in chat once its status is `indexed`."
                )

            if docs:
                _status_icon = {
                    "indexed": "🟢",
                    "processing": "🟡",
                    "pending": "⏳",
                    "failed": "🔴",
                }
                allowed_levels = SENSITIVITY_ORDER[
                    : SENSITIVITY_ORDER.index(_my_clearance()) + 1
                ]
                _sens_icon = {
                    "public": "🌐",
                    "internal": "🏢",
                    "confidential": "🔒",
                    "restricted": "⛔",
                }
                for doc in docs:
                    icon = _status_icon.get(doc["status"], "⚪")
                    sens = doc.get("sensitivity", "internal")
                    label = (
                        f"{icon} {doc['file_name']} — {doc['status'].upper()}"
                        f"  {_sens_icon.get(sens, '')} {sens}"
                    )
                    with st.expander(label):
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Type", doc.get("file_type", "?").upper())
                        col2.metric("Pages", doc.get("page_count") or "—")
                        col3.metric("Domain", doc.get("domain") or "—")
                        col4.metric("Sensitivity", sens)
                        if doc.get("retention_until"):
                            st.caption(f"Retained until: {doc['retention_until'][:10]}")
                        if doc.get("tags"):
                            st.write("**Tags:**", ", ".join(doc["tags"]))
                        if doc.get("indexed_at"):
                            st.caption(f"Indexed: {doc['indexed_at']}")
                        st.caption(f"Created: {doc['created_at']}  |  ID: {doc['id']}")

                        # Reclassification is the steward's core action and was
                        # API-only. It re-indexes every chunk across all stores
                        # and invalidates cached answers, so the label change
                        # actually changes behaviour rather than just the badge.
                        with st.form(key=f"reclass_{doc['id']}"):
                            rc1, rc2, rc3 = st.columns([2, 3, 1])
                            new_sens = rc1.selectbox(
                                "Reclassify to",
                                allowed_levels,
                                index=allowed_levels.index(sens) if sens in allowed_levels else 0,
                                key=f"sens_{doc['id']}",
                            )
                            reason = rc2.text_input(
                                "Reason (audited)", key=f"reason_{doc['id']}",
                                placeholder="Why is this changing?",
                            )
                            if rc3.form_submit_button("Apply") and new_sens != sens:
                                rr = httpx.put(
                                    f"{API_BASE}/api/v1/documents/{doc['id']}/classification",
                                    json={"sensitivity": new_sens, "reason": reason},
                                    timeout=60,
                                )
                                if rr.status_code == 200:
                                    st.success(f"Reclassified to {new_sens} and re-indexed.")
                                    st.rerun()
                                elif rr.status_code == 403:
                                    st.error("Reclassification requires the steward or admin role.")
                                else:
                                    st.error(f"Failed ({rr.status_code}): {rr.text[:150]}")

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

                if pages > 1:
                    st.divider()
                    prev_col, info_col, next_col = st.columns([1, 3, 1])
                    if prev_col.button(
                        "‹ Previous",
                        disabled=st.session_state.doc_page <= 1,
                        use_container_width=True,
                    ):
                        st.session_state.doc_page -= 1
                        st.rerun()
                    info_col.markdown(
                        f"<div style='text-align:center'>Page "
                        f"{st.session_state.doc_page} of {pages}</div>",
                        unsafe_allow_html=True,
                    )
                    if next_col.button(
                        "Next ›",
                        disabled=st.session_state.doc_page >= pages,
                        use_container_width=True,
                    ):
                        st.session_state.doc_page += 1
                        st.rerun()

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
