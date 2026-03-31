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
from flask import Flask, request, jsonify
from flask_cors import CORS

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
rag_chain = None


def get_mongo_client() -> MongoClient:
    global mongo_client
    if mongo_client is None:
        mongo_client = MongoClient(MONGODB_URI)
    return mongo_client


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

        context_text = "\n\n---\n\n".join(
            f"[Source: {doc.metadata.get('source', 'unknown')}, "
            f"Page: {doc.metadata.get('page', '?')}]\n{doc.page_content}"
            for doc in docs
        )

        sources = list(
            {
                f"{doc.metadata.get('source', 'unknown')} (p.{doc.metadata.get('page', '?')})"
                for doc in docs
            }
        )

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
