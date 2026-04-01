# SmartStudy 🎓 — Cloud-Native AI Tutor

> **INFO-H505 Cloud Computing Project — ULB 2025-2026**
>
> An automated, cloud-native study assistant that lets you upload lecture PDFs
> and immediately chat with an AI tutor to prepare for exams.

---

## 📐 Architecture Overview

```
┌──────────────┐        ┌────────────────────────────────────────┐
│   Student    │        │          Google Cloud Platform          │
│   Browser    │        │                                        │
│              │        │  ┌──────────────────────────────────┐  │
│  Streamlit   │◄──────►│  │  Chat API (Cloud Run)            │  │
│  (Cloud Run) │  HTTP  │  │  Flask + LangChain + Gemini 2.5  │  │
│              │        │  │  + Conversation Memory            │  │
└──────────────┘        │  └──────────┬───────────────────────┘  │
                        │             │ vector search             │
                        │             ▼                           │
                        │  ┌──────────────────────┐              │
                        │  │  MongoDB Atlas        │              │
                        │  │  • context (vectors)  │              │
                        │  │  • chat_history       │              │
                        │  └──────────────────────┘              │
                        │             ▲                           │
                        │             │ upsert embeddings         │
                        │  ┌──────────┴───────────────────────┐  │
                        │  │  Cloud Function (Python)          │  │
                        │  │  Triggered by GCS "finalize"      │  │
                        │  │  PDF → chunks → embeddings → DB   │  │
                        │  └──────────┬───────────────────────┘  │
                        │             │ trigger                   │
                        │  ┌──────────┴───────────────────────┐  │
                        │  │  GCS Bucket (smartstudy-pdfs)     │  │
                        │  │  Upload lecture PDFs here         │  │
                        │  └──────────────────────────────────┘  │
                        └────────────────────────────────────────┘
```

**Data flow:**

1. Student uploads a PDF to the GCS bucket.
2. The upload triggers a **Cloud Function** that extracts text, chunks it,
   generates vector embeddings (Vertex AI), and stores them in **MongoDB Atlas**.
3. Student asks a question via the **Streamlit** web UI.
4. The **Chat API** retrieves relevant chunks from MongoDB (vector search),
   sends them + the question + chat history to **Gemini 2.5 Flash**, and
   returns a grounded, cited answer.

---

## 🗂️ Project Structure

```
cloud-computing-project/
├── cloud_function/          # GCS-triggered PDF ingestion pipeline
│   ├── main.py              #   Cloud Function entry point
│   └── requirements.txt     #   Python dependencies
│
├── chat_api/                # RAG chat backend (Flask)
│   ├── main.py              #   API server + LangChain RAG chain
│   ├── requirements.txt     #   Python dependencies
│   └── Dockerfile           #   Container for Cloud Run
│
├── streamlit_app/           # Web UI (Streamlit)
│   ├── app.py               #   Chat interface
│   ├── requirements.txt     #   Python dependencies
│   └── Dockerfile           #   Container for Cloud Run
│
├── .env.example             # Template for environment variables
├── .gitignore
├── project-context.md       # Project instructions (markdown)
└── README.md                # ← You are here
```

---

## ⚙️ Prerequisites

| Tool | Purpose |
|------|---------|
| [Google Cloud SDK (`gcloud`)](https://cloud.google.com/sdk/docs/install) | CLI for GCP deployment |
| [Python 3.12+](https://www.python.org/) | Runtime for all services |
| [Docker](https://docs.docker.com/get-docker/) | Build containers for Cloud Run |
| MongoDB Atlas account | Free-tier cluster for vector search |
| GCP project with billing | Use the $50 student coupon |

**GCP APIs to enable:**

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  eventarc.googleapis.com
```

---

## 🚀 Setup & Deployment

### 1. Clone and configure

```bash
git clone https://github.com/DanielMortada/cloud-computing-project.git
cd cloud-computing-project
cp .env.example .env
# Edit .env with your actual values (MongoDB URI, GCP project ID, etc.)
```

### 2. Create the GCS bucket

```bash
gcloud storage buckets create gs://YOUR_BUCKET_NAME \
  --location=europe-west1 \
  --uniform-bucket-level-access
```

### 3. Deploy the Cloud Function

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
  --set-env-vars="MONGODB_URI=...,MONGODB_DB_NAME=smartstudy,GCP_PROJECT_ID=...,GCP_REGION=europe-west1" \
  --memory=512Mi \
  --timeout=300s

cd ..
```

### 4. Deploy the Chat API to Cloud Run

```bash
cd chat_api

gcloud run deploy smartstudy-chat-api \
  --source=. \
  --region=europe-west1 \
  --allow-unauthenticated \
  --set-env-vars="MONGODB_URI=...,GCP_PROJECT_ID=...,GCP_REGION=europe-west1" \
  --memory=512Mi

cd ..
```

### 5. Deploy the Streamlit UI to Cloud Run

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

### 6. Upload a PDF to test

```bash
gcloud storage cp my-lecture.pdf gs://YOUR_BUCKET_NAME/
# The Cloud Function triggers automatically — check logs:
gcloud functions logs read smartstudy-ingest --region=europe-west1
```

---

## 🧪 Local Development

To run the Chat API locally:

```bash
cd chat_api
pip install -r requirements.txt
# Make sure .env is loaded (or export vars manually)
python main.py
```

To run the Streamlit UI locally:

```bash
cd streamlit_app
pip install -r requirements.txt
streamlit run app.py
```

---

## 🧠 Key Features

| Feature | Status | Description |
|---------|--------|-------------|
| **PDF Ingestion Pipeline** | ✅ | GCS upload → Cloud Function → chunks → embeddings → MongoDB |
| **RAG Chat** | ✅ | Vector search + Gemini 2.5 Flash with source citations |
| **Tutor Persona** | ✅ | System prompt enforces academic style, citations, study tips |
| **Conversation Memory** | ✅ | Chat history stored in MongoDB, persists across messages |
| **Quiz Mode** | ✅ | `/quiz` command generates MCQ from lecture material |
| **Web Interface** | ✅ | Streamlit on Cloud Run |

---

## 📚 MongoDB Atlas Setup

1. Create a free-tier M0 cluster at [cloud.mongodb.com](https://cloud.mongodb.com).
2. Create a database called `smartstudy` with two collections:
   - `context` — stores document chunks + vector embeddings
   - `chat_history` — stores conversation messages
3. Create a **Vector Search Index** on the `context` collection named `vector_index`:

   ```json
   {
     "fields": [
       {
         "type": "vector",
         "path": "vectorEmbedding",
         "numDimensions": 768,
         "similarity": "cosine"
       }
     ]
   }
   ```

4. Whitelist `0.0.0.0/0` in Network Access (for Cloud Functions / Cloud Run access).

---

## 👥 Team

- Daniel Mortada

---

## 📝 License

University project — INFO-H505 Cloud Computing, ULB.
