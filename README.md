# SmartStudy - Cloud-Native AI Tutor

> INFO-H505 Cloud Computing Project - ULB 2025-2026

SmartStudy is a cloud-native study assistant for lecture PDFs. A student batch-uploads PDFs in the Streamlit UI, the Chat API stores them in a session-scoped folder in Google Cloud Storage through `POST /upload`, GCS events trigger ingestion and cleanup functions, the UI can delete session documents through `DELETE /documents`, and the chat backend answers questions with grounded citations from MongoDB Atlas.

## Architecture

The live system is made of four main parts:

- Streamlit UI on Cloud Run
- Chat API on Cloud Run
- GCS-triggered ingest function for PDF processing
- GCS-delete-trigger cleanup function for removing document vectors

High-level flow:

1. The user opens the Streamlit app.
2. The UI calls the Chat API upload endpoint at `/upload`.
3. The API writes the PDF to the GCS bucket.
4. GCS finalization triggers the ingest Cloud Function.
5. The ingest function extracts text, chunks it, creates embeddings, and stores vectors in MongoDB Atlas.
6. The user asks a question in the UI.
7. The UI calls the Chat API chat endpoint.
8. The API loads only the active session's chunks, ranks them by embedding similarity, builds the prompt, and returns a grounded answer with citations.
9. If a PDF is deleted from the Documents UI or directly from GCS, the file is removed from storage and its vectors are removed from MongoDB.

For diagrams and deeper implementation notes, see:

- [Architecture overview](docs/architecture-overview.md)
- [Architecture deep dive](docs/architecture-dev.md)

## Repository Layout

```text
cloud-computing-project/
  chat_api/          Flask + LangChain RAG backend deployed to Cloud Run
  cloud_function/    GCS-triggered PDF ingestion and cleanup functions
  docs/              Architecture documentation
  streamlit_app/     Streamlit UI deployed to Cloud Run
  .env.example       Environment variable template
  README.md          Project setup and usage
```

## Prerequisites

- Google Cloud SDK (`gcloud`)
- Python 3.12+
- Docker
- MongoDB Atlas account with a vector-search-capable cluster
- A GCP project with billing enabled

## From-Scratch Setup

1. Clone the repository and prepare your environment.

```bash
git clone https://github.com/DanielMortada/cloud-computing-project.git
cd cloud-computing-project
cp .env.example .env
```

2. Edit `.env` with your GCP project ID, region, MongoDB connection string, and bucket name.

3. Enable the required Google Cloud APIs.

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  eventarc.googleapis.com
```

4. Create the GCS bucket used for uploads.

```bash
gcloud storage buckets create gs://YOUR_BUCKET_NAME \
  --location=europe-west1 \
  --uniform-bucket-level-access
```

5. Deploy the ingest function.

```bash
cd cloud_function

gcloud functions deploy smartstudy-ingest \
  --gen2 \
  --region=europe-west1 \
  --runtime=python312 \
  --source=. \
  --entry-point=process_pdf \
  --trigger-event-filters="type=google.cloud.storage.object.v1.finalized" \
  --trigger-event-filters="bucket=YOUR_BUCKET_NAME" \
  --set-env-vars="MONGODB_URI=...,MONGODB_DB_NAME=smartstudy,MONGODB_COLLECTION=context,GCP_PROJECT_ID=...,GCP_REGION=europe-west1,GCS_BUCKET_NAME=YOUR_BUCKET_NAME,VERTEX_AI_EMBEDDING_MODEL=text-embedding-005" \
  --memory=1Gi \
  --timeout=300s
```

6. Deploy the cleanup function.

```bash
gcloud functions deploy smartstudy-cleanup \
  --gen2 \
  --region=europe-west1 \
  --runtime=python312 \
  --source=. \
  --entry-point=cleanup_deleted_pdf \
  --trigger-event-filters="type=google.cloud.storage.object.v1.deleted" \
  --trigger-event-filters="bucket=YOUR_BUCKET_NAME" \
  --set-env-vars="MONGODB_URI=...,MONGODB_DB_NAME=smartstudy,MONGODB_COLLECTION=context,GCP_PROJECT_ID=...,GCP_REGION=europe-west1,GCS_BUCKET_NAME=YOUR_BUCKET_NAME" \
  --memory=1Gi \
  --timeout=300s

cd ..
```

7. Deploy the Chat API to Cloud Run.

```bash
cd chat_api

gcloud run deploy smartstudy-chat-api \
  --source=. \
  --region=europe-west1 \
  --allow-unauthenticated \
  --set-env-vars="MONGODB_URI=...,MONGODB_DB_NAME=smartstudy,MONGODB_COLLECTION=context,MONGODB_CHAT_HISTORY_COLLECTION=chat_history,MONGODB_VECTOR_INDEX_NAME=vector_index,GCP_PROJECT_ID=...,GCP_REGION=europe-west1,GCS_BUCKET_NAME=YOUR_BUCKET_NAME,VERTEX_AI_EMBEDDING_MODEL=text-embedding-005,VERTEX_AI_LLM_MODEL=gemini-2.5-flash" \
  --memory=1Gi

cd ..
```

8. Deploy the Streamlit UI to Cloud Run.

```bash
cd streamlit_app

gcloud run deploy smartstudy-ui \
  --source=. \
  --region=europe-west1 \
  --allow-unauthenticated \
  --set-env-vars="CHAT_API_URL=https://smartstudy-chat-api-XXXXX-ew.a.run.app" \
  --port=8501

cd ..
```

## Verify the Deployment

Use these commands to confirm the live services and functions after deployment:

```bash
gcloud run services describe smartstudy-chat-api --region=europe-west1 --project=YOUR_PROJECT_ID --format="value(status.url)"
gcloud run services describe smartstudy-ui --region=europe-west1 --project=YOUR_PROJECT_ID --format="value(status.url)"
gcloud functions describe smartstudy-ingest --gen2 --region=europe-west1 --project=YOUR_PROJECT_ID
gcloud functions describe smartstudy-cleanup --gen2 --region=europe-west1 --project=YOUR_PROJECT_ID
```

## Test the End-to-End Flow

1. Upload a PDF through the Streamlit UI, or call the upload endpoint indirectly by using the UI.
2. Confirm the file appears in GCS.
3. Watch the ingest function logs.
4. Ask a document-grounded question in the UI and verify the answer includes sources.
5. Ask `Hello` or `How are you?` and verify the answer returns without document sources.
6. Refresh the UI, or reopen the same `?sid=...` link, and confirm the same chat history and Documents list are restored.
7. Click `New Session` and confirm both chat and Documents start empty for the new `sid`.
8. Delete a PDF from the Documents tab and confirm the file disappears from the session and no longer contributes context.

Useful commands:

```bash
gcloud storage cp my-lecture.pdf gs://YOUR_BUCKET_NAME/uploads/YOUR_SESSION_ID/my-lecture.pdf
gcloud functions logs read smartstudy-ingest --region=europe-west1 --limit=100
gcloud storage rm gs://YOUR_BUCKET_NAME/uploads/YOUR_SESSION_ID/my-lecture.pdf
gcloud functions logs read smartstudy-cleanup --region=europe-west1 --limit=100
```

## Local Development

Run the services locally if you want to iterate before deployment.

Chat API:

```bash
cd chat_api
pip install -r requirements.txt
python main.py
```

Streamlit UI:

```bash
cd streamlit_app
pip install -r requirements.txt
streamlit run app.py
```

## MongoDB Atlas Setup

Create a database named `smartstudy` with these collections:

- `context` for PDF chunks and embeddings
- `chat_history` for conversation state

Create a vector search index named `vector_index` on the `context` collection. The current embedding dimension is `768` and similarity is `cosine`.

## Current Notes

- Uploads are handled by the Chat API through `/upload`, not by direct browser-to-GCS writes.
- Each upload is namespaced under `uploads/<session_id>/...`, which isolates one session's study materials from another.
- Uploads are deduplicated per session with SHA-256 content hashes. Re-uploading identical bytes reuses the existing object, even under a different filename.
- Re-uploading the same normalized filename with different content creates a new version and removes the previous same-title object and vectors.
- Ingestion is event-driven and reproducible: a finalized object in GCS is what starts PDF processing.
- Cleanup is also event-driven: deleting a PDF from GCS removes its stored vectors.
- The UI can remove one session-scoped PDF at a time through the Documents tab, which calls `DELETE /documents`.
- Chat memory is persisted in MongoDB and restored on refresh or reopen using session-aware history hydration (`sid` + `GET /history`).
- The Documents tab and sidebar status badges are restored and re-synced through session-aware document hydration (`sid` + `GET /documents`).
- Short social prompts such as `Hello` and `How are you?` return source-free replies instead of citing uploaded PDFs.
- The Sources expander lists only source labels that are also cited inline in the assistant answer.
- Opening the app with a new or missing `sid` starts a new session by design, with an empty chat and an empty Documents pane.

## Team

- Daniel Mortada
- Ismail Hossain Sohan
- George Vasile

## License

University project - INFO-H505 Cloud Computing, ULB.
