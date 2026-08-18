import os

import streamlit as st

from src.ui.auth import AUTH_ENABLED, require_auth
from src.ui.utils import APIError, api_get

PAGE_TITLE = os.getenv("STREAMLIT_PAGE_TITLE", "Enterprise RAG Platform")

st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Gate first, render second. Anything above this line is visible to a
# signed-out visitor.
session = require_auth()

st.title(f"🤖 {PAGE_TITLE}")
st.markdown("---")

# ── Live status ──────────────────────────────────────────────────────────────
health_checks: dict = {}
api_ok = False
doc_count = 0

try:
    health = api_get("/api/v1/health", timeout=5)
    health_checks = health.get("checks", {})
    api_ok = health.get("status") == "healthy"
except APIError:
    pass

try:
    doc_count = api_get("/api/v1/documents", params={"size": 1}).get("total", 0)
except APIError:
    # A 403 here is correct behaviour, not a failure: the count reflects only
    # documents this user's clearance permits, and a viewer may see zero.
    pass

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("API Status", "Online ✅" if api_ok else "Offline ❌")
col2.metric("Documents", doc_count)
col3.metric("PostgreSQL", health_checks.get("postgres", "—"))
col4.metric("Qdrant", health_checks.get("qdrant", "—"))
col5.metric("Redis", health_checks.get("redis", "—"))

st.markdown("---")

# ── Who you are ──────────────────────────────────────────────────────────────
if AUTH_ENABLED and session:
    try:
        principal = api_get("/api/v1/governance/policy")["principal"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Signed in as", principal.get("email") or session.email)
        c2.metric("Role", principal.get("role", "—"))
        c3.metric("Clearance", principal.get("clearance", "—"))

        if principal.get("role") == "viewer":
            st.info(
                "You have the **viewer** role, so retrieval returns only "
                "`public` documents. Signing in proves who you are; an "
                "administrator grants higher clearance separately."
            )
    except APIError as exc:
        st.warning(f"Could not read your principal: {exc}")
else:
    st.warning(
        "**Authentication is disabled** (`FIREBASE_ENABLED=false`). Identity "
        "comes from an unverified header and proves nothing — development only."
    )

st.markdown("---")
st.markdown(
    """
### Quick Start
1. **Documents** → upload PDFs, DOCX, TXT, MD, HTML — classify each on upload
2. **Chat** → ask questions; the router answers greetings and arithmetic
   without touching retrieval at all
3. **Retrieval Inspector** → see what was retrieved, and what your clearance withheld
4. **Evaluation** → offline golden-set scores and live-traffic judging
5. **Governance** → policy, risk register, audit trail, kill switches
6. **Admin** → model and retrieval configuration
"""
)
