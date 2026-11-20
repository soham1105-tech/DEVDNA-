"""Tests for Cloud Run production runtime integrations: GCS storage, Cloud Tasks worker, JSON logging."""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

from app.config import settings
from app.services import storage as storage_svc
from app.services import tasks as tasks_svc


def test_storage_noop_when_gcs_bucket_unset():
    assert storage_svc.gcs_enabled() is False
    assert storage_svc.archive_project_zip("p1", b"PK\x03\x04") is None
    assert storage_svc.download_project_zip("p1") is None
    assert storage_svc.delete_project_artifacts("p1") == 0


def test_storage_archive_and_delete_when_gcs_bucket_set(monkeypatch):
    monkeypatch.setattr(settings, "gcs_bucket", "devdna-test-bucket")
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_bucket.list_blobs.return_value = [mock_blob]
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    monkeypatch.setattr(storage_svc, "_client", lambda: mock_client)

    uri = storage_svc.archive_project_zip("proj123", b"zip-bytes")
    assert uri == "gs://devdna-test-bucket/projects/proj123/source.zip"
    mock_blob.upload_from_string.assert_called_once_with(b"zip-bytes", content_type="application/zip")

    deleted = storage_svc.delete_project_artifacts("proj123")
    assert deleted == 1
    mock_blob.delete.assert_called_once()


def test_worker_endpoint_requires_configured_secret_and_is_idempotent(client, sample_project, monkeypatch):
    # 1. Fails closed (503) when DEVDNA_WORKER_SECRET is unset
    monkeypatch.setattr(settings, "worker_secret", "")
    r = client.post("/api/internal/tasks/analyze", json={"run_id": "any"})
    assert r.status_code == 503

    # 2. Rejects missing or wrong secret (401)
    monkeypatch.setattr(settings, "worker_secret", "super-worker-secret")
    monkeypatch.setattr(settings, "api_token", "user-api-token")
    assert client.post("/api/internal/tasks/analyze", json={"run_id": "any"}).status_code == 401
    assert client.post(
        "/api/internal/tasks/analyze",
        json={"run_id": "any"},
        headers={"X-DevDNA-Worker-Secret": "wrong"},
    ).status_code == 401

    # 3. Idempotent skip for an already-completed run
    proj = client.get(
        f"/api/projects/{sample_project}",
        headers={"X-DevDNA-Token": "user-api-token"},
    ).json()
    run_id = proj["latest_run_id"]
    ok = client.post(
        "/api/internal/tasks/analyze",
        json={"run_id": run_id},
        headers={"X-DevDNA-Worker-Secret": "super-worker-secret"},
    )
    assert ok.status_code == 200
    assert ok.json()["idempotent_skip"] is True
    assert ok.json()["status"] == "completed"


def test_tasks_fallback_to_background_tasks_when_cloud_tasks_unset(monkeypatch):
    monkeypatch.setattr(settings, "cloud_tasks_queue", "")
    monkeypatch.setattr(settings, "worker_url", "")
    bg = MagicMock()
    mode = tasks_svc.enqueue_analysis_run("run123", bg)
    assert mode == "background_tasks"
    bg.add_task.assert_called_once()


def test_structured_json_access_log_when_enabled(client, monkeypatch, caplog):
    monkeypatch.setattr(settings, "log_format", "json")
    with caplog.at_level(logging.INFO, logger="devdna.access"):
        r = client.get("/api/projects")
        assert r.status_code == 200
    json_records = [rec.getMessage() for rec in caplog.records if rec.name == "devdna.access"]
    assert json_records
    parsed = json.loads(json_records[-1])
    assert parsed["severity"] == "INFO"
    assert parsed["method"] == "GET"
    assert parsed["path"] == "/api/projects"
    assert parsed["status"] == 200
    assert parsed["request_id"]
