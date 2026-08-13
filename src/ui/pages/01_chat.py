import json
import os

import streamlit as st

from src.ui import http as httpx
from src.ui.auth import require_auth

st.set_page_config(page_title="Chat", layout="wide")

# Gate before rendering anything: Streamlit's multipage nav lists every
# page regardless of sign-in state, so each page must check for itself.
require_auth()
st.title("Chat")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []

FEEDBACK_TAGS = ["hallucinated", "wrong_source", "incomplete", "outdated", "irrelevant"]


def _load_conversation(conversation_id: str) -> None:
    """Replace the on-screen thread with a stored one.

    Messages have been persisted since the governance work, but nothing read
    them back -- so a refresh lost the user's history while it sat in the
    database. This is the read side.
    """
    try:
        r = httpx.get(f"{API_BASE}/api/v1/conversations/{conversation_id}", timeout=20)
        r.raise_for_status()
        st.session_state.messages = [
            {
                "role": m["role"],
                "content": m["content"],
                "citations": m.get("citations", []),
                "message_id": m["id"],
                "feedback_sent": True,  # historical turns are not re-rateable
            }
            for m in r.json()
        ]
        st.session_state.conversation_id = conversation_id
    except Exception as exc:
        st.sidebar.error(f"Could not load that conversation: {exc}")


def _send_feedback(message_id: str, rating: int, tags: list[str] | None = None) -> bool:
    try:
        r = httpx.post(
            f"{API_BASE}/api/v1/feedback",
            json={"message_id": message_id, "rating": rating, "tags": tags or []},
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


# Sidebar: settings + history
with st.sidebar:
    st.header("Settings")
    debug_mode = st.toggle("Debug mode (show retrieval)", value=False)
    if st.button("New Conversation", use_container_width=True):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("History")
    try:
        convos = httpx.get(f"{API_BASE}/api/v1/conversations", timeout=15).json()
    except Exception:
        convos = []

    if not convos:
        st.caption("No past conversations yet.")
    for convo in convos[:25]:
        label = convo["title"][:38] + ("..." if len(convo["title"]) > 38 else "")
        active = convo["id"] == st.session_state.conversation_id
        if st.button(
            f"{'▸ ' if active else ''}{label}",
            key=f"convo_{convo['id']}",
            use_container_width=True,
            help=f"{convo['message_count']} messages - {convo['updated_at'][:16].replace('T', ' ')}",
        ):
            _load_conversation(convo["id"])
            st.rerun()


# An empty chat with no guidance is the least useful screen in the product:
# a new user cannot tell what this thing knows or what to ask it.
if not st.session_state.messages:
    st.markdown(
        "#### Ask a question about your documents\n"
        "Answers come with citations to the exact passages used. Questions "
        "that need no documents — greetings, arithmetic, the date — are "
        "answered directly without a search."
    )
    st.caption("Try one of these:")
    examples = [
        "What is our refund policy?",
        "12 * 7",
        "What time is it?",
        "Summarise the key points of the latest policy document",
    ]
    cols = st.columns(len(examples))
    for col, example in zip(cols, examples):
        if col.button(example, use_container_width=True, key=f"eg_{example[:12]}"):
            st.session_state._pending_prompt = example
            st.rerun()

# Display the current thread
for index, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("citations"):
            with st.expander("Sources"):
                for cite in msg["citations"]:
                    st.markdown(f"**[{cite['index']}]** {cite.get('document_name', '')} — p.{cite.get('page_number', '?')}")

        # Feedback is the only signal reflecting whether an answer was
        # actually *useful*, as opposed to grounded or fast -- and a
        # thumbs-down is promoted into the golden dataset, so this is the
        # entry point of the whole evaluation feedback loop.
        if msg["role"] == "assistant" and msg.get("message_id") and not msg.get("feedback_sent"):
            col_up, col_down, col_rest = st.columns([1, 1, 10])
            if col_up.button("👍", key=f"up_{index}", help="This answer was helpful"):
                _send_feedback(msg["message_id"], rating=5)
                st.session_state.messages[index]["feedback_sent"] = True
                st.rerun()
            if col_down.button("👎", key=f"down_{index}", help="Something was wrong"):
                st.session_state.messages[index]["awaiting_tags"] = True
                st.rerun()

        if msg.get("awaiting_tags"):
            with st.form(key=f"fb_form_{index}"):
                st.caption("What went wrong? (optional — helps us fix it)")
                chosen = st.multiselect(
                    "Tags", FEEDBACK_TAGS, key=f"tags_{index}", label_visibility="collapsed"
                )
                note = st.text_input("Comment (optional)", key=f"note_{index}")
                if st.form_submit_button("Send feedback", type="primary"):
                    _send_feedback(msg["message_id"], rating=1, tags=chosen)
                    st.session_state.messages[index]["awaiting_tags"] = False
                    st.session_state.messages[index]["feedback_sent"] = True
                    st.rerun()

        if msg.get("feedback_sent") and msg["role"] == "assistant" and msg.get("message_id"):
            st.caption("Thanks — feedback recorded.")

# Chat input. An example click sets `_pending_prompt` and reruns, so the two
# entry points converge on the same handler below rather than duplicating it.
typed = st.chat_input("Ask a question about your documents...")
prompt = typed or st.session_state.pop("_pending_prompt", None)

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_answer = ""
        citations = []
        received_first_token = False
        meta: dict = {}

        # Show a thinking indicator immediately — retrieval can take several
        # seconds before the first token is streamed.
        placeholder.markdown("_Searching documents and thinking..._")

        try:
            with httpx.Client(timeout=120) as client, client.stream(
                "POST",
                f"{API_BASE}/api/v1/chat",
                json={
                    "query": prompt,
                    "conversation_id": st.session_state.conversation_id,
                    "stream": True,
                    "debug": debug_mode,
                },
            ) as response:
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")
                    if etype == "token":
                        if not received_first_token:
                            # First token — clear the thinking indicator
                            received_first_token = True
                        full_answer += event.get("content", "")
                        placeholder.markdown(full_answer + "▌")
                    elif etype == "done":
                        full_answer = event.get("answer", full_answer)
                        citations = event.get("citations", [])
                        meta = {
                            "route": event.get("route", "rag"),
                            "route_source": event.get("route_source", ""),
                            "grounded": event.get("grounded", True),
                            "cached": event.get("cached", False),
                            "refused": event.get("refused", False),
                            "trace_id": event.get("trace_id", ""),
                            "latency_ms": event.get("latency_ms"),
                        }
                        if event.get("conversation_id"):
                            st.session_state.conversation_id = event["conversation_id"]
                    elif etype == "error":
                        placeholder.error(f"Backend error: {event.get('message', 'unknown')}")
                        full_answer = f"Error: {event.get('message', 'unknown')}"

            placeholder.markdown(full_answer if full_answer else "_No response received._")

            # Make the router's decision visible. Without this an answer from
            # general knowledge is indistinguishable from one grounded in the
            # corpus, which is exactly the confusion a RAG system exists to
            # prevent.
            if meta:
                route = meta.get("route", "rag")
                badges = {
                    "greeting": "👋 greeting — no model call, no retrieval",
                    "calculator": "🧮 calculator — no model call",
                    "datetime": "🕐 clock — no model call",
                    "translation": "🌍 translation — no retrieval",
                    "llm_knowledge": "🧠 model knowledge — **not from your documents**",
                    "web_search": "🌐 web search — external source",
                    "sql": "🗄️ SQL — structured data",
                    "rag": "📚 document retrieval",
                }
                bits = [badges.get(route, route)]
                if meta.get("cached"):
                    bits.append("⚡ cached")
                if meta.get("refused"):
                    bits.append("🚫 refused by policy")
                if meta.get("latency_ms"):
                    bits.append(f"{meta['latency_ms']} ms")
                st.caption(" · ".join(bits))

                if not meta.get("grounded", True) and not meta.get("refused"):
                    st.warning(
                        "This answer did not come from your indexed documents, "
                        "so it carries no citations and is not verifiable against "
                        "your corpus.",
                        icon="⚠️",
                    )
                if meta.get("trace_id"):
                    st.caption(f"trace: `{meta['trace_id']}`")

            if citations:
                with st.expander("Sources"):
                    for cite in citations:
                        st.markdown(
                            f"**[{cite['index']}]** {cite.get('document_name', '')} "
                            f"— p.{cite.get('page_number', '?')}"
                        )

        except Exception as e:
            message = str(e)
            if "401" in message or "Unauthorized" in message:
                placeholder.error("Your session expired. Sign out and back in.")
            else:
                placeholder.error(f"Error connecting to API: {e}")
            full_answer = f"Error: {e}"

    st.session_state.messages.append({
        "role": "assistant",
        "content": full_answer,
        "citations": citations,
        # Carried from the `done` event so the feedback buttons below can
        # attach a rating to this specific answer. Without it, feedback could
        # only reference a trace id, and trace-only feedback cannot be
        # promoted into the golden dataset (promotion resolves the original
        # question through the message).
        "message_id": meta.get("message_id"),
        "route": meta.get("route"),
        "feedback_sent": False,
    })
    # Re-run so the freshly appended answer renders through the normal
    # message loop, which is what draws the feedback controls.
    st.rerun()
