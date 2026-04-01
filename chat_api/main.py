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
from flask import Flask, request, jsonify
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
    """Support both legacy nested metadata and flattened metadata fields."""
    metadata = doc.metadata or {}
    nested = metadata.get("metadata", {})
    if not isinstance(nested, dict):
        nested = {}

    source = (
        metadata.get("source")
        or metadata.get("filename")
        or nested.get("source")
        or nested.get("filename")
        or "unknown"
    )

    # Prefer explicit pageNumber when available (new ingestion format).
    if metadata.get("pageNumber") is not None:
        page_display = str(metadata.get("pageNumber"))
    else:
        raw_page = (
            metadata.get("page")
            if metadata.get("page") is not None
            else nested.get("page")
        )
        page_display = _normalize_page_display(raw_page)

    return source, page_display


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


def get_vector_store() -> MongoDBAtlasVectorSearch:
    """Return the LangChain vector store backed by MongoDB Atlas."""
    global vector_store
    if vector_store is None:
        embeddings = VertexAIEmbeddings(
            model_name=EMBEDDING_MODEL,
            project=GCP_PROJECT_ID,
            location=GCP_REGION,
        )
        client = get_mongo_client()
        collection = client[MONGODB_DB_NAME][MONGODB_COLLECTION]
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
        max_output_tokens=2048,
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
        # 1. Retrieve relevant context from vector store
        vs = get_vector_store()
        docs = vs.similarity_search(question, k=5)

        context_parts = []
        source_labels = set()
        for doc in docs:
            source, page = _extract_source_and_page(doc)
            context_parts.append(
                f"[Source: {source}, Page: {page}]\n{doc.page_content}"
            )
            source_labels.add(f"{source} (p.{page})")

        context_text = "\n\n---\n\n".join(context_parts)
        sources = sorted(source_labels)

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

    unique_suffix = uuid.uuid4().hex[:8]
    base_name, ext = os.path.splitext(safe_name)
    object_name = f"{base_name}-{unique_suffix}{ext}"
    if GCS_UPLOAD_PREFIX:
        object_name = f"{GCS_UPLOAD_PREFIX.strip('/')}/{object_name}"

    try:
        client = get_storage_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(object_name)
        blob.upload_from_string(file_bytes, content_type="application/pdf")

        return jsonify(
            {
                "status": "uploaded",
                "message": "PDF uploaded. Ingestion will start automatically.",
                "bucket": GCS_BUCKET_NAME,
                "object_name": object_name,
                "source_name": os.path.basename(object_name),
                "size_bytes": len(file_bytes),
            }
        )
    except Exception as e:
        print(f"? Error in /upload: {e}")
        return jsonify({"error": "Upload failed", "detail": str(e)}), 500


@app.route("/history", methods=["DELETE"])
def clear_history():
    """DELETE /history?session_id=... — Clear chat history for a session."""
    session_id = request.args.get("session_id", "default")
    history = get_session_history(session_id)
    history.clear()
    return jsonify({"status": "cleared", "session_id": session_id})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
