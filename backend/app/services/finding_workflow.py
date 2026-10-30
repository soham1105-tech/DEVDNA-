"""Stable finding identity and suppression lookup helpers."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Finding, FindingSuppression


def finding_fingerprint(finding: Finding | dict) -> str:
    def get(key: str):
        return finding.get(key) if isinstance(finding, dict) else getattr(finding, key)

    evidence = get("evidence") or []
    location = next((entry for entry in evidence if entry.get("file")), {})
    occurrence_rule = get("rule") in {"hardcoded_secret", "http_unhandled", "http_without_timeout", "parse_error"}
    identity = {
        "rule": get("rule"),
        "component_id": get("component_id"),
        "file": location.get("file") if not get("component_id") or occurrence_rule else None,
        "line": location.get("line") if not get("component_id") or occurrence_rule else None,
        "title": get("title") if not get("component_id") and not location else None,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def active_suppression(suppression: FindingSuppression | None, now: datetime | None = None) -> bool:
    if not suppression or not suppression.active:
        return False
    expires_at = suppression.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > (now or datetime.now(timezone.utc))


def suppression_map(db: Session, findings: list[Finding]) -> dict[str, FindingSuppression]:
    if not findings:
        return {}
    fingerprints = {finding_fingerprint(finding) for finding in findings}
    rows = db.scalars(select(FindingSuppression).where(
        FindingSuppression.project_id == findings[0].project_id,
        FindingSuppression.fingerprint.in_(fingerprints),
    )).all()
    return {row.fingerprint: row for row in rows}


def suppression_for(db: Session, finding: Finding) -> FindingSuppression | None:
    return db.scalar(select(FindingSuppression).where(
        FindingSuppression.project_id == finding.project_id,
        FindingSuppression.fingerprint == finding_fingerprint(finding),
    ))
