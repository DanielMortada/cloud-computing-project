"""
SmartStudy - Streamlit Chat UI
==============================
A web interface for the SmartStudy tutor.
Talks to the Chat API backend via HTTP.
Deployed on Cloud Run alongside the Chat API.
"""

import html
import os
import uuid

import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CHAT_API_URL = os.environ.get("CHAT_API_URL", "http://localhost:8080")
UPLOAD_TIMEOUT_SECONDS = int(os.environ.get("UPLOAD_TIMEOUT_SECONDS", "180"))
STATUS_POLL_INTERVAL_SECONDS = int(os.environ.get("STATUS_POLL_INTERVAL_SECONDS", "4"))
STATUS_REQUEST_TIMEOUT_SECONDS = int(
    os.environ.get("STATUS_REQUEST_TIMEOUT_SECONDS", "15")
)


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SmartStudy",
    page_icon="S",
    layout="centered",
)


def init_session_state():
    """Initialize Streamlit session state used by the app."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "uploaded_documents" not in st.session_state:
        st.session_state.uploaded_documents = []

    if "upload_feedback" not in st.session_state:
        st.session_state.upload_feedback = None

    if "document_status_error" not in st.session_state:
        st.session_state.document_status_error = None

    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0


def render_theme():
    """Apply the SmartStudy visual theme."""
    st.markdown(
        """
        <style>
        :root {
            --ss-bg: #f6f8fc;
            --ss-card: rgba(255, 255, 255, 0.88);
            --ss-card-strong: #ffffff;
            --ss-ink: #1f1f1f;
            --ss-muted: #5f6368;
            --ss-blue: #4285f4;
            --ss-red: #ea4335;
            --ss-yellow: #fbbc05;
            --ss-green: #34a853;
            --ss-border: rgba(66, 133, 244, 0.12);
            --ss-shadow: 0 18px 45px rgba(60, 64, 67, 0.12);
            --ss-radius: 24px;
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(66, 133, 244, 0.14), transparent 30%),
                radial-gradient(circle at top right, rgba(251, 188, 5, 0.18), transparent 28%),
                linear-gradient(180deg, #f7faff 0%, #f6f8fc 45%, #eef3fb 100%);
            color: var(--ss-ink);
        }

        [data-testid="stHeader"] {
            background: transparent;
        }

        [data-testid="stSidebar"] {
            background:
                linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(244, 248, 255, 0.96));
            border-right: 1px solid rgba(66, 133, 244, 0.08);
        }

        [data-testid="stSidebar"] > div:first-child {
            padding-top: 1.25rem;
        }

        .ss-hero {
            position: relative;
            overflow: hidden;
            padding: 1.75rem 1.75rem 1.5rem 1.75rem;
            border-radius: 28px;
            background:
                linear-gradient(135deg, rgba(66, 133, 244, 0.96), rgba(74, 112, 241, 0.92) 42%, rgba(52, 168, 83, 0.88) 100%);
            color: white;
            box-shadow: 0 22px 60px rgba(66, 133, 244, 0.22);
            animation: ssRise 0.7s ease-out;
        }

        .ss-hero::before,
        .ss-hero::after {
            content: "";
            position: absolute;
            border-radius: 999px;
            opacity: 0.22;
            filter: blur(4px);
        }

        .ss-hero::before {
            width: 220px;
            height: 220px;
            right: -40px;
            top: -70px;
            background: rgba(255, 255, 255, 0.42);
        }

        .ss-hero::after {
            width: 150px;
            height: 150px;
            left: -30px;
            bottom: -50px;
            background: rgba(251, 188, 5, 0.45);
        }

        .ss-eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            padding: 0.38rem 0.7rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.16);
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            backdrop-filter: blur(10px);
        }

        .ss-hero h1 {
            margin: 0.95rem 0 0.35rem 0;
            font-size: 2.35rem;
            line-height: 1.04;
            font-weight: 700;
            letter-spacing: -0.04em;
        }

        .ss-hero p {
            margin: 0;
            max-width: 38rem;
            font-size: 1rem;
            line-height: 1.6;
            color: rgba(255, 255, 255, 0.88);
        }

        .ss-summary-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.9rem;
            margin-top: 1.1rem;
        }

        .ss-summary-card,
        .ss-empty-state,
        .ss-section-shell,
        .ss-sidebar-card {
            border-radius: var(--ss-radius);
            background: var(--ss-card);
            border: 1px solid rgba(255, 255, 255, 0.65);
            box-shadow: var(--ss-shadow);
            backdrop-filter: blur(16px);
        }

        .ss-summary-card {
            padding: 1rem 1.05rem;
            animation: ssRise 0.8s ease-out;
        }

        .ss-summary-label {
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--ss-muted);
        }

        .ss-summary-value {
            margin-top: 0.15rem;
            font-size: 1.7rem;
            line-height: 1.1;
            font-weight: 700;
            color: var(--ss-ink);
        }

        .ss-summary-hint {
            margin-top: 0.25rem;
            font-size: 0.92rem;
            color: var(--ss-muted);
        }

        .ss-section-shell {
            padding: 1.2rem;
            margin-top: 1.1rem;
            animation: ssRise 0.85s ease-out;
        }

        .ss-section-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 0.9rem;
        }

        .ss-section-title h3 {
            margin: 0;
            font-size: 1.1rem;
            color: var(--ss-ink);
        }

        .ss-section-title p {
            margin: 0.2rem 0 0 0;
            color: var(--ss-muted);
            font-size: 0.94rem;
        }

        .ss-doc-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 0.95rem;
        }

        .ss-doc-card {
            position: relative;
            padding: 1rem;
            border-radius: 22px;
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid var(--ss-border);
            box-shadow: 0 10px 24px rgba(60, 64, 67, 0.08);
            transition: transform 180ms ease, box-shadow 180ms ease;
            animation: ssRise 0.75s ease-out;
        }

        .ss-doc-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 16px 30px rgba(60, 64, 67, 0.12);
        }

        .ss-doc-status {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            padding: 0.35rem 0.65rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
        }

        .ss-doc-status::before {
            content: "";
            width: 0.55rem;
            height: 0.55rem;
            border-radius: 999px;
        }

        .ss-status-ready {
            background: rgba(52, 168, 83, 0.12);
            color: #0f7a34;
        }

        .ss-status-ready::before {
            background: var(--ss-green);
        }

        .ss-status-processing {
            background: rgba(66, 133, 244, 0.12);
            color: #1b66d1;
        }

        .ss-status-processing::before {
            background: var(--ss-blue);
            animation: ssPulse 1.6s infinite;
        }

        .ss-status-not_found,
        .ss-status-invalid {
            background: rgba(234, 67, 53, 0.12);
            color: #b3261e;
        }

        .ss-status-not_found::before,
        .ss-status-invalid::before {
            background: var(--ss-red);
        }

        .ss-doc-name {
            margin: 0.8rem 0 0.35rem 0;
            font-size: 1rem;
            font-weight: 700;
            color: var(--ss-ink);
            word-break: break-word;
        }

        .ss-doc-detail,
        .ss-doc-meta {
            margin: 0;
            color: var(--ss-muted);
            line-height: 1.5;
            font-size: 0.9rem;
        }

        .ss-doc-meta {
            margin-top: 0.65rem;
            font-size: 0.8rem;
            color: #6f7378;
        }

        .ss-empty-state {
            padding: 1.4rem;
            margin-top: 1.1rem;
            animation: ssRise 0.8s ease-out;
        }

        .ss-empty-state h3 {
            margin: 0 0 0.35rem 0;
            color: var(--ss-ink);
        }

        .ss-empty-state p {
            margin: 0;
            color: var(--ss-muted);
            line-height: 1.6;
        }

        .ss-sidebar-card {
            padding: 1rem;
            margin-bottom: 0.95rem;
        }

        .ss-sidebar-card h3,
        .ss-sidebar-card h4 {
            margin: 0 0 0.45rem 0;
            color: var(--ss-ink);
        }

        .ss-sidebar-card p,
        .ss-sidebar-card li {
            color: var(--ss-muted);
            line-height: 1.55;
        }

        .ss-sidebar-card ol {
            padding-left: 1.15rem;
            margin: 0.35rem 0 0 0;
        }

        .stButton > button,
        .stDownloadButton > button {
            border-radius: 999px;
            border: 0;
            height: 2.9rem;
            font-weight: 600;
            box-shadow: 0 10px 22px rgba(66, 133, 244, 0.16);
            transition: transform 180ms ease, box-shadow 180ms ease;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 16px 28px rgba(66, 133, 244, 0.2);
        }

        .stFileUploader,
        [data-testid="stChatInput"] {
            border-radius: 24px;
        }

        [data-testid="stFileUploader"] section {
            border-radius: 24px;
            border: 1px dashed rgba(66, 133, 244, 0.35);
            background: rgba(255, 255, 255, 0.7);
        }

        [data-testid="stChatMessage"] {
            border-radius: 22px;
            background: rgba(255, 255, 255, 0.78);
            border: 1px solid rgba(66, 133, 244, 0.08);
            box-shadow: 0 12px 28px rgba(60, 64, 67, 0.07);
            padding: 0.2rem 0.35rem;
        }

        [data-testid="stChatInput"] textarea {
            min-height: 52px;
        }

        @keyframes ssRise {
            from {
                opacity: 0;
                transform: translateY(12px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes ssPulse {
            0%, 100% {
                transform: scale(1);
                opacity: 0.95;
            }
            50% {
                transform: scale(1.25);
                opacity: 0.45;
            }
        }

        @media (max-width: 768px) {
            .ss-hero {
                padding: 1.3rem;
            }

            .ss-hero h1 {
                font-size: 1.85rem;
            }

            .ss-summary-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def safe_json(response: requests.Response) -> dict:
    """Return a JSON body when available without raising parser errors."""
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def merge_uploaded_documents(new_documents: list[dict]):
    """Prepend newly uploaded documents and keep object names unique."""
    if not new_documents:
        return

    seen_object_names = {
        doc.get("object_name")
        for doc in new_documents
        if doc.get("object_name")
    }
    retained_documents = [
        doc
        for doc in st.session_state.uploaded_documents
        if doc.get("object_name") not in seen_object_names
    ]
    st.session_state.uploaded_documents = new_documents + retained_documents


def summarize_documents() -> dict:
    """Compute readiness counts for the current session's uploads."""
    documents = st.session_state.uploaded_documents
    total = len(documents)
    ready = sum(1 for doc in documents if doc.get("ready"))
    processing = sum(1 for doc in documents if doc.get("status") == "processing")
    issues = sum(
        1
        for doc in documents
        if doc.get("status") in {"not_found", "invalid"}
    )
    return {
        "total": total,
        "ready": ready,
        "processing": processing,
        "issues": issues,
    }


def has_pending_documents() -> bool:
    """Return True while at least one uploaded document is still processing."""
    return any(
        doc.get("status") == "processing"
        for doc in st.session_state.uploaded_documents
    )


def upload_selected_pdfs(uploaded_files) -> tuple[list[dict], list[str]]:
    """Upload a batch of PDFs through the existing API endpoint."""
    successful_uploads = []
    errors = []
    total_files = len(uploaded_files)
    progress = st.progress(0.0, text="Preparing upload batch.")

    for index, uploaded_pdf in enumerate(uploaded_files, start=1):
        progress.progress(
            (index - 1) / total_files,
            text=f"Uploading {index} of {total_files}: {uploaded_pdf.name}",
        )

        try:
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
            data = safe_json(response)

            if response.ok:
                object_name = data.get("object_name", "").strip()
                if not object_name:
                    errors.append(f"{uploaded_pdf.name}: upload response was missing object_name.")
                    continue

                successful_uploads.append(
                    {
                        "upload_id": data.get("upload_id", uuid.uuid4().hex),
                        "object_name": object_name,
                        "source_name": data.get("source_name") or os.path.basename(object_name),
                        "original_name": data.get("original_name") or uploaded_pdf.name,
                        "status": "processing",
                        "ready": False,
                        "chunk_count": 0,
                        "message": data.get(
                            "message",
                            "Upload complete. Waiting for ingestion to finish.",
                        ),
                        "checked_at": None,
                    }
                )
            else:
                error_message = data.get("error", "Upload failed.")
                detail = data.get("detail")
                if detail:
                    errors.append(f"{uploaded_pdf.name}: {error_message} ({detail})")
                else:
                    errors.append(f"{uploaded_pdf.name}: {error_message}")
        except requests.exceptions.ConnectionError:
            errors.append(f"{uploaded_pdf.name}: cannot reach the Chat API upload endpoint.")
        except Exception as exc:
            errors.append(f"{uploaded_pdf.name}: {exc}")

    progress.progress(1.0, text="Upload batch complete.")
    progress.empty()
    return successful_uploads, errors


def poll_document_statuses(force: bool = False):
    """Refresh document readiness from the Chat API."""
    all_documents = st.session_state.uploaded_documents
    if not all_documents:
        return

    target_documents = (
        all_documents
        if force
        else [doc for doc in all_documents if doc.get("status") == "processing"]
    )
    if not target_documents:
        return

    try:
        response = requests.post(
            f"{CHAT_API_URL}/documents/status",
            json={
                "documents": [
                    {
                        "object_name": doc.get("object_name"),
                        "source_name": doc.get("source_name"),
                    }
                    for doc in target_documents
                ]
            },
            timeout=STATUS_REQUEST_TIMEOUT_SECONDS,
        )
        data = safe_json(response)
        if not response.ok:
            detail = data.get("detail")
            error = data.get("error", "Status polling failed.")
            st.session_state.document_status_error = (
                f"{error} ({detail})" if detail else error
            )
            return

        status_by_object_name = {
            item.get("object_name"): item
            for item in data.get("documents", [])
            if item.get("object_name")
        }

        refreshed_documents = []
        for document in all_documents:
            object_name = document.get("object_name")
            refreshed_documents.append(
                {**document, **status_by_object_name.get(object_name, {})}
            )

        st.session_state.uploaded_documents = refreshed_documents
        st.session_state.document_status_error = None
    except requests.exceptions.ConnectionError:
        st.session_state.document_status_error = (
            "Cannot reach the Chat API status endpoint right now."
        )
    except Exception as exc:
        st.session_state.document_status_error = f"Status polling error: {exc}"


def render_hero():
    """Render the main hero section."""
    summary = summarize_documents()
    st.markdown(
        f"""
        <section class="ss-hero">
            <div class="ss-eyebrow">SmartStudy Workspace</div>
            <h1>Upload notes, watch ingestion live, then ask better questions.</h1>
            <p>
                Batch-upload your PDFs, track when each one is actually ready for retrieval,
                and keep the study flow moving in one clean interface.
            </p>
        </section>
        <div class="ss-summary-grid">
            <div class="ss-summary-card">
                <div class="ss-summary-label">Ready For Chat</div>
                <div class="ss-summary-value">{summary["ready"]}</div>
                <div class="ss-summary-hint">Documents fully indexed in MongoDB.</div>
            </div>
            <div class="ss-summary-card">
                <div class="ss-summary-label">Processing</div>
                <div class="ss-summary-value">{summary["processing"]}</div>
                <div class="ss-summary-hint">We keep checking until ingestion finishes.</div>
            </div>
            <div class="ss-summary-card">
                <div class="ss-summary-label">Session</div>
                <div class="ss-summary-value">{st.session_state.session_id[:8]}</div>
                <div class="ss-summary-hint">Chat history stays scoped to this study session.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_document_card(document: dict) -> str:
    """Return a styled HTML card for one uploaded PDF."""
    status = document.get("status", "processing")
    status_label = {
        "ready": "Ready for chat",
        "processing": "Ingesting",
        "not_found": "Missing",
        "invalid": "Invalid",
    }.get(status, "Checking")

    detail = document.get("message") or "Waiting for the latest ingestion status."
    meta_bits = []
    if document.get("chunk_count"):
        meta_bits.append(f'{document["chunk_count"]} indexed chunks')
    if document.get("checked_at"):
        meta_bits.append(f'checked {html.escape(str(document["checked_at"]))}')

    meta_text = " | ".join(meta_bits) if meta_bits else "Awaiting status refresh."

    return f"""
        <article class="ss-doc-card">
            <span class="ss-doc-status ss-status-{html.escape(status)}">{status_label}</span>
            <h4 class="ss-doc-name">{html.escape(document.get("source_name", "Untitled PDF"))}</h4>
            <p class="ss-doc-detail">{html.escape(detail)}</p>
            <p class="ss-doc-meta">{html.escape(meta_text)}</p>
        </article>
    """


def render_document_status_panel():
    """Render the uploaded document readiness area."""
    documents = st.session_state.uploaded_documents
    if not documents:
        st.markdown(
            """
            <section class="ss-empty-state">
                <h3>No PDFs uploaded yet</h3>
                <p>
                    Select one or more PDFs from the sidebar, send them in one batch,
                    and SmartStudy will mark each file ready as soon as ingestion lands.
                </p>
            </section>
            """,
            unsafe_allow_html=True,
        )
        return

    summary = summarize_documents()
    col_a, col_b = st.columns([5, 1])
    with col_a:
        st.markdown(
            f"""
            <div class="ss-section-title">
                <div>
                    <h3>Document readiness</h3>
                    <p>
                        {summary["ready"]} ready, {summary["processing"]} processing, {summary["issues"]} issues.
                    </p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_b:
        if st.button("Refresh", key="refresh_document_statuses", use_container_width=True):
            poll_document_statuses(force=True)

    if st.session_state.document_status_error:
        st.warning(st.session_state.document_status_error)

    cards = "".join(build_document_card(document) for document in documents)
    st.markdown(
        f"""
        <section class="ss-section-shell">
            <div class="ss-doc-grid">{cards}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_document_status_area():
    """Auto-refresh the status panel when ingestion is still running."""
    fragment = getattr(st, "fragment", None)
    pending_documents = has_pending_documents()

    if fragment is None or not pending_documents:
        if pending_documents:
            poll_document_statuses()
        render_document_status_panel()
        return

    @fragment(run_every=STATUS_POLL_INTERVAL_SECONDS)
    def document_status_fragment():
        if has_pending_documents():
            poll_document_statuses()
        render_document_status_panel()

    document_status_fragment()


def render_sidebar():
    """Render sidebar controls and upload flow."""
    with st.sidebar:
        st.markdown(
            """
            <section class="ss-sidebar-card">
                <h3>How it works</h3>
                <ol>
                    <li>Select one or more PDF lecture notes.</li>
                    <li>Upload the whole batch with one click.</li>
                    <li>Watch each file move from ingesting to ready.</li>
                    <li>Ask questions or type <code>/quiz</code> in the chat.</li>
                </ol>
            </section>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <section class="ss-sidebar-card">
                <h4>Upload PDFs</h4>
                <p>Each upload gets a unique cloud object name so files never overwrite each other.</p>
            </section>
            """,
            unsafe_allow_html=True,
        )

        uploaded_pdfs = st.file_uploader(
            "Choose one or more PDF documents",
            type=["pdf"],
            accept_multiple_files=True,
            key=f"pdf_uploader_{st.session_state.uploader_key}",
            label_visibility="collapsed",
        )

        if st.button("Upload selected PDFs", type="primary", use_container_width=True):
            if not uploaded_pdfs:
                st.session_state.upload_feedback = {
                    "kind": "warning",
                    "message": "Please choose at least one PDF first.",
                }
            else:
                with st.spinner(f"Uploading {len(uploaded_pdfs)} PDF(s) to SmartStudy."):
                    successful_uploads, errors = upload_selected_pdfs(uploaded_pdfs)

                if successful_uploads:
                    merge_uploaded_documents(successful_uploads)
                    st.session_state.uploader_key += 1
                    poll_document_statuses()

                if successful_uploads and not errors:
                    st.session_state.upload_feedback = {
                        "kind": "success",
                        "message": (
                            f"Queued {len(successful_uploads)} PDF(s). "
                            "Each card will switch to ready as soon as ingestion completes."
                        ),
                    }
                elif successful_uploads and errors:
                    st.session_state.upload_feedback = {
                        "kind": "warning",
                        "message": (
                            f"Uploaded {len(successful_uploads)} PDF(s), "
                            f"with {len(errors)} issue(s): {' | '.join(errors)}"
                        ),
                    }
                else:
                    st.session_state.upload_feedback = {
                        "kind": "error",
                        "message": " | ".join(errors) if errors else "Upload failed.",
                    }

                st.rerun()

        feedback = st.session_state.upload_feedback
        if feedback:
            if feedback["kind"] == "success":
                st.success(feedback["message"])
            elif feedback["kind"] == "warning":
                st.warning(feedback["message"])
            else:
                st.error(feedback["message"])

        st.divider()

        if st.button("Clear chat history", use_container_width=True):
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
            st.session_state.upload_feedback = None
            st.rerun()

        st.caption(f"Session: `{st.session_state.session_id[:8]}`")


def render_chat_history():
    """Render the conversation history."""
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def handle_chat_input():
    """Handle the chat interaction with the backend."""
    placeholder = "Ask a question about your lecture notes."
    if not st.session_state.uploaded_documents:
        placeholder = "Upload notes first for grounded answers, or ask a general question."
    elif has_pending_documents():
        placeholder = "Your notes are still ingesting. You can ask now, or wait for ready status."

    if prompt := st.chat_input(placeholder):
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
                    data = safe_json(response)
                    answer = data.get(
                        "answer",
                        "Sorry, I could not generate a response.",
                    )
                    sources = data.get("sources", [])

                    st.markdown(answer)

                    if sources:
                        with st.expander("Sources"):
                            for src in sources:
                                st.markdown(f"- {src}")
                except requests.exceptions.ConnectionError:
                    answer = "Cannot reach the Chat API. Is the backend running?"
                    st.error(answer)
                except Exception as exc:
                    answer = f"Error: {exc}"
                    st.error(answer)

        st.session_state.messages.append({"role": "assistant", "content": answer})


init_session_state()
render_theme()
render_sidebar()
render_hero()
render_document_status_area()
render_chat_history()
handle_chat_input()
