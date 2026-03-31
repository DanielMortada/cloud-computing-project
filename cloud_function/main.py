"""
SmartStudy — Cloud Function: PDF Ingestion Pipeline
=====================================================
Triggered automatically when a PDF is uploaded to the GCS bucket.

Pipeline:
  1. Download PDF from GCS
  2. Extract text with PyPDF
  3. Chunk text with LangChain RecursiveCharacterTextSplitter
  4. Generate embeddings via Vertex AI Text Embeddings API
  5. Upsert vectors + metadata into MongoDB Atlas Vector Search
"""

import os
import tempfile
import functions_framework

from google.cloud import storage
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_vertexai import VertexAIEmbeddings
from pymongo import MongoClient


# ---------------------------------------------------------------------------
# Configuration (read from environment variables set during deployment)
# ---------------------------------------------------------------------------
MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "smartstudy")
MONGODB_COLLECTION = os.environ.get("MONGODB_COLLECTION", "context")
EMBEDDING_MODEL = os.environ.get("VERTEX_AI_EMBEDDING_MODEL", "text-embedding-005")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_REGION = os.environ.get("GCP_REGION", "europe-west1")


# ---------------------------------------------------------------------------
# MongoDB helper
# ---------------------------------------------------------------------------
def get_mongodb_collection():
    """Return the MongoDB collection used for storing document chunks."""
    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB_NAME]
    return db[MONGODB_COLLECTION]


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------
def download_pdf_from_gcs(bucket_name: str, blob_name: str, dest_path: str):
    """Download a PDF from GCS to a local temporary path."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(dest_path)
    print(f"✅ Downloaded gs://{bucket_name}/{blob_name} → {dest_path}")


def extract_and_chunk(pdf_path: str, source_name: str):
    """Load PDF, split into chunks, and attach metadata."""
    loader = PyPDFLoader(pdf_path)
    pages = loader.load()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    chunks = splitter.split_documents(pages)

    # Enrich metadata with the original filename
    for chunk in chunks:
        chunk.metadata["source"] = source_name

    print(f"✅ Extracted {len(pages)} pages → {len(chunks)} chunks")
    return chunks


def generate_embeddings(chunks):
    """Generate vector embeddings for each chunk using Vertex AI."""
    embeddings_model = VertexAIEmbeddings(
        model_name=EMBEDDING_MODEL,
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
    )

    texts = [chunk.page_content for chunk in chunks]

    # Vertex AI supports batched embedding — process in batches of 250
    batch_size = 250
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_embeddings = embeddings_model.embed_documents(batch)
        all_embeddings.extend(batch_embeddings)

    print(f"✅ Generated {len(all_embeddings)} embeddings")
    return all_embeddings


def upsert_to_mongodb(chunks, embeddings):
    """Insert document chunks and their embeddings into MongoDB Atlas."""
    collection = get_mongodb_collection()

    documents = []
    for chunk, embedding in zip(chunks, embeddings):
        documents.append(
            {
                "textChunk": chunk.page_content,
                "vectorEmbedding": embedding,
                "metadata": chunk.metadata,
            }
        )

    result = collection.insert_many(documents)
    print(f"✅ Upserted {len(result.inserted_ids)} documents into MongoDB")


# ---------------------------------------------------------------------------
# Cloud Function entry point
# ---------------------------------------------------------------------------
@functions_framework.cloud_event
def process_pdf(cloud_event):
    """
    Triggered by a 'google.cloud.storage.object.v1.finalized' event.
    Runs the full ingestion pipeline for the uploaded PDF.
    """
    data = cloud_event.data

    bucket_name = data["bucket"]
    blob_name = data["name"]

    # Only process PDF files
    if not blob_name.lower().endswith(".pdf"):
        print(f"⏭️  Skipping non-PDF file: {blob_name}")
        return

    print(f"📄 Processing: gs://{bucket_name}/{blob_name}")

    # 1. Download PDF to a temp file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        download_pdf_from_gcs(bucket_name, blob_name, tmp_path)

        # 2. Extract text and chunk
        chunks = extract_and_chunk(tmp_path, source_name=blob_name)

        if not chunks:
            print("⚠️  No text extracted from PDF — skipping.")
            return

        # 3. Generate embeddings
        embeddings = generate_embeddings(chunks)

        # 4. Upsert into MongoDB
        upsert_to_mongodb(chunks, embeddings)

        print(f"🎉 Pipeline complete for {blob_name}")

    except Exception as e:
        print(f"❌ Error processing {blob_name}: {e}")
        raise

    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
