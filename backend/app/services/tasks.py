"""Durable background job dispatch via Google Cloud Tasks (with local BackgroundTasks fallback).

When DEVDNA_CLOUD_TASKS_QUEUE and DEVDNA_WORKER_URL are configured (Cloud Run
production), analysis runs are enqueued as durable Cloud Tasks targeting
`POST {DEVDNA_WORKER_URL}/api/internal/tasks/analyze` with an authenticated
worker secret header. When unset (local development), jobs run via FastAPI's
BackgroundTasks.
"""
from __future__ import annotations

import json
import logging

from fastapi import BackgroundTasks

from app.config import settings
from app.services.analysis import run_analysis

log = logging.getLogger("devdna.tasks")


def cloud_tasks_enabled() -> bool:
    return bool(settings.cloud_tasks_queue and settings.worker_url)


def enqueue_analysis_run(run_id: str, background: BackgroundTasks | None = None) -> str:
    """Dispatch an analysis run durably via Cloud Tasks when configured, else BackgroundTasks.

    Returns the dispatch mode: 'cloud_tasks' | 'background_tasks' | 'inline'.
    """
    if cloud_tasks_enabled():
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        url = f"{settings.worker_url.rstrip('/')}/api/internal/tasks/analyze"
        headers = {"Content-Type": "application/json"}
        if settings.worker_secret:
            headers["X-DevDNA-Worker-Secret"] = settings.worker_secret
        body = json.dumps({"run_id": run_id}).encode("utf-8")
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url,
                "headers": headers,
                "body": body,
            }
        }
        response = client.create_task(parent=settings.cloud_tasks_queue, task=task)
        log.info("Enqueued Cloud Task %s for analysis run %s", response.name, run_id)
        return "cloud_tasks"

    if background is not None:
        background.add_task(run_analysis, run_id)
        return "background_tasks"

    run_analysis(run_id)
    return "inline"
