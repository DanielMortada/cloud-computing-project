"""
SmartStudy — Chat API
======================
Flask service that handles the RAG chat loop:
  • Receives a user question
  • Retrieves relevant document chunks from MongoDB Atlas Vector Search
  • Sends context + question + chat history to Gemini 2.5 Flash
  • Returns the tutor's response

Deployed to Cloud Run (or run locally for development).
"""

import os
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
from google.cloud import storage
from werkzeug.utils import secure_filename

from langchain_google_vertexai import ChatVertexAI, VertexAIEmbeddings
from langchain_mongodb import MongoDBAtlasVectorSearch
from langchain_mongodb.chat_message_histories import MongoDBChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.output_parsers import StrOutputParser
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "smartstudy")
MONGODB_COLLECTION = os.environ.get("MONGODB_COLLECTION", "context")
MONGODB_CHAT_HISTORY_COLLECTION = os.environ.get("MONGODB_CHAT_HISTORY_COLLECTION", "chat_history")
MONGODB_VECTOR_INDEX_NAME = os.environ.get("MONGODB_VECTOR_INDEX_NAME", "vector_index")
EMBEDDING_MODEL = os.environ.get("VERTEX_AI_EMBEDDING_MODEL", "text-embedding-005")
LLM_MODEL = os.environ.get("VERTEX_AI_LLM_MODEL", "gemini-2.5-flash")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_REGION = os.environ.get("GCP_REGION", "europe-west1")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")
GCS_UPLOAD_PREFIX = os.environ.get("GCS_UPLOAD_PREFIX", "uploads")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
PORT = int(os.environ.get("PORT", 8080))

# ---------------------------------------------------------------------------
# System prompt — Formal Academic Tutor persona
# ---------------------------------------------------------------------------
TUTOR_SYSTEM_PROMPT = """You are **SmartStudy**, a Formal Academic Tutor. \
Your mission is to help university students prepare for their exams by \
answering questions grounded **exclusively** in the uploaded lecture notes.

Rules you MUST follow:
1. **Cite your sources**: Always mention the source filename and page number \
   when referencing material (e.g. "According to lecture3.pdf, p.5 …").
2. **Never hallucinate**: If the answer is not in the provided context, say \
   "I don't have enough information in the uploaded notes to answer this."
3. **Summarize clearly**: Use bullet points, numbered lists, or short \
   paragraphs. Prefer structured answers.
4. **Be pedagogical**: After answering, suggest a follow-up question or a \
   study tip to deepen understanding.
5. **Quiz mode**: When the user sends "/quiz", generate a 5-question \
   multiple-choice quiz based on the retrieved context, then evaluate the \
   student's answers in the follow-up messages.

Context from the lecture notes:
{context}
"""

# ---------------------------------------------------------------------------
# Initialise singletons
# ---------------------------------------------------------------------------
mongo_client: MongoClient | None = None
vector_store: MongoDBAtlasVectorSearch | None = None
storage_client: storage.Client | None = None
rag_chain = None


def _normalize_page_display(raw_page):
    """Normalize page metadata to a human-readable 1-based page string."""
    if raw_page is None:
        return "?"

    if isinstance(raw_page, bool):
        return "?"

    if isinstance(raw_page, int):
        # PyPDFLoader page metadata is usually zero-based.
        return str(raw_page + 1 if raw_page >= 0 else raw_page)

    if isinstance(raw_page, float):
        page_int = int(raw_page)
        return str(page_int + 1 if page_int >= 0 else page_int)

    if isinstance(raw_page, str):
        trimmed = raw_page.strip()
        if not trimmed:
            return "?"
        if trimmed.isdigit():
            page_int = int(trimmed)
            return str(page_int + 1 if page_int >= 0 else page_int)
        return trimmed

    return "?"


def _extract_source_and_page(doc):
    """Extract source and page from a LangChain Document's metadata."""
    metadata = doc.metadata or {}
    source = metadata.get("source", "unknown")
    raw_page = metadata.get("page")
    page_display = _normalize_page_display(raw_page)
    return source, page_display


def _extract_source_and_page_from_record(record: dict):
    """Extract source and page from a raw MongoDB document."""
    source = record.get("source", "unknown")
    raw_page = record.get("page")
    page_display = _normalize_page_display(raw_page)
    return source, page_display


def _is_quiz_command(question: str) -> bool:
    """Return True when the user is invoking quiz mode."""
    return question.strip().lower() == "/quiz"


def _sample_quiz_records(sample_size: int = 10) -> list[dict]:
    """Fetch a small random sample of indexed chunks for quiz generation."""
    collection = get_context_collection()
    pipeline = [
        {
            "$match": {
                "textChunk": {
                    "$exists": True,
                    "$type": "string",
                    "$ne": "",
                }
            }
        },
        {"$sample": {"size": sample_size}},
    ]
    return list(collection.aggregate(pipeline))


def _build_context_and_sources(retrieved_docs) -> tuple[str, list[str]]:
    """Normalize different retrieval outputs into prompt context and source labels."""
    context_parts = []
    source_labels = set()

    for item in retrieved_docs:
        if hasattr(item, "page_content"):
            source, page = _extract_source_and_page(item)
            content = item.page_content
        else:
            source, page = _extract_source_and_page_from_record(item)
            content = item.get("textChunk") or item.get("page_content") or ""

        if not content:
            continue

        context_parts.append(f"[Source: {source}, Page: {page}]\n{content}")
        source_labels.add(f"{source} (p.{page})")

    return "\n\n---\n\n".join(context_parts), sorted(source_labels)


def retrieve_context_for_question(question: str) -> tuple[str, list[str]]:
    """Retrieve context differently for standard chat and quiz mode."""
    if _is_quiz_command(question):
        sampled_records = _sample_quiz_records(sample_size=10)
        return _build_context_and_sources(sampled_records)

    vs = get_vector_store()
    docs = vs.similarity_search(question, k=5)
    return _build_context_and_sources(docs)


def get_mongo_client() -> MongoClient:
    global mongo_client
    if mongo_client is None:
        mongo_client = MongoClient(MONGODB_URI)
    return mongo_client


def get_storage_client() -> storage.Client:
    """Return a shared Google Cloud Storage client."""
    global storage_client
    if storage_client is None:
        if GCP_PROJECT_ID:
            storage_client = storage.Client(project=GCP_PROJECT_ID)
        else:
            storage_client = storage.Client()
    return storage_client


def get_context_collection():
    """Return the MongoDB collection that stores document chunks."""
    client = get_mongo_client()
    return client[MONGODB_DB_NAME][MONGODB_COLLECTION]


def get_vector_store() -> MongoDBAtlasVectorSearch:
    """Return the LangChain vector store backed by MongoDB Atlas."""
    global vector_store
    if vector_store is None:
        embeddings = VertexAIEmbeddings(
            model_name=EMBEDDING_MODEL,
            project=GCP_PROJECT_ID,
            location=GCP_REGION,
        )
        collection = get_context_collection()
        vector_store = MongoDBAtlasVectorSearch(
            collection=collection,
            embedding=embeddings,
            index_name=MONGODB_VECTOR_INDEX_NAME,
            text_key="textChunk",
            embedding_key="vectorEmbedding",
        )
    return vector_store


def get_session_history(session_id: str) -> MongoDBChatMessageHistory:
    """Return a MongoDB-backed chat history for the given session."""
    return MongoDBChatMessageHistory(
        connection_string=MONGODB_URI,
        database_name=MONGODB_DB_NAME,
        collection_name=MONGODB_CHAT_HISTORY_COLLECTION,
        session_id=session_id,
    )


def _normalize_history_role(raw_role: str) -> str:
    """Map backend role labels to Streamlit chat roles."""
    role = (raw_role or "").strip().lower()
    if role in {"human", "user"}:
        return "user"
    if role in {"ai", "assistant"}:
        return "assistant"
    if role == "system":
        return "system"
    return "assistant"


def _normalize_history_content(raw_content) -> str:
    """Convert stored message payloads to plain text."""
    if isinstance(raw_content, str):
        return raw_content
    if raw_content is None:
        return ""
    if isinstance(raw_content, list):
        parts = []
        for item in raw_content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    return str(raw_content)


def serialize_session_history(session_id: str) -> dict:
    """Return a JSON-friendly view of the stored chat history."""
    history = get_session_history(session_id)
    messages = []

    for message in history.messages:
        raw_role = getattr(message, "type", None) or getattr(message, "role", None) or ""
        raw_content = getattr(message, "content", "")
        role = _normalize_history_role(str(raw_role))
        content = _normalize_history_content(raw_content)
        messages.append({"role": role, "content": content})

    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
    }


def build_rag_chain():
    """Build the LangChain LCEL chain: retrieval → prompt → LLM → output."""
    global rag_chain
    if rag_chain is not None:
        return rag_chain

    llm = ChatVertexAI(
        model_name=LLM_MODEL,
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        temperature=0.3,
        max_output_tokens=8192,
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", TUTOR_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}"),
        ]
    )

    # Base chain (without history wiring)
    base_chain = prompt | llm | StrOutputParser()

    # Wrap with message history
    rag_chain = RunnableWithMessageHistory(
        base_chain,
        get_session_history,
        input_messages_key="question",
        history_messages_key="history",
    )
    return rag_chain


def build_upload_object_name(original_filename: str) -> str:
    """Create a unique object path for an uploaded PDF."""
    safe_name = secure_filename(original_filename)
    unique_suffix = uuid.uuid4().hex[:8]
    base_name, ext = os.path.splitext(safe_name)
    object_name = f"{base_name}-{unique_suffix}{ext}"
    if GCS_UPLOAD_PREFIX:
        object_name = f"{GCS_UPLOAD_PREFIX.strip('/')}/{object_name}"
    return object_name


def _document_source_filter(object_name: str) -> dict:
    """Build a MongoDB filter matching documents by their source path."""
    return {"source": object_name}


def get_document_status(object_name: str, source_name: str | None = None) -> dict:
    """Return ingestion readiness for a previously uploaded PDF."""
    checked_at = datetime.now(timezone.utc).isoformat()
    clean_object_name = (object_name or "").strip()
    label = source_name or os.path.basename(clean_object_name)

    if not clean_object_name:
        return {
            "object_name": "",
            "source_name": label or "unknown.pdf",
            "status": "invalid",
            "ready": False,
            "chunk_count": 0,
            "exists_in_storage": None,
            "checked_at": checked_at,
            "message": "Missing object_name.",
        }

    collection = get_context_collection()
    chunk_count = collection.count_documents(_document_source_filter(clean_object_name))

    exists_in_storage = None
    if GCS_BUCKET_NAME:
        try:
            bucket = get_storage_client().bucket(GCS_BUCKET_NAME)
            exists_in_storage = bucket.blob(clean_object_name).exists()
        except Exception as exc:
            print(f"Warning: could not verify storage status for {clean_object_name}: {exc}")

    if chunk_count > 0:
        status = "ready"
        message = "Ingestion complete. Ready for chat."
        ready = True
    elif exists_in_storage is False:
        status = "not_found"
        message = "File is not present in storage."
        ready = False
    else:
        status = "processing"
        message = "Upload received. Waiting for ingestion to finish."
        ready = False

    return {
        "object_name": clean_object_name,
        "source_name": label or os.path.basename(clean_object_name),
        "status": status,
        "ready": ready,
        "chunk_count": chunk_count,
        "exists_in_storage": exists_in_storage,
        "checked_at": checked_at,
        "message": message,
    }


def parse_status_documents(payload: dict) -> list[dict]:
    """Normalize the status endpoint request payload."""
    documents = payload.get("documents")
    if documents is None:
        single_object_name = payload.get("object_name") or payload.get("source")
        if single_object_name:
            documents = [
                {
                    "object_name": single_object_name,
                    "source_name": payload.get("source_name"),
                }
            ]

    if not isinstance(documents, list) or not documents:
        raise ValueError("Provide a non-empty 'documents' list or an 'object_name'.")

    normalized_documents: list[dict] = []
    for document in documents:
        if isinstance(document, str):
            normalized_documents.append(
                {"object_name": document, "source_name": os.path.basename(document)}
            )
            continue

        if not isinstance(document, dict):
            normalized_documents.append({"object_name": "", "source_name": "unknown.pdf"})
            continue

        normalized_documents.append(
            {
                "object_name": (
                    document.get("object_name")
                    or document.get("source")
                    or document.get("source_path")
                    or ""
                ),
                "source_name": document.get("source_name"),
            }
        )

    return normalized_documents


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "SmartStudy Chat API 🎓"})


@app.route("/chat", methods=["POST"])
def chat():
    """
    POST /chat
    Body: { "question": "...", "session_id": "..." }
    Returns: { "answer": "...", "sources": [...] }
    """
    body = request.get_json(silent=True) or {}
    question = body.get("question", "").strip()
    session_id = body.get("session_id", "default")

    if not question:
        return jsonify({"error": "No question provided"}), 400

    try:
        # 1. Retrieve relevant context using a dedicated path for quiz mode.
        context_text, sources = retrieve_context_for_question(question)

        # 2. Run the RAG chain with conversation history
        chain = build_rag_chain()
        answer = chain.invoke(
            {"question": question, "context": context_text},
            config={"configurable": {"session_id": session_id}},
        )

        return jsonify({"answer": answer, "sources": sources})

    except Exception as e:
        print(f"❌ Error in /chat: {e}")
        return jsonify({"error": "Internal server error", "detail": str(e)}), 500


@app.route("/upload", methods=["POST"])
def upload():
    """
    POST /upload (multipart/form-data)
    Body: file=<pdf>
    Returns: upload metadata and processing status hint.
    """
    if not GCS_BUCKET_NAME:
        return jsonify({"error": "GCS_BUCKET_NAME is not configured"}), 500

    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return jsonify({"error": "No file provided. Use form field name 'file'."}), 400

    original_name = (uploaded_file.filename or "").strip()
    if not original_name:
        return jsonify({"error": "Empty filename."}), 400

    safe_name = secure_filename(original_name)
    if not safe_name or not safe_name.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported."}), 400

    file_bytes = uploaded_file.read()
    if not file_bytes:
        return jsonify({"error": "Uploaded file is empty."}), 400

    max_upload_bytes = MAX_UPLOAD_MB * 1024 * 1024
    if len(file_bytes) > max_upload_bytes:
        return jsonify({"error": f"File too large. Max size is {MAX_UPLOAD_MB} MB."}), 413

    object_name = build_upload_object_name(safe_name)
    upload_id = uuid.uuid4().hex

    try:
        client = get_storage_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(object_name)
        blob.upload_from_string(file_bytes, content_type="application/pdf")

        return jsonify(
            {
                "status": "uploaded",
                "message": "PDF uploaded. Ingestion will start automatically.",
                "upload_id": upload_id,
                "bucket": GCS_BUCKET_NAME,
                "object_name": object_name,
                "source_name": os.path.basename(object_name),
                "original_name": original_name,
                "size_bytes": len(file_bytes),
                "status_poll_path": "/documents/status",
                "status_poll_after_seconds": 4,
            }
        )
    except Exception as e:
        print(f"? Error in /upload: {e}")
        return jsonify({"error": "Upload failed", "detail": str(e)}), 500


@app.route("/documents/status", methods=["POST"])
def document_status():
    """
    POST /documents/status
    Body: { "documents": [{ "object_name": "...", "source_name": "..." }] }
    Returns readiness for each uploaded document.
    """
    try:
        body = request.get_json(silent=True) or {}
        documents = parse_status_documents(body)
        statuses = [
            get_document_status(
                object_name=document["object_name"],
                source_name=document.get("source_name"),
            )
            for document in documents
        ]

        ready_count = sum(1 for item in statuses if item["ready"])
        processing_count = sum(1 for item in statuses if item["status"] == "processing")
        not_found_count = sum(1 for item in statuses if item["status"] == "not_found")
        invalid_count = sum(1 for item in statuses if item["status"] == "invalid")

        return jsonify(
            {
                "documents": statuses,
                "summary": {
                    "total": len(statuses),
                    "ready": ready_count,
                    "processing": processing_count,
                    "not_found": not_found_count,
                    "invalid": invalid_count,
                    "all_ready": ready_count == len(statuses) and len(statuses) > 0,
                },
            }
        )
    except ValueError as exc:
        return jsonify({"error": "Invalid status request", "detail": str(exc)}), 400
    except Exception as exc:
        print(f"❌ Error in /documents/status: {exc}")
        return (
            jsonify({"error": "Internal server error", "detail": str(exc)}),
            500,
        )


@app.route("/history", methods=["DELETE"])
def clear_history():
    """DELETE /history?session_id=... - Clear chat history for a session."""
    session_id = request.args.get("session_id", "default")
    history = get_session_history(session_id)
    history.clear()
    return jsonify({"status": "cleared", "session_id": session_id})


@app.route("/history", methods=["GET"])
def read_history():
    """GET /history?session_id=... - Return stored chat messages for a session."""
    session_id = request.args.get("session_id", "").strip()
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    try:
        return jsonify(serialize_session_history(session_id))
    except Exception as exc:
        print(f"❌ Error in GET /history: {exc}")
        return jsonify({"error": "Internal server error", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
