import json
import os

import httpx
import streamlit as st

st.set_page_config(page_title="Chat", layout="wide")
st.title("Chat")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []

# Sidebar controls
with st.sidebar:
    st.header("Settings")
    debug_mode = st.toggle("Debug mode (show retrieval)", value=False)
    if st.button("New Conversation"):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.rerun()

# Display conversation history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("citations"):
            with st.expander("Sources"):
                for cite in msg["citations"]:
                    st.markdown(f"**[{cite['index']}]** {cite.get('document_name', '')} — p.{cite.get('page_number', '?')}")

# Chat input
if prompt := st.chat_input("Ask a question about your documents..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_answer = ""
        citations = []
        received_first_token = False

        # Show a thinking indicator immediately — retrieval can take several
        # seconds before the first token is streamed.
        placeholder.markdown("_Searching documents and thinking..._")

        try:
            with httpx.Client(timeout=120) as client:
                with client.stream(
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
                            if event.get("conversation_id"):
                                st.session_state.conversation_id = event["conversation_id"]
                        elif etype == "error":
                            placeholder.error(f"Backend error: {event.get('message', 'unknown')}")
                            full_answer = f"Error: {event.get('message', 'unknown')}"

            placeholder.markdown(full_answer if full_answer else "_No response received._")
            if citations:
                with st.expander("Sources"):
                    for cite in citations:
                        st.markdown(
                            f"**[{cite['index']}]** {cite.get('document_name', '')} "
                            f"— p.{cite.get('page_number', '?')}"
                        )

        except Exception as e:
            placeholder.error(f"Error connecting to API: {e}")
            full_answer = f"Error: {e}"

    st.session_state.messages.append({
        "role": "assistant",
        "content": full_answer,
        "citations": citations,
    })
