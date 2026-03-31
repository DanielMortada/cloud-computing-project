"""
SmartStudy — Streamlit Chat UI
================================
A clean web interface for the SmartStudy tutor.
Talks to the Chat API backend via HTTP.
Deployed on Cloud Run alongside the Chat API.
"""

import os
import uuid
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CHAT_API_URL = os.environ.get("CHAT_API_URL", "http://localhost:8080")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SmartStudy 🎓",
    page_icon="🎓",
    layout="centered",
)

st.title("SmartStudy 🎓")
st.caption("Your AI-powered academic tutor — ask questions about your lecture notes.")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("📚 How to use")
    st.markdown(
        """
        1. **Upload PDFs** to the GCS bucket — they are ingested automatically.
        2. **Ask questions** about your lecture notes below.
        3. Type **`/quiz`** to get a 5-question quiz on the material.
        """
    )

    if st.button("🗑️ Clear chat history"):
        try:
            requests.delete(
                f"{CHAT_API_URL}/history",
                params={"session_id": st.session_state.session_id},
                timeout=10,
            )
        except Exception:
            pass
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

    st.divider()
    st.caption(f"Session: `{st.session_state.session_id[:8]}…`")

# ---------------------------------------------------------------------------
# Chat history display
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# User input
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Ask a question about your lecture notes…"):
    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call the Chat API
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                response = requests.post(
                    f"{CHAT_API_URL}/chat",
                    json={
                        "question": prompt,
                        "session_id": st.session_state.session_id,
                    },
                    timeout=60,
                )
                response.raise_for_status()
                data = response.json()
                answer = data.get("answer", "Sorry, I couldn't generate a response.")
                sources = data.get("sources", [])

                st.markdown(answer)

                if sources:
                    with st.expander("📖 Sources"):
                        for src in sources:
                            st.markdown(f"- {src}")

            except requests.exceptions.ConnectionError:
                answer = "⚠️ Cannot reach the Chat API. Is the backend running?"
                st.error(answer)
            except Exception as e:
                answer = f"⚠️ Error: {e}"
                st.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
