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
import re
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
from google.cloud import storage
from werkzeug.utils import secure_filename

from langchain_google_vertexai import ChatVertexAI, VertexAIEmbeddings
from langchain_core.messages import AIMessage, HumanMessage
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
MIN_CONTEXT_SIMILARITY = float(os.environ.get("MIN_CONTEXT_SIMILARITY", "0.35"))
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
storage_client: storage.Client | None = None
embeddings_model: VertexAIEmbeddings | None = None
rag_chain = None

SOCIAL_PROMPTS = {
    "hello",
    "hi",
    "hey",
    "hey there",
    "hello there",
    "good morning",
    "good afternoon",
    "good evening",
    "how are you",
    "how are you doing",
    "how are things",
    "thanks",
    "thank you",
    "who are you",
}


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


def normalize_session_id(raw_session_id: str) -> str:
    """Normalize a session id for safe path construction and storage."""
    return secure_filename((raw_session_id or "").strip())


def build_session_upload_prefix(session_id: str) -> str:
    """Return the session-scoped upload prefix in GCS."""
    clean_prefix = GCS_UPLOAD_PREFIX.strip("/")
    if clean_prefix:
        return f"{clean_prefix}/{session_id}"
    return session_id


def extract_session_id_from_object_name(object_name: str) -> str:
    """Extract the session folder immediately above the filename."""
    path_parts = [part for part in (object_name or "").split("/") if part]
    if len(path_parts) < 2:
        return ""
    return path_parts[-2]


def _display_source_name(source: str) -> str:
    """Return a user-facing filename for citations and status cards."""
    clean_source = (source or "").strip()
    return os.path.basename(clean_source) or clean_source or "unknown"


def _normalize_prompt_for_match(question: str) -> str:
    """Normalize a user prompt for simple intent classification."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", question.lower())).strip()


def _is_social_prompt(question: str) -> bool:
    """Return True for short social prompts that should not cite documents."""
    normalized = _normalize_prompt_for_match(question)
    if not normalized:
        return False
    if normalized in SOCIAL_PROMPTS:
        return True
    return any(
        normalized.startswith(prefix)
        for prefix in (
            "hello ",
            "hi ",
            "hey ",
            "how are you ",
            "thank you ",
            "thanks ",
        )
    )


def _build_social_response(question: str) -> str:
    """Return a polite, source-free reply for social prompts."""
    normalized = _normalize_prompt_for_match(question)
    if normalized.startswith("how are you"):
        return (
            "I'm doing well and I'm ready to help with your study materials. "
            "Upload lecture notes or ask a question whenever you want."
        )
    if normalized in {"who are you"}:
        return (
            "I'm SmartStudy, your academic tutor. I can help explain uploaded notes, "
            "summarize them, and generate study questions."
        )
    if normalized.startswith("thank"):
        return "You're welcome. Ask about your notes whenever you're ready."
    return "Hello. I'm ready to help with your study materials whenever you are."


def _store_direct_response(session_id: str, question: str, answer: str):
    """Persist a direct assistant reply outside the RAG chain."""
    history = get_session_history(session_id)
    history.add_messages(
        [
            HumanMessage(content=question),
            AIMessage(content=answer),
        ]
    )


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


def _sample_quiz_records(session_id: str, sample_size: int = 10) -> list[dict]:
    """Fetch a small random sample of indexed chunks for quiz generation."""
    collection = get_context_collection()
    pipeline = [
        {
            "$match": {
                "session_id": session_id,
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

        display_source = _display_source_name(source)
        context_parts.append(f"[Source: {display_source}, Page: {page}]\n{content}")
        source_labels.add(f"{display_source} (p.{page})")

    return "\n\n---\n\n".join(context_parts), sorted(source_labels)


def _source_label_parts(source_label: str) -> tuple[str, str]:
    """Split a UI source label into filename and page display parts."""
    match = re.match(r"^(?P<source>.+)\s+\(p\.(?P<page>[^)]+)\)$", source_label)
    if not match:
        return source_label.strip(), ""
    return match.group("source").strip(), match.group("page").strip()


def _regex_for_literal_text(value: str) -> str:
    """Build a regex pattern for literal text while tolerating whitespace differences."""
    escaped = re.escape(value.strip())
    return re.sub(r"\\\s+", r"\\s+", escaped)


def _page_reference_pattern(page: str) -> str:
    """Return citation regex variants for the page format shown in source labels."""
    escaped_page = re.escape(page.strip())
    return rf"(?:p\.?\s*{escaped_page}(?!\d)|pages?\s+{escaped_page}(?!\d))"


def _answer_mentions_source_page(answer: str, source_name: str, page: str) -> bool:
    """Return True when the final answer cites a specific source label."""
    if not answer or not source_name:
        return False

    source_pattern = _regex_for_literal_text(source_name)
    if not re.search(source_pattern, answer, flags=re.IGNORECASE):
        return False

    if not page or page == "?":
        return True

    page_pattern = _page_reference_pattern(page)
    return bool(
        re.search(
            rf"{source_pattern}.{{0,160}}{page_pattern}|{page_pattern}.{{0,160}}{source_pattern}",
            answer,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def filter_sources_to_answer_citations(answer: str, retrieved_sources: list[str]) -> list[str]:
    """
    Keep the source summary aligned with inline citations in the final answer.

    Retrieval can provide several chunks to the model, but the UI source
    expander should only list sources the model actually cited.
    """
    page_level_matches = []
    file_level_matches = []

    for source_label in retrieved_sources:
        source_name, page = _source_label_parts(source_label)
        if not source_name:
            continue
        source_pattern = _regex_for_literal_text(source_name)
        if not re.search(source_pattern, answer or "", flags=re.IGNORECASE):
            continue

        file_level_matches.append(source_label)
        if _answer_mentions_source_page(answer, source_name, page):
            page_level_matches.append(source_label)

    return page_level_matches or file_level_matches


def get_embeddings_model() -> VertexAIEmbeddings:
    """Return a shared Vertex AI embeddings client."""
    global embeddings_model
    if embeddings_model is None:
        embeddings_model = VertexAIEmbeddings(
            model_name=EMBEDDING_MODEL,
            project=GCP_PROJECT_ID,
            location=GCP_REGION,
        )
    return embeddings_model


def _session_chunk_filter(session_id: str) -> dict:
    """Return a MongoDB filter matching indexed chunks for one session."""
    return {
        "session_id": session_id,
        "textChunk": {"$exists": True, "$type": "string", "$ne": ""},
        "vectorEmbedding": {"$exists": True, "$type": "array"},
    }


def _cosine_similarity(query_vector: list[float], candidate_vector) -> float:
    """Compute cosine similarity between two embedding vectors."""
    if not isinstance(candidate_vector, list) or not candidate_vector:
        return float("-inf")

    dot_product = 0.0
    query_norm = 0.0
    candidate_norm = 0.0

    for query_value, candidate_value in zip(query_vector, candidate_vector):
        query_float = float(query_value)
        candidate_float = float(candidate_value)
        dot_product += query_float * candidate_float
        query_norm += query_float * query_float
        candidate_norm += candidate_float * candidate_float

    if query_norm <= 0.0 or candidate_norm <= 0.0:
        return float("-inf")

    return dot_product / ((query_norm ** 0.5) * (candidate_norm ** 0.5))


def _rank_session_records_by_similarity(
    question: str,
    session_id: str,
    limit: int = 5,
) -> list[dict]:
    """Rank one session's stored chunk vectors against the current question."""
    collection = get_context_collection()
    records = list(
        collection.find(
            _session_chunk_filter(session_id),
            {
                "_id": 0,
                "textChunk": 1,
                "vectorEmbedding": 1,
                "source": 1,
                "page": 1,
                "session_id": 1,
            },
        )
    )
    if not records:
        return []

    query_vector = get_embeddings_model().embed_query(question)
    scored_records = []

    for record in records:
        score = _cosine_similarity(query_vector, record.get("vectorEmbedding"))
        if score == float("-inf"):
            continue
        scored_records.append((score, record))

    if not scored_records:
        return []

    scored_records.sort(key=lambda item: item[0], reverse=True)
    best_score = scored_records[0][0]
    if best_score < MIN_CONTEXT_SIMILARITY:
        return []

    return [
        {key: value for key, value in record.items() if key != "vectorEmbedding"}
        for _, record in scored_records[:limit]
    ]


def retrieve_context_for_question(question: str, session_id: str) -> tuple[str, list[str]]:
    """Retrieve session-scoped context for standard chat and quiz mode."""
    if _is_quiz_command(question):
        sampled_records = _sample_quiz_records(session_id=session_id, sample_size=10)
        return _build_context_and_sources(sampled_records)

    ranked_records = _rank_session_records_by_similarity(question, session_id, limit=5)
    return _build_context_and_sources(ranked_records)


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


def build_upload_object_name(original_filename: str, session_id: str) -> str:
    """Create a unique object path for an uploaded PDF."""
    safe_name = secure_filename(original_filename)
    unique_suffix = uuid.uuid4().hex[:8]
    base_name, ext = os.path.splitext(safe_name)
    object_name = f"{base_name}-{unique_suffix}{ext}"
    return f"{build_session_upload_prefix(session_id)}/{object_name}"


def _document_source_filter(object_name: str, session_id: str | None = None) -> dict:
    """Build a MongoDB filter matching documents by their source path."""
    filter_doc = {"source": object_name}
    if session_id:
        filter_doc["session_id"] = session_id
    return filter_doc


def delete_vectors_for_source(object_name: str, session_id: str | None = None) -> int:
    """Delete all stored chunks for one uploaded document."""
    collection = get_context_collection()
    result = collection.delete_many(_document_source_filter(object_name, session_id=session_id))
    return result.deleted_count


def get_document_status(
    object_name: str,
    source_name: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Return ingestion readiness for a previously uploaded PDF."""
    checked_at = datetime.now(timezone.utc).isoformat()
    clean_object_name = (object_name or "").strip()
    label = source_name or _display_source_name(clean_object_name)

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

    if session_id and extract_session_id_from_object_name(clean_object_name) != session_id:
        return {
            "object_name": clean_object_name,
            "source_name": label or _display_source_name(clean_object_name),
            "status": "invalid",
            "ready": False,
            "chunk_count": 0,
            "exists_in_storage": None,
            "checked_at": checked_at,
            "message": "Document does not belong to the active session.",
        }

    collection = get_context_collection()
    chunk_count = collection.count_documents(
        _document_source_filter(clean_object_name, session_id=session_id)
    )

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
        "source_name": label or _display_source_name(clean_object_name),
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
                {"object_name": document, "source_name": _display_source_name(document)}
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


def summarize_document_statuses(statuses: list[dict]) -> dict:
    """Build the document-status summary payload used by the API."""
    ready_count = sum(1 for item in statuses if item["ready"])
    processing_count = sum(1 for item in statuses if item["status"] == "processing")
    not_found_count = sum(1 for item in statuses if item["status"] == "not_found")
    invalid_count = sum(1 for item in statuses if item["status"] == "invalid")
    return {
        "total": len(statuses),
        "ready": ready_count,
        "processing": processing_count,
        "not_found": not_found_count,
        "invalid": invalid_count,
        "all_ready": ready_count == len(statuses) and len(statuses) > 0,
    }


def list_session_documents(session_id: str) -> list[dict]:
    """List uploaded PDFs for one session directly from the storage namespace."""
    if not session_id or not GCS_BUCKET_NAME:
        return []

    bucket = get_storage_client().bucket(GCS_BUCKET_NAME)
    prefix = f"{build_session_upload_prefix(session_id).rstrip('/')}/"
    listed_documents = []

    for blob in bucket.list_blobs(prefix=prefix):
        if not blob.name or not blob.name.lower().endswith(".pdf"):
            continue
        listed_documents.append(
            get_document_status(
                object_name=blob.name,
                source_name=_display_source_name(blob.name),
                session_id=session_id,
            )
        )

    listed_documents.sort(key=lambda item: item.get("source_name", "").lower())
    return listed_documents


def delete_session_document(object_name: str, session_id: str) -> dict:
    """Delete one session-scoped PDF from storage and remove its indexed chunks."""
    clean_object_name = (object_name or "").strip()
    if not clean_object_name:
        raise ValueError("Missing object_name.")

    if extract_session_id_from_object_name(clean_object_name) != session_id:
        raise ValueError("Document does not belong to the active session.")

    bucket = get_storage_client().bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(clean_object_name)
    existed_in_storage = False

    try:
        existed_in_storage = blob.exists()
        if existed_in_storage:
            blob.delete()
    except Exception as exc:
        raise RuntimeError(f"Storage delete failed: {exc}") from exc

    deleted_vectors = delete_vectors_for_source(clean_object_name, session_id=session_id)
    return {
        "object_name": clean_object_name,
        "source_name": _display_source_name(clean_object_name),
        "session_id": session_id,
        "deleted_from_storage": existed_in_storage,
        "deleted_vectors": deleted_vectors,
    }


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
    session_id = normalize_session_id(body.get("session_id", "")) or "default"

    if not question:
        return jsonify({"error": "No question provided"}), 400

    try:
        if _is_social_prompt(question):
            answer = _build_social_response(question)
            _store_direct_response(session_id, question, answer)
            return jsonify({"answer": answer, "sources": []})

        # 1. Retrieve relevant context using a dedicated path for quiz mode.
        context_text, sources = retrieve_context_for_question(question, session_id)
        if not context_text:
            if _is_quiz_command(question):
                answer = (
                    "I don't have enough indexed material in the uploaded notes "
                    "to generate a quiz yet."
                )
            else:
                answer = "I don't have enough information in the uploaded notes to answer this."
            _store_direct_response(session_id, question, answer)
            return jsonify({"answer": answer, "sources": []})

        # 2. Run the RAG chain with conversation history
        chain = build_rag_chain()
        answer = chain.invoke(
            {"question": question, "context": context_text},
            config={"configurable": {"session_id": session_id}},
        )
        cited_sources = filter_sources_to_answer_citations(answer, sources)

        return jsonify({"answer": answer, "sources": cited_sources})

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

    session_id = normalize_session_id(request.form.get("session_id", ""))
    if not session_id:
        return jsonify({"error": "Missing session_id."}), 400

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

    object_name = build_upload_object_name(safe_name, session_id)
    upload_id = uuid.uuid4().hex

    try:
        client = get_storage_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(object_name)
        blob.metadata = {
            "session_id": session_id,
            "original_name": original_name,
        }
        blob.upload_from_string(file_bytes, content_type="application/pdf")

        return jsonify(
            {
                "status": "uploaded",
                "message": "PDF uploaded. Ingestion will start automatically.",
                "upload_id": upload_id,
                "session_id": session_id,
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


@app.route("/documents", methods=["GET"])
def list_documents():
    """GET /documents?session_id=... - Return this session's uploaded PDFs."""
    session_id = normalize_session_id(request.args.get("session_id", ""))
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    try:
        documents = list_session_documents(session_id)
        return jsonify(
            {
                "session_id": session_id,
                "documents": documents,
                "summary": summarize_document_statuses(documents),
            }
        )
    except Exception as exc:
        print(f"❌ Error in GET /documents: {exc}")
        return jsonify({"error": "Internal server error", "detail": str(exc)}), 500


@app.route("/documents", methods=["DELETE"])
def delete_document():
    """DELETE /documents?session_id=...&object_name=... - Remove one uploaded PDF."""
    session_id = normalize_session_id(request.args.get("session_id", ""))
    object_name = (request.args.get("object_name", "") or "").strip()

    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    if not object_name:
        return jsonify({"error": "Missing object_name"}), 400

    try:
        result = delete_session_document(object_name, session_id)
        return jsonify({"status": "deleted", **result})
    except ValueError as exc:
        return jsonify({"error": "Invalid delete request", "detail": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": "Delete failed", "detail": str(exc)}), 500
    except Exception as exc:
        print(f"❌ Error in DELETE /documents: {exc}")
        return jsonify({"error": "Internal server error", "detail": str(exc)}), 500


@app.route("/documents/status", methods=["POST"])
def document_status():
    """
    POST /documents/status
    Body: { "documents": [{ "object_name": "...", "source_name": "..." }] }
    Returns readiness for each uploaded document.
    """
    try:
        body = request.get_json(silent=True) or {}
        session_id = normalize_session_id(body.get("session_id", ""))
        documents = parse_status_documents(body)
        statuses = [
            get_document_status(
                object_name=document["object_name"],
                source_name=document.get("source_name"),
                session_id=session_id or None,
            )
            for document in documents
        ]

        return jsonify(
            {
                "session_id": session_id or None,
                "documents": statuses,
                "summary": summarize_document_statuses(statuses),
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
    session_id = normalize_session_id(request.args.get("session_id", "")) or "default"
    history = get_session_history(session_id)
    history.clear()
    return jsonify({"status": "cleared", "session_id": session_id})


@app.route("/history", methods=["GET"])
def read_history():
    """GET /history?session_id=... - Return stored chat messages for a session."""
    session_id = normalize_session_id(request.args.get("session_id", ""))
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
