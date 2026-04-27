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
from textwrap import dedent

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
HISTORY_REQUEST_TIMEOUT_SECONDS = int(
    os.environ.get("HISTORY_REQUEST_TIMEOUT_SECONDS", "15")
)


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SmartStudy",
    page_icon="S",
    layout="wide",
    initial_sidebar_state="expanded",
)


def normalize_chat_role(raw_role: str) -> str:
    """Map backend message roles to Streamlit chat roles."""
    role = (raw_role or "").strip().lower()
    if role in {"human", "user"}:
        return "user"
    if role in {"ai", "assistant"}:
        return "assistant"
    if role == "system":
        return "assistant"
    return "assistant"


def read_query_session_id() -> str:
    """Read the session id from query params when available."""
    raw_value = st.query_params.get("sid", "")
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    return str(raw_value).strip()


def sync_query_session_id(session_id: str):
    """Keep the browser URL aligned with the active session id."""
    if read_query_session_id() != session_id:
        st.query_params["sid"] = session_id


def init_session_state():
    """Initialize Streamlit session state used by the app."""
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "history_hydrated" not in st.session_state:
        st.session_state.history_hydrated = False

    if "history_error" not in st.session_state:
        st.session_state.history_error = None

    if "documents_hydrated" not in st.session_state:
        st.session_state.documents_hydrated = False

    query_session_id = read_query_session_id()
    if "session_id" not in st.session_state:
        st.session_state.session_id = query_session_id or str(uuid.uuid4())
    elif query_session_id and query_session_id != st.session_state.session_id:
        # User opened a different session id from URL.
        st.session_state.session_id = query_session_id
        st.session_state.messages = []
        st.session_state.history_hydrated = False
        st.session_state.history_error = None
        st.session_state.uploaded_documents = []
        st.session_state.documents_hydrated = False
        st.session_state.document_status_error = None
        st.session_state.upload_feedback = None
        if "uploader_key" in st.session_state:
            st.session_state.uploader_key += 1

    if "uploaded_documents" not in st.session_state:
        st.session_state.uploaded_documents = []

    if "upload_feedback" not in st.session_state:
        st.session_state.upload_feedback = None

    if "document_status_error" not in st.session_state:
        st.session_state.document_status_error = None

    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    sync_query_session_id(st.session_state.session_id)


def render_theme():
    """Apply the SmartStudy visual theme."""
    st.markdown(
        """
        <style>
        :root {
            --ss-accent: #5865F2;
            --ss-accent-light: rgba(88, 101, 242, 0.1);
            --ss-ink: #111827;
            --ss-muted: #6b7280;
            --ss-bg: #fafafa;
            --ss-card: #ffffff;
            --ss-border: #e5e7eb;
            --ss-green: #16a34a;
            --ss-green-bg: rgba(22, 163, 74, 0.1);
            --ss-red: #dc2626;
            --ss-red-bg: rgba(220, 38, 38, 0.1);
            --ss-shadow-sm: 0 1px 3px rgba(0,0,0,0.07), 0 4px 12px rgba(0,0,0,0.04);
            --ss-radius: 12px;
        }

        .stApp {
            background: var(--ss-bg);
            color: var(--ss-ink);
        }

        [data-testid="stHeader"] { background: transparent; }

        [data-testid="stSidebar"] {
            background: var(--ss-card);
            border-right: 1px solid var(--ss-border);
        }

        /* ---- Brand ---- */
        .ss-brand {
            display: flex;
            align-items: center;
            gap: 0.65rem;
            padding: 0.25rem 0 0.9rem 0;
        }
        .ss-brand-icon { font-size: 1.75rem; }
        .ss-brand-text h2 {
            margin: 0;
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--ss-ink);
            line-height: 1.2;
        }
        .ss-brand-text span {
            font-size: 0.75rem;
            color: var(--ss-muted);
            letter-spacing: 0.03em;
        }

        /* ---- Status badges ---- */
        .ss-status-badges {
            display: flex;
            gap: 0.45rem;
            flex-wrap: wrap;
            margin-bottom: 0.5rem;
        }
        .ss-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.3rem;
            padding: 0.25rem 0.6rem;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 600;
            background: #f3f4f6;
            color: var(--ss-muted);
        }
        .ss-badge.ready  { background: var(--ss-green-bg); color: var(--ss-green); }
        .ss-badge.processing { background: var(--ss-accent-light); color: var(--ss-accent); }

        /* ---- Tabs ---- */
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: 0.25rem;
            border-bottom: 2px solid var(--ss-border);
        }
        [data-testid="stTabs"] [data-baseweb="tab"] {
            font-weight: 600;
            font-size: 0.95rem;
            padding: 0.6rem 1.1rem;
            border-radius: var(--ss-radius) var(--ss-radius) 0 0;
            color: var(--ss-muted);
        }
        [data-testid="stTabs"] [aria-selected="true"] {
            color: var(--ss-accent);
            border-bottom: 2px solid var(--ss-accent);
        }

        /* ---- Chat welcome state ---- */
        .ss-welcome {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 5rem 1rem 3rem 1rem;
            text-align: center;
            gap: 0.65rem;
        }
        .ss-welcome-icon { font-size: 3.5rem; }
        .ss-welcome h2 {
            margin: 0;
            font-size: 1.6rem;
            font-weight: 700;
            color: var(--ss-ink);
        }
        .ss-welcome p {
            margin: 0;
            color: var(--ss-muted);
            font-size: 1rem;
            max-width: 32rem;
            line-height: 1.6;
        }
        .ss-quiz-tip {
            display: inline-block;
            padding: 0.35rem 0.85rem;
            background: var(--ss-accent-light);
            color: var(--ss-accent);
            border-radius: 999px;
            font-size: 0.82rem;
            font-weight: 600;
            margin-top: 0.4rem;
        }

        /* ---- Document cards ---- */
        .ss-doc-card {
            padding: 1rem 1.1rem;
            border-radius: var(--ss-radius);
            background: var(--ss-card);
            border: 1px solid var(--ss-border);
            box-shadow: var(--ss-shadow-sm);
            transition: border-color 160ms ease, box-shadow 160ms ease;
            animation: ssRise 0.5s ease-out;
        }
        .ss-doc-card:hover {
            border-color: var(--ss-accent);
            box-shadow: 0 4px 16px rgba(88, 101, 242, 0.12);
        }
        .ss-doc-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
            gap: 0.9rem;
            margin-top: 0.75rem;
        }
        .ss-doc-status {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.28rem 0.6rem;
            border-radius: 999px;
            font-size: 0.74rem;
            font-weight: 600;
        }
        .ss-doc-status::before {
            content: "";
            width: 0.5rem;
            height: 0.5rem;
            border-radius: 999px;
        }
        .ss-status-ready    { background: var(--ss-green-bg); color: var(--ss-green); }
        .ss-status-ready::before { background: var(--ss-green); }
        .ss-status-processing { background: var(--ss-accent-light); color: var(--ss-accent); }
        .ss-status-processing::before { background: var(--ss-accent); animation: ssPulse 1.6s infinite; }
        .ss-status-not_found,
        .ss-status-invalid  { background: var(--ss-red-bg); color: var(--ss-red); }
        .ss-status-not_found::before,
        .ss-status-invalid::before { background: var(--ss-red); }
        .ss-doc-name {
            margin: 0.65rem 0 0.25rem 0;
            font-size: 0.95rem;
            font-weight: 600;
            color: var(--ss-ink);
            word-break: break-word;
        }
        .ss-doc-detail { margin: 0; color: var(--ss-muted); font-size: 0.85rem; line-height: 1.5; }
        .ss-doc-meta   { margin: 0.5rem 0 0 0; color: #9ca3af; font-size: 0.75rem; }

        /* ---- Empty state ---- */
        .ss-empty-state {
            padding: 2rem 1.5rem;
            border-radius: var(--ss-radius);
            background: var(--ss-card);
            border: 1px dashed var(--ss-border);
            text-align: center;
            margin-top: 0.75rem;
            animation: ssRise 0.5s ease-out;
        }
        .ss-empty-state h3 { margin: 0 0 0.35rem 0; color: var(--ss-ink); font-size: 1rem; }
        .ss-empty-state p  { margin: 0; color: var(--ss-muted); font-size: 0.9rem; line-height: 1.6; }

        /* ---- Section shell (document area wrapper) ---- */
        .ss-section-shell { margin-top: 0.5rem; }

        /* ---- Buttons ---- */
        .stButton > button {
            border-radius: var(--ss-radius);
            font-weight: 600;
            transition: opacity 160ms ease, transform 120ms ease;
        }
        .stButton > button:hover { opacity: 0.88; transform: translateY(-1px); }

        /* ---- Chat messages ---- */
        [data-testid="stChatMessage"] {
            border-radius: var(--ss-radius);
            background: var(--ss-card);
            border: 1px solid var(--ss-border);
            box-shadow: var(--ss-shadow-sm);
            padding: 0.15rem 0.3rem;
        }

        /* ---- Chat input ---- */
        [data-testid="stChatInput"] textarea { min-height: 48px; }

        /* ---- File uploader ---- */
        [data-testid="stFileUploader"] section {
            border-radius: var(--ss-radius);
            border: 1.5px dashed var(--ss-border);
            background: #f9fafb;
        }

        /* ---- Animations ---- */
        @keyframes ssRise {
            from { opacity: 0; transform: translateY(8px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes ssPulse {
            0%, 100% { transform: scale(1); opacity: 1; }
            50%       { transform: scale(1.3); opacity: 0.4; }
        }

        /* ---- Responsive ---- */
        @media (max-width: 768px) {
            .ss-doc-grid { grid-template-columns: 1fr; }
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


def hydrate_chat_history_once():
    """Load persisted chat history for this session exactly once per page load."""
    if st.session_state.history_hydrated:
        return

    try:
        response = requests.get(
            f"{CHAT_API_URL}/history",
            params={"session_id": st.session_state.session_id},
            timeout=HISTORY_REQUEST_TIMEOUT_SECONDS,
        )
        data = safe_json(response)
        if response.ok:
            hydrated_messages = []
            for item in data.get("messages", []):
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content", ""))
                if not content:
                    continue
                role = normalize_chat_role(str(item.get("role", "")))
                hydrated_messages.append({"role": role, "content": content})
            st.session_state.messages = hydrated_messages
            st.session_state.history_error = None
        else:
            detail = data.get("detail")
            error = data.get("error", "History restoration failed.")
            st.session_state.history_error = (
                f"{error} ({detail})" if detail else error
            )
    except requests.exceptions.ConnectionError:
        st.session_state.history_error = (
            "Cannot reach the Chat API history endpoint right now."
        )
    except Exception as exc:
        st.session_state.history_error = f"History load error: {exc}"
    finally:
        st.session_state.history_hydrated = True


def hydrate_documents_once():
    """Load this session's uploaded-document state exactly once per page load."""
    if st.session_state.documents_hydrated:
        return

    try:
        response = requests.get(
            f"{CHAT_API_URL}/documents",
            params={"session_id": st.session_state.session_id},
            timeout=STATUS_REQUEST_TIMEOUT_SECONDS,
        )
        data = safe_json(response)
        if response.ok:
            hydrated_documents = []
            for item in data.get("documents", []):
                if not isinstance(item, dict):
                    continue
                object_name = str(item.get("object_name", "")).strip()
                if not object_name:
                    continue
                hydrated_documents.append(
                    {
                        "object_name": object_name,
                        "source_name": item.get("source_name") or os.path.basename(object_name),
                        "status": item.get("status", "processing"),
                        "ready": bool(item.get("ready", False)),
                        "chunk_count": item.get("chunk_count", 0),
                        "message": item.get(
                            "message",
                            "Upload complete. Waiting for ingestion to finish.",
                        ),
                        "checked_at": item.get("checked_at"),
                    }
                )

            st.session_state.uploaded_documents = hydrated_documents
            st.session_state.document_status_error = None
        else:
            detail = data.get("detail")
            error = data.get("error", "Document restoration failed.")
            st.session_state.document_status_error = (
                f"{error} ({detail})" if detail else error
            )
    except requests.exceptions.ConnectionError:
        st.session_state.document_status_error = (
            "Cannot reach the Chat API documents endpoint right now."
        )
    except Exception as exc:
        st.session_state.document_status_error = f"Document load error: {exc}"
    finally:
        st.session_state.documents_hydrated = True


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
                data={"session_id": st.session_state.session_id},
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
                "session_id": st.session_state.session_id,
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

    return dedent(
        f"""
        <article class="ss-doc-card">
            <span class="ss-doc-status ss-status-{html.escape(status)}">{status_label}</span>
            <h4 class="ss-doc-name">{html.escape(document.get("source_name", "Untitled PDF"))}</h4>
            <p class="ss-doc-detail">{html.escape(detail)}</p>
            <p class="ss-doc-meta">{html.escape(meta_text)}</p>
        </article>
        """
    ).strip()


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
        dedent(
            f"""
        <section class="ss-section-shell">
            <div class="ss-doc-grid">{cards}</div>
        </section>
            """
        ).strip(),
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
        summary = summarize_documents()
        badges = [
            f'<span class="ss-badge ready">✓ {summary["ready"]} ready</span>'
        ]
        if summary["processing"] > 0:
            badges.append(
                f'<span class="ss-badge processing">⏳ {summary["processing"]} processing</span>'
            )

        st.markdown(
            """
            <div class="ss-brand">
              <span class="ss-brand-icon">🎓</span>
              <div class="ss-brand-text">
                <h2>SmartStudy</h2>
                <span>AI Academic Tutor</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<div class="ss-status-badges">{"".join(badges)}</div>',
            unsafe_allow_html=True,
        )

        st.divider()
        st.markdown("**📄 Upload lecture notes**")

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

        if st.button("🔄 New Session", use_container_width=True):
            old_session_id = st.session_state.session_id
            try:
                requests.delete(
                    f"{CHAT_API_URL}/history",
                    params={"session_id": old_session_id},
                    timeout=10,
                )
            except Exception:
                pass

            st.session_state.messages = []
            st.session_state.uploaded_documents = []
            st.session_state.session_id = str(uuid.uuid4())
            sync_query_session_id(st.session_state.session_id)
            st.session_state.history_hydrated = True
            st.session_state.documents_hydrated = True
            st.session_state.history_error = None
            st.session_state.document_status_error = None
            st.session_state.upload_feedback = None
            st.session_state.uploader_key += 1
            st.rerun()

        st.caption(f"Session · `{st.session_state.session_id[:8]}`")

        with st.expander("ℹ️ How to use"):
            st.markdown(
                """
                1. Select one or more PDF lecture notes.
                2. Upload the whole batch with one click.
                3. Watch each file move from ingesting to ready.
                4. Ask questions or type `/quiz` in the chat.
                """
            )

        if st.session_state.history_error:
            st.info(st.session_state.history_error)


def render_chat_welcome():
    """Render the empty-state welcome screen inside the Chat tab."""
    summary = summarize_documents()
    if summary["ready"] > 0:
        subtitle = f"{summary['ready']} document(s) indexed and ready for retrieval."
    elif summary["processing"] > 0:
        subtitle = "Your notes are still ingesting — you can already ask questions."
    else:
        subtitle = "Upload lecture PDFs from the sidebar to get grounded answers."

    st.markdown(
        f"""
        <div class="ss-welcome">
            <div class="ss-welcome-icon">🎓</div>
            <h2>Ask your tutor anything</h2>
            <p>{subtitle}</p>
            <span class="ss-quiz-tip">💡 Type /quiz to generate a 5-question MCQ from your notes</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_chat_history():
    """Render the conversation history."""
    for msg in st.session_state.messages:
        role = normalize_chat_role(msg.get("role", "assistant"))
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))
            sources = msg.get("sources", [])
            if role == "assistant" and sources:
                with st.expander("Sources"):
                    for src in sources:
                        st.markdown(f"- {src}")


def handle_chat_input(chat_container=None):
    """Handle the chat interaction with the backend."""
    placeholder = "Ask a question about your lecture notes."
    if not st.session_state.uploaded_documents:
        placeholder = "Upload notes first for grounded answers, or ask a general question."
    elif has_pending_documents():
        placeholder = "Your notes are still ingesting. You can ask now, or wait for ready status."

    if prompt := st.chat_input(placeholder):
        st.session_state.messages.append({"role": "user", "content": prompt})
        render_target = chat_container if chat_container is not None else st.container()
        assistant_message_for_history = None
        assistant_sources = []

        with render_target:
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
                        assistant_sources = data.get("sources", [])
                        assistant_message_for_history = answer

                        st.markdown(answer)

                        if assistant_sources:
                            with st.expander("Sources"):
                                for src in assistant_sources:
                                    st.markdown(f"- {src}")
                    except requests.exceptions.ConnectionError:
                        answer = "Cannot reach the Chat API. Is the backend running?"
                        st.error(answer)
                    except Exception as exc:
                        answer = f"Error: {exc}"
                        st.error(answer)

        if assistant_message_for_history is not None:
            assistant_message = {
                "role": "assistant",
                "content": assistant_message_for_history,
            }
            if assistant_sources:
                assistant_message["sources"] = assistant_sources
            st.session_state.messages.append(assistant_message)


init_session_state()
hydrate_chat_history_once()
hydrate_documents_once()
render_theme()
render_sidebar()

tab_chat, tab_docs = st.tabs(["💬  Chat", "📚  Documents"])

with tab_chat:
    if not st.session_state.messages:
        render_chat_welcome()
    chat_history_container = st.container()
    with chat_history_container:
        render_chat_history()
    handle_chat_input(chat_history_container)

with tab_docs:
    st.markdown("### 📚 Your Study Materials")
    st.caption("Files move from *Ingesting* → *Ready* automatically after upload.")
    render_document_status_area()
