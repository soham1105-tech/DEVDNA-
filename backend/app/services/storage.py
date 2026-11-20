"""Cloud Storage artifact persistence for DevDNA.

When DEVDNA_GCS_BUCKET is unset (local dev), all operations are safe no-ops and
local filesystem storage under settings.data_dir is used.

When DEVDNA_GCS_BUCKET is set (Cloud Run), uploaded repository ZIPs and
generated reports are persisted to Google Cloud Storage (private, uniform
bucket-level access) so state survives ephemeral Cloud Run container restarts.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

log = logging.getLogger("devdna.storage")


def gcs_enabled() -> bool:
    return bool(settings.gcs_bucket)


def _client():
    from google.cloud import storage  # imported lazily so local dev needs no GCS SDK

    return storage.Client()


def archive_project_zip(project_id: str, data: bytes) -> str | None:
    """Persist an uploaded or fetched repository ZIP to GCS. Returns gs:// URI or None."""
    if not gcs_enabled():
        return None
    blob_name = f"projects/{project_id}/source.zip"
    try:
        bucket = _client().bucket(settings.gcs_bucket)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type="application/zip")
        uri = f"gs://{settings.gcs_bucket}/{blob_name}"
        log.info("Archived project %s ZIP to %s (%d bytes)", project_id, uri, len(data))
        return uri
    except Exception as exc:
        log.error("Failed to archive project %s to GCS: %s", project_id, exc)
        raise RuntimeError(f"Cloud Storage upload failed for project '{project_id}': {exc}") from exc


def download_project_zip(project_id: str) -> bytes | None:
    """Fetch a persisted project ZIP from GCS (for worker hydration on ephemeral containers)."""
    if not gcs_enabled():
        return None
    blob_name = f"projects/{project_id}/source.zip"
    bucket = _client().bucket(settings.gcs_bucket)
    blob = bucket.blob(blob_name)
    if not blob.exists():
        return None
    return blob.download_as_bytes()


def delete_project_artifacts(project_id: str) -> int:
    """Delete all GCS artifacts belonging to a deleted project. Returns deleted count."""
    if not gcs_enabled():
        return 0
    prefix = f"projects/{project_id}/"
    try:
        bucket = _client().bucket(settings.gcs_bucket)
        blobs = list(bucket.list_blobs(prefix=prefix))
        for blob in blobs:
            blob.delete()
        return len(blobs)
    except Exception as exc:
        log.warning("Failed to clean up GCS artifacts for project %s: %s", project_id, exc)
        return 0


def ensure_local_source(project_id: str, source_path: Path) -> Path:
    """Ensure source_path exists locally; rehydrate from GCS archive if running on a fresh container."""
    if source_path.exists():
        return source_path
    if not gcs_enabled():
        return source_path
    data = download_project_zip(project_id)
    if not data:
        return source_path
    from app.analyzer.ingest import safe_extract_zip

    safe_extract_zip(data, source_path)
    return source_path
