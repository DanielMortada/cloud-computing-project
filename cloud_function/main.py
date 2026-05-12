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
MONGODB_DOCUMENT_STATUS_COLLECTION = os.environ.get(
    "MONGODB_DOCUMENT_STATUS_COLLECTION",
    "document_status",
)
EMBEDDING_MODEL = os.environ.get("VERTEX_AI_EMBEDDING_MODEL", "text-embedding-005")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_REGION = os.environ.get("GCP_REGION", "europe-west1")

mongo_client: MongoClient | None = None
storage_client: storage.Client | None = None


class PdfIngestionError(Exception):
    """Expected user-actionable PDF ingestion failure."""

    def __init__(self, error_code: str, user_message: str, technical_detail: str = ""):
        super().__init__(technical_detail or user_message)
        self.error_code = error_code
        self.user_message = user_message
        self.technical_detail = technical_detail or user_message


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------
def extract_session_id_from_object_name(object_name: str) -> str:
    """Extract the session folder immediately above the filename."""
    path_parts = [part for part in (object_name or "").split("/") if part]
    if len(path_parts) < 2:
        return ""
    return path_parts[-2]


def get_mongo_client() -> MongoClient:
    """Return a shared MongoDB client for the function instance."""
    global mongo_client
    if mongo_client is None:
        mongo_client = MongoClient(MONGODB_URI)
    return mongo_client


def get_storage_client() -> storage.Client:
    """Return a shared Google Cloud Storage client for the function instance."""
    global storage_client
    if storage_client is None:
        storage_client = storage.Client()
    return storage_client


def get_mongodb_collection():
    """Return the MongoDB collection used for storing document chunks."""
    client = get_mongo_client()
    db = client[MONGODB_DB_NAME]
    return db[MONGODB_COLLECTION]


def get_document_status_collection():
    """Return the MongoDB collection used for document ingestion status."""
    client = get_mongo_client()
    db = client[MONGODB_DB_NAME]
    return db[MONGODB_DOCUMENT_STATUS_COLLECTION]


def set_document_status(
    source_name: str,
    session_id: str,
    status: str,
    message: str,
    *,
    stage: str = "",
    error_code: str = "",
    error_detail: str = "",
    chunk_count: int = 0,
):
    """Persist the latest ingestion status for one uploaded document."""
    from datetime import datetime, timezone

    if not source_name:
        return

    now = datetime.now(timezone.utc)
    update = {
        "$set": {
            "object_name": source_name,
            "source": source_name,
            "session_id": session_id,
            "status": status,
            "message": message,
            "stage": stage,
            "error_code": error_code,
            "error_detail": error_detail,
            "chunk_count": int(chunk_count or 0),
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now},
    }
    get_document_status_collection().update_one(
        {"object_name": source_name, "session_id": session_id},
        update,
        upsert=True,
    )


def delete_vectors_for_source(source_name: str) -> int:
    """Delete all vectors belonging to one source file path/name."""
    collection = get_mongodb_collection()
    result = collection.delete_many({"source": source_name})
    return result.deleted_count


def delete_status_for_source(source_name: str) -> int:
    """Delete ingestion status records belonging to one source file path/name."""
    collection = get_document_status_collection()
    result = collection.delete_many({"object_name": source_name})
    return result.deleted_count


def list_pdf_sources_in_bucket(bucket_name: str) -> set[str]:
    """Return all PDF object names currently present in the bucket."""
    bucket = get_storage_client().bucket(bucket_name)
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
        {"_id": 1, "source": 1},
    )
    for doc in cursor:
        source = doc.get("source")
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
    bucket = get_storage_client().bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(dest_path)
    print(f"Downloaded gs://{bucket_name}/{blob_name} to {dest_path}")


def object_exists_in_bucket(bucket_name: str, blob_name: str) -> bool:
    """Return True when the referenced object still exists in GCS."""
    bucket = get_storage_client().bucket(bucket_name)
    return bucket.blob(blob_name).exists()


def extract_and_chunk(pdf_path: str, source_name: str, session_id: str):
    """Load PDF, split into chunks, and attach metadata."""
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    loader = PyPDFLoader(pdf_path)
    try:
        pages = loader.load()
    except Exception as exc:
        raise PdfIngestionError(
            "pdf_parse_error",
            "SmartStudy could not read this PDF. The file may be corrupted, encrypted, or unsupported. Please try exporting it again as a standard text-based PDF.",
            str(exc),
        ) from exc

    extracted_text = "\n".join((page.page_content or "").strip() for page in pages)
    if not pages or not any(char.isalnum() for char in extracted_text):
        raise PdfIngestionError(
            "no_extractable_text",
            "This PDF does not contain extractable text. It may be a scan or image-only PDF. Please upload a text-based PDF or run OCR on the file first.",
            f"PyPDFLoader returned {len(pages)} page(s) with no alphanumeric text.",
        )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    chunks = [
        chunk
        for chunk in splitter.split_documents(pages)
        if (chunk.page_content or "").strip()
    ]

    if not chunks:
        raise PdfIngestionError(
            "no_extractable_text",
            "This PDF does not contain enough extractable text to index. Please upload a text-based PDF or run OCR on the file first.",
            "Text splitter returned no non-empty chunks.",
        )

    for chunk in chunks:
        chunk.metadata["source"] = source_name
        if session_id:
            chunk.metadata["session_id"] = session_id

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
        session_id = chunk_metadata.get("session_id", "")

        # Store the raw 0-based page index from PyPDFLoader as-is.
        # The Chat API's _normalize_page_display() handles the +1 conversion
        # for human-readable display.
        raw_page = chunk_metadata.get("page")

        documents.append(
            {
                "textChunk": chunk.page_content,
                "vectorEmbedding": embedding,
                "source": source,
                "page": raw_page,
                "session_id": session_id,
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
    session_id = extract_session_id_from_object_name(blob_name)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if not object_exists_in_bucket(bucket_name, blob_name):
            print(f"Skipping {blob_name}: object no longer exists in storage.")
            return

        set_document_status(
            blob_name,
            session_id,
            "processing",
            "Ingestion started. Downloading the PDF from storage.",
            stage="downloading",
        )
        download_pdf_from_gcs(bucket_name, blob_name, tmp_path)
        set_document_status(
            blob_name,
            session_id,
            "processing",
            "Reading PDF text and splitting it into study chunks.",
            stage="parsing",
        )
        chunks = extract_and_chunk(tmp_path, source_name=blob_name, session_id=session_id)

        set_document_status(
            blob_name,
            session_id,
            "processing",
            "Generating semantic embeddings for the extracted chunks.",
            stage="embedding",
            chunk_count=len(chunks),
        )
        embeddings = generate_embeddings(chunks)

        # Idempotency per source object: replace any previous vectors for this path.
        set_document_status(
            blob_name,
            session_id,
            "processing",
            "Saving indexed chunks to MongoDB.",
            stage="upserting",
            chunk_count=len(chunks),
        )
        deleted_for_source = delete_vectors_for_source(blob_name)
        if deleted_for_source:
            print(f"Removed {deleted_for_source} old vectors for {blob_name}")

        if not object_exists_in_bucket(bucket_name, blob_name):
            print(f"Skipping upsert for {blob_name}: object was deleted during ingestion.")
            return

        upsert_to_mongodb(chunks, embeddings)
        set_document_status(
            blob_name,
            session_id,
            "ready",
            "Ingestion complete. Ready for chat.",
            stage="ready",
            chunk_count=len(chunks),
        )

        # Safety net: remove stale vectors for files no longer in GCS.
        deleted_stale = reconcile_context_with_bucket(bucket_name)
        if deleted_stale:
            print(f"Reconciled {deleted_stale} stale vectors not present in GCS")

        print(f"Pipeline complete for {blob_name}")
    except PdfIngestionError as exc:
        set_document_status(
            blob_name,
            session_id,
            "failed",
            exc.user_message,
            stage="failed",
            error_code=exc.error_code,
            error_detail=exc.technical_detail,
        )
        print(f"PDF ingestion failed for {blob_name}: {exc.error_code} - {exc}")
        return
    except Exception as exc:
        set_document_status(
            blob_name,
            session_id,
            "failed",
            "Ingestion failed while processing this PDF. Please try again later or upload a different text-based PDF.",
            stage="failed",
            error_code="ingestion_error",
            error_detail=str(exc),
        )
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

    deleted_count = delete_vectors_for_source(blob_name)
    deleted_status_count = delete_status_for_source(blob_name)
    print(
        f"Deleted {deleted_count} vectors and {deleted_status_count} status records "
        f"for removed file: {blob_name}"
    )
