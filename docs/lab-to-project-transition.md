# From Lab Baseline to SmartStudy

Last updated: 2026-05-04

This document summarizes how we moved from the downloaded `lab_code` baseline to the current SmartStudy project. The goal is to explain what we kept from the lab, what we changed, and why those changes were necessary when moving from a guided sandbox exercise to a live cloud application.

## 1. Starting Point: What the Lab Gave Us

The lab code was a useful architectural starting point rather than a finished version of our project. It introduced the core idea of a Retrieval-Augmented Generation application: documents are converted into vector embeddings, stored in MongoDB Atlas, retrieved at question time, and passed to Gemini through LangChain so that the answer can be grounded in private documents.

The downloaded lab project was organized as a Node.js application:

```text
lab_code/
  client/          Angular frontend
  server/          Express.js backend
  server/src/      server, database, and embedding-related files
  server/pdf_documents/
                   static sample insurance PDFs
```

The original domain was an insurance assistant. The frontend was Angular, the backend was Express.js, and the lab README described a manual embedding step using `npm run embed-documents` before running the application locally. In our downloaded workspace, some server-side implementation points were still scaffolded, such as the `/messages` endpoint and the embedding script, so we treated the lab primarily as a conceptual and architectural blueprint.

That blueprint was still valuable. It showed us the shape of a RAG system:

1. Load PDF documents.
2. Split document text into chunks.
3. Generate 768-dimensional embeddings using Vertex AI.
4. Store document chunks and embeddings in MongoDB Atlas.
5. Retrieve relevant chunks for a user question.
6. Give the retrieved context to Gemini.
7. Return an answer through a web application.

SmartStudy keeps this core logic, but rebuilds the system around our actual project mission: a cloud-native academic tutor where students upload their own lecture PDFs and immediately ask document-grounded questions.

## 2. What We Kept From the Lab

We kept the RAG pattern because it directly matched the project requirement for grounded answers. The lab made clear that the LLM should not answer only from its general training knowledge; it should receive relevant excerpts from the uploaded material at query time. This principle remains the center of SmartStudy.

We kept MongoDB Atlas as the persistent knowledge store because the lab demonstrated that MongoDB can store both the raw text chunks and their vector representations. In SmartStudy, MongoDB still stores the `context` collection used for retrieval, and we added a separate `chat_history` collection for conversation memory.

We kept Vertex AI for embeddings and Gemini for generation. The lab used Google Cloud AI services for both semantic representation and language generation. We preserved that cloud-native design, while updating the model configuration to the project stack: `text-embedding-005` for embeddings and `gemini-2.5-flash` for answer generation.

We kept LangChain as the orchestration layer. The lab introduced LangChain as the framework connecting document retrieval, prompts, and model calls. In SmartStudy, LangChain is still used in the Chat API for prompt construction, Gemini calls, output parsing, and message history integration. It is also used in the Cloud Function ingestion path for PDF loading and text splitting.

We kept the idea of a separate backend API. The lab separated the frontend from the server. SmartStudy also keeps this separation: the Streamlit UI does not talk directly to MongoDB, GCS, or Vertex AI. It only calls the Chat API over HTTP.

We also kept the idea of containerized deployment. The lab server had a Dockerfile, which introduced the principle of packaging a service with its runtime and startup command. In SmartStudy, this idea was expanded into two Cloud Run services: one for the Chat API and one for the Streamlit UI.

## 3. What We Changed and Why

### Domain and User Goal

The lab was an insurance chatbot over a fixed set of sample insurance PDFs. Our project is an academic tutor for arbitrary lecture PDFs uploaded by students.

This changed the system requirements. Instead of answering questions about a known static corpus, SmartStudy had to accept new files, isolate one student's session from another, show ingestion progress, cite filenames and pages, and support study-focused behavior such as explanations and quiz generation.

### Programming Stack

The lab used Angular and Express.js. We moved to Python for the backend and UI layers:

| Layer | Lab baseline | SmartStudy |
|---|---|---|
| Frontend | Angular | Streamlit |
| Backend API | Express.js / TypeScript | Flask / Python |
| Ingestion | Local/manual script concept | Python Gen2 Cloud Functions |
| Deployment | Local app and server Dockerfile | Cloud Run plus Cloud Functions |

This was not just a stylistic change. Python gave us a more direct path for the Google Cloud Function requirement, PDF processing with `PyPDFLoader`, and the Python LangChain ecosystem. Streamlit also allowed us to build a usable academic interface quickly without maintaining a full JavaScript frontend framework.

### Document Ingestion

The lab assumed a fixed folder of PDFs and a manual vectorization step. That is acceptable for a tutorial, but it did not satisfy the project requirement for cloud automation.

We replaced the manual ingestion model with an event-driven cloud pipeline:

```text
Student uploads PDF
  -> Chat API stores it in GCS
  -> GCS emits object.finalized event
  -> Eventarc routes event to Cloud Function
  -> Cloud Function parses, chunks, embeds, and inserts vectors
```

This change is one of the most important transitions from the lab to SmartStudy. In the lab, ingestion is something the developer runs. In SmartStudy, ingestion is something the cloud system performs automatically when a new PDF appears in the bucket.

### Storage Model

The lab worked with static local files. SmartStudy uses Google Cloud Storage as the source of truth for uploaded PDFs.

Uploaded documents are stored under session-scoped paths:

```text
uploads/<session_id>/<secure_filename>-<uuid8>.pdf
```

This allows each browser session to have its own document namespace. It also gives the ingestion and cleanup functions a stable object path to use as the `source` field in MongoDB.

We also added GCS object metadata:

```json
{
  "session_id": "...",
  "original_name": "lecture.pdf",
  "content_sha256": "...",
  "document_title_key": "lecture.pdf"
}
```

This metadata supports deduplication and same-title replacement, which the lab did not need because it worked with static documents.

### MongoDB Document Shape

The lab introduced MongoDB as the vector/context store. SmartStudy kept this idea but made the document shape more explicit and operational:

```json
{
  "textChunk": "chunk text",
  "vectorEmbedding": [0.012, -0.091, "..."],
  "source": "uploads/<session_id>/lecture-a1b2c3d4.pdf",
  "page": 3,
  "session_id": "<session_id>"
}
```

The added `source`, `page`, and `session_id` fields are essential for our application. They allow us to filter retrieval by session, display citations, check document readiness, delete vectors for a removed PDF, and avoid mixing different students' materials.

### Retrieval Strategy

The lab demonstrated retrieval as the core of RAG. We kept that principle but adapted it for session isolation.

In SmartStudy, the Chat API retrieves only chunks belonging to the active `session_id`. It then ranks those candidate chunks by cosine similarity against the embedded user question. This design is simple, transparent, and safe for the project scale because it avoids accidentally retrieving context from another session.

We also added a minimum similarity threshold. If the best retrieved chunk is weakly related to the question, the API returns a no-context answer instead of forcing an irrelevant citation.

### API Surface

The lab centered around a simple chat endpoint concept, with the frontend calling `/api/messages`. SmartStudy needed a broader backend because it manages a complete document lifecycle:

| Endpoint | Purpose |
|---|---|
| `GET /` | Health check |
| `POST /chat` | Ask a session-scoped RAG question |
| `POST /upload` | Upload a PDF into the session's GCS folder |
| `GET /documents` | Restore and refresh the session's document list |
| `DELETE /documents` | Remove one session document |
| `POST /documents/status` | Check ingestion readiness |
| `GET /history` | Restore chat history |
| `DELETE /history` | Clear chat history for a session |

This API design reflects the shift from a tutorial chatbot to an application with upload, ingestion, retrieval, status, deletion, and persistence.

### User Interface

The lab UI was an Angular insurance chat interface with a RAG toggle. We replaced it with a Streamlit academic tutor UI.

The RAG toggle was removed because SmartStudy is intended to be a grounded study assistant by default. Instead, the UI focuses on workflows students actually need:

- upload one or more PDFs;
- see which files are processing or ready;
- ask questions in a chat interface;
- inspect cited sources;
- delete documents;
- reopen the same session through the `sid` URL parameter;
- start a new clean session.

This made the UI less about demonstrating a RAG feature toggle and more about supporting a complete student workflow.

### Conversation Memory

The lab focused mainly on single-turn retrieval and generation. SmartStudy adds persistent conversation memory through MongoDB.

Each conversation is keyed by `session_id`, and the UI restores it with `GET /history` when the same session URL is reopened. This implements one of the advanced project features and makes the tutor feel continuous rather than stateless.

### Quiz Mode

The project asked for at least one advanced feature. We implemented multiple advanced features, including `/quiz`.

The lab had no dedicated quiz workflow. In SmartStudy, `/quiz` is handled as a special command. Instead of trying to vector-search for the literal string `/quiz`, the API samples indexed chunks from the active session and asks Gemini to generate a five-question multiple-choice quiz. This makes quiz mode grounded in the uploaded PDFs while avoiding an obviously poor retrieval query.

### Reliability and Cleanup

The lab did not need production-style cleanup because it used a fixed document set. SmartStudy needed cleanup because users can upload, replace, and delete PDFs.

We added:

- idempotent ingestion: delete old vectors for the same object before inserting fresh ones;
- object-existence guards: avoid processing or upserting vectors for PDFs deleted during ingestion;
- cleanup function: remove vectors when GCS emits an object deletion event;
- overwrite-race guard: skip cleanup if the same object path still exists after a delete event;
- reconciliation scan: remove stale MongoDB vectors for PDFs no longer present in GCS.

These changes are not extra decoration. They are what keep GCS and MongoDB aligned in an event-driven system.

### Deployment Model

The lab was mainly local-development oriented. SmartStudy was deployed as a live cloud system:

```text
Streamlit UI      -> Cloud Run service
Chat API          -> Cloud Run service
PDF ingestion     -> Gen2 Cloud Function
PDF cleanup       -> Gen2 Cloud Function
Uploaded PDFs     -> Google Cloud Storage
Vectors/history   -> MongoDB Atlas
AI services       -> Vertex AI
```

This deployment model matches the project requirement to move from the sandbox lab into a real cloud environment. It also separates responsibilities clearly: the UI renders the user experience, the API orchestrates application logic, the functions handle event-driven ingestion and cleanup, and managed cloud services handle storage, database, and AI inference.

## 4. Summary of Main Changes

| Area | Lab baseline | SmartStudy change | Reason |
|---|---|---|---|
| Domain | Insurance assistant | Academic tutor for lecture PDFs | Match the project mission |
| Documents | Static sample PDFs | Student-uploaded PDFs in GCS | Support real user content |
| Ingestion | Manual/local embedding step | Event-driven Cloud Function on GCS finalize | Meet cloud automation requirement |
| Cleanup | Not central | Delete-triggered cleanup function plus reconciliation | Keep MongoDB in sync with GCS |
| Backend | Express.js scaffold | Flask Chat API | Python integration with project stack |
| Frontend | Angular chat app | Streamlit study interface | Faster academic workflow implementation |
| API | Simple message endpoint concept | Upload, chat, documents, status, history endpoints | Support full document lifecycle |
| Retrieval | General RAG retrieval | Session-scoped cosine ranking | Prevent cross-session context leakage |
| Data model | Basic context records | `textChunk`, `vectorEmbedding`, `source`, `page`, `session_id` | Enable citations, status checks, and deletion |
| Memory | Not central | MongoDB `chat_history` by session | Implement conversation continuity |
| Advanced features | RAG demo toggle | `/quiz`, memory, web UI, source filtering | Satisfy and exceed advanced feature requirement |
| Deployment | Local tutorial setup | Cloud Run, Cloud Functions, GCS, MongoDB Atlas, Vertex AI | Move to real cloud architecture |

## 5. Final Interpretation

The lab gave us the conceptual skeleton of the system: RAG with MongoDB Atlas, Vertex AI, Gemini, LangChain, and a web-facing backend. We did not simply submit the lab with small edits. We transformed it into a cloud-native application with user uploads, event-driven ingestion, session isolation, document readiness tracking, deletion synchronization, persistent memory, quiz generation, and a deployed web interface.

From our perspective as developers, the most important transition was moving from a static tutorial corpus to a dynamic cloud workflow. In the lab, the documents already existed and the developer manually prepared the vector store. In SmartStudy, the user uploads a PDF, Google Cloud Storage becomes the document source of truth, Eventarc triggers the ingestion function automatically, MongoDB is updated asynchronously, and the UI reflects readiness before the student asks questions. That is the architectural shift that turns the lab concept into the SmartStudy project.
