"""
SmartStudy - Cloud Function: PDF ingestion and cleanup pipeline.

This module exposes two Gen2 Cloud Function entry points:
1. process_pdf (GCS finalized event): ingest PDF -> chunks -> embeddings -> MongoDB.
2. cleanup_deleted_pdf (GCS deleted event): remove vectors for deleted PDFs.
"""

import os
import tempfile

import functions_framework
from google.cloud import storage
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
# MongoDB helpers
# ---------------------------------------------------------------------------
def get_mongodb_collection():
    """Return the MongoDB collection used for storing document chunks."""
    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB_NAME]
    return db[MONGODB_COLLECTION]


def delete_vectors_for_source(source_name: str) -> int:
    """Delete all vectors belonging to one source file path/name."""
    collection = get_mongodb_collection()
    result = collection.delete_many(
        {
            "$or": [
                {"source": source_name},
                {"metadata.source": source_name},
            ]
        }
    )
    return result.deleted_count


def list_pdf_sources_in_bucket(bucket_name: str) -> set[str]:
    """Return all PDF object names currently present in the bucket."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    pdf_sources: set[str] = set()
    for blob in bucket.list_blobs():
        if blob.name and blob.name.lower().endswith(".pdf"):
            pdf_sources.add(blob.name)
    return pdf_sources


def reconcile_context_with_bucket(bucket_name: str) -> int:
    """
    Remove stale MongoDB vectors whose source file no longer exists in GCS.
    This keeps context synced even if historical delete events were missed.
    """
    active_pdf_sources = list_pdf_sources_in_bucket(bucket_name)
    collection = get_mongodb_collection()

    stale_ids = []
    cursor = collection.find(
        {},
        {"_id": 1, "source": 1, "metadata.source": 1},
    )
    for doc in cursor:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        source = doc.get("source") or metadata.get("source")
        if not source or source not in active_pdf_sources:
            stale_ids.append(doc["_id"])

    if not stale_ids:
        return 0

    result = collection.delete_many({"_id": {"$in": stale_ids}})
    return result.deleted_count


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------
def download_pdf_from_gcs(bucket_name: str, blob_name: str, dest_path: str):
    """Download a PDF from GCS to a local temporary path."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(dest_path)
    print(f"Downloaded gs://{bucket_name}/{blob_name} to {dest_path}")


def extract_and_chunk(pdf_path: str, source_name: str):
    """Load PDF, split into chunks, and attach metadata."""
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    loader = PyPDFLoader(pdf_path)
    pages = loader.load()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    chunks = splitter.split_documents(pages)

    for chunk in chunks:
        chunk.metadata["source"] = source_name

    print(f"Extracted {len(pages)} pages into {len(chunks)} chunks")
    return chunks


def generate_embeddings(chunks):
    """Generate vector embeddings for each chunk using Vertex AI."""
    from langchain_google_vertexai import VertexAIEmbeddings

    embeddings_model = VertexAIEmbeddings(
        model_name=EMBEDDING_MODEL,
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
    )

    texts = [chunk.page_content for chunk in chunks]
    batch_size = 250
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_embeddings = embeddings_model.embed_documents(batch)
        all_embeddings.extend(batch_embeddings)

    print(f"Generated {len(all_embeddings)} embeddings")
    return all_embeddings


def upsert_to_mongodb(chunks, embeddings):
    """Insert document chunks and their embeddings into MongoDB Atlas."""
    collection = get_mongodb_collection()

    documents = []
    for chunk, embedding in zip(chunks, embeddings):
        chunk_metadata = chunk.metadata or {}
        source = chunk_metadata.get("source", "unknown")
        raw_page = chunk_metadata.get("page")
        page_number = None
        if isinstance(raw_page, int):
            page_number = raw_page + 1 if raw_page >= 0 else raw_page
        elif isinstance(raw_page, str) and raw_page.strip().isdigit():
            page_int = int(raw_page.strip())
            page_number = page_int + 1 if page_int >= 0 else page_int

        documents.append(
            {
                "textChunk": chunk.page_content,
                "vectorEmbedding": embedding,
                "source": source,
                "pageNumber": page_number,
                "metadata": chunk_metadata,
            }
        )

    result = collection.insert_many(documents)
    print(f"Upserted {len(result.inserted_ids)} documents into MongoDB")


# ---------------------------------------------------------------------------
# Cloud Function entry points
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

    if not blob_name.lower().endswith(".pdf"):
        print(f"Skipping non-PDF file: {blob_name}")
        return

    print(f"Processing: gs://{bucket_name}/{blob_name}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        download_pdf_from_gcs(bucket_name, blob_name, tmp_path)
        chunks = extract_and_chunk(tmp_path, source_name=blob_name)

        if not chunks:
            print("No text extracted from PDF - skipping.")
            return

        embeddings = generate_embeddings(chunks)

        # Idempotency per source object: replace any previous vectors for this path.
        deleted_for_source = delete_vectors_for_source(blob_name)
        if deleted_for_source:
            print(f"Removed {deleted_for_source} old vectors for {blob_name}")

        upsert_to_mongodb(chunks, embeddings)

        # Safety net: remove stale vectors for files no longer in GCS.
        deleted_stale = reconcile_context_with_bucket(bucket_name)
        if deleted_stale:
            print(f"Reconciled {deleted_stale} stale vectors not present in GCS")

        print(f"Pipeline complete for {blob_name}")
    except Exception as exc:
        print(f"Error processing {blob_name}: {exc}")
        raise
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@functions_framework.cloud_event
def cleanup_deleted_pdf(cloud_event):
    """
    Triggered by a 'google.cloud.storage.object.v1.deleted' event.
    Removes all vectors linked to the deleted source object.
    """
    data = cloud_event.data
    bucket_name = data["bucket"]
    blob_name = data["name"]

    if not blob_name.lower().endswith(".pdf"):
        print(f"Skipping non-PDF deletion event: {blob_name}")
        return

    # Overwrite operations can emit delete events for older generations.
    # If the same object path still exists, do not remove vectors.
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    if bucket.blob(blob_name).exists():
        print(
            f"Skipping cleanup for {blob_name}: object path still exists "
            "(likely generation replacement)."
        )
        return

    deleted_count = delete_vectors_for_source(blob_name)
    print(f"Deleted {deleted_count} vectors for removed file: {blob_name}")
