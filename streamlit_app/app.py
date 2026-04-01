"""
SmartStudy - Streamlit Chat UI
================================
A web interface for the SmartStudy tutor.
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
UPLOAD_TIMEOUT_SECONDS = int(os.environ.get("UPLOAD_TIMEOUT_SECONDS", "180"))

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SmartStudy",
    page_icon="S",
    layout="centered",
)

st.title("SmartStudy")
st.caption("Your AI-powered academic tutor. Ask questions about your lecture notes.")

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
    st.header("How to use")
    st.markdown(
        """
        1. Upload one or more PDFs from this panel.
        2. Wait for ingestion to complete.
        3. Ask questions in the chat box.
        4. Type `/quiz` to generate a quiz from your notes.
        """
    )

    st.subheader("Upload PDF")
    uploaded_pdf = st.file_uploader(
        "Choose a PDF document",
        type=["pdf"],
        accept_multiple_files=False,
    )

    if st.button("Upload PDF", use_container_width=True):
        if uploaded_pdf is None:
            st.warning("Please choose a PDF first.")
        else:
            try:
                with st.spinner("Uploading file to SmartStudy cloud storage."):
                    files = {
                        "file": (
                            uploaded_pdf.name,
                            uploaded_pdf.getvalue(),
                            "application/pdf",
                        )
                    }
                    response = requests.post(
                        f"{CHAT_API_URL}/upload",
                        files=files,
                        timeout=UPLOAD_TIMEOUT_SECONDS,
                    )

                data = response.json()
                if response.ok:
                    st.success(f"Uploaded: {data.get('source_name', uploaded_pdf.name)}")
                    st.info(
                        "Ingestion starts automatically. Wait about 20-90 seconds before querying."
                    )
                else:
                    error_message = data.get("error", "Upload failed.")
                    detail = data.get("detail", "")
                    if detail:
                        st.error(f"{error_message} ({detail})")
                    else:
                        st.error(error_message)
            except requests.exceptions.ConnectionError:
                st.error("Cannot reach the Chat API upload endpoint.")
            except Exception as e:
                st.error(f"Upload error: {e}")

    st.divider()

    if st.button("Clear chat history"):
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
    st.caption(f"Session: `{st.session_state.session_id[:8]}`")

# ---------------------------------------------------------------------------
# Chat history display
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# User input
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Ask a question about your lecture notes."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking."):
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
                answer = data.get("answer", "Sorry, I could not generate a response.")
                sources = data.get("sources", [])

                st.markdown(answer)

                if sources:
                    with st.expander("Sources"):
                        for src in sources:
                            st.markdown(f"- {src}")
            except requests.exceptions.ConnectionError:
                answer = "Cannot reach the Chat API. Is the backend running?"
                st.error(answer)
            except Exception as e:
                answer = f"Error: {e}"
                st.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
