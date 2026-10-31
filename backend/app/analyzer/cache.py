"""Incremental analysis cache: per-file extraction results keyed by content hash.

Analyzers split work into two phases:

  1. extraction (per file, expensive)   - parse the source and summarise it
     into a JSON-serialisable model. This is what gets cached.
  2. resolution (whole project, cheap)  - join per-file models into the final
     component/edge graph. Always re-run, because cross-file relationships can
     change even when the changed file's own content did not.

A cache entry is keyed by sha256(rel_path + NUL + content), so the same file
moved to a different path gets a different entry, and identical content at the
same path always hits. Entries are immutable, written atomically, and safe to
share across projects (the model includes the relative path it was built from).

The cache lives under `<data_dir>/analyzer-cache/<project-key>/`. Disable it by
passing ``use_cache=False`` (or setting DEVDNA_INCREMENTAL_CACHE=0).

Security: entries are JSON only — a cache file is data, never code, so a
tampered cache cannot execute. Every entry is signed with HMAC-SHA256 using a
random per-installation key stored under the data directory; unsigned or
tampered files are rejected as misses. Models must contain no secret values
(only redacted text), so the cache is safe to keep on disk.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from pathlib import Path

log = logging.getLogger("devdna.cache")

_MAGIC = b"devdna-cache-v1"


def default_cache_dir(root: Path) -> Path:
    from app.config import settings

    key = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(settings.data_dir) / "analyzer-cache" / key


_KEY_CACHE: dict[str, bytes] = {}


def _integrity_key(directory: Path) -> bytes | None:
    """Per-installation HMAC key, created once next to the cache entries."""
    cached = _KEY_CACHE.get(str(directory))
    if cached is not None:
        return cached
    key_path = directory / ".hmac-key"
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.is_file():
            key = key_path.read_bytes()
        else:
            key = secrets.token_bytes(32)
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
    except OSError:
        log.warning("cache integrity key unavailable; cache entries will be rejected")
        return None
    if len(key) < 32:
        return None
    _KEY_CACHE[str(directory)] = key
    return key


class FileCache:
    """Content-addressed store for per-file extraction models.

    Models are JSON dicts signed with HMAC-SHA256. Entries are written
    atomically; any read/write failure, HMAC mismatch or tampering just counts
    as a miss and is never loaded, let alone executed.
    """

    def __init__(self, directory: Path | None, use_cache: bool = True):
        self.enabled = use_cache and bool(directory) and \
            os.environ.get("DEVDNA_INCREMENTAL_CACHE", "1") != "0"
        self.directory = Path(directory) if directory else None
        self.hits = 0
        self.misses = 0
        self.errors = 0
        self.rejected = 0

    @staticmethod
    def key(rel: str, content: str | bytes) -> str:
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")
        return hashlib.sha256(rel.encode("utf-8") + b"\x00" + content).hexdigest()

    def _path(self, rel: str, content: str | bytes) -> Path | None:
        if not self.enabled:
            return None
        return self.directory / f"{self.key(rel, content)}.json"

    def _verify(self, raw: bytes) -> dict | None:
        """Return the signed model, or None when unsigned/tampered/corrupt."""
        if not raw.startswith(_MAGIC + b"\n"):
            return None
        try:
            header, payload = raw[len(_MAGIC) + 1:].split(b"\n", 1)
        except ValueError:
            return None
        key = _integrity_key(self.directory)
        if key is None:
            return None
        if not hmac.compare_digest(header, hmac.new(key, payload, hashlib.sha256).hexdigest().encode()):
            return None
        try:
            model = json.loads(payload)
        except ValueError:
            return None
        if not isinstance(model, dict):
            return None
        return model

    def load(self, rel: str, content: str | bytes):
        """Return the cached model for (rel, content), or None."""
        path = self._path(rel, content)
        if path is None:
            return None
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        model = self._verify(raw)
        if model is None or model.get("rel") != rel:
            self.rejected += 1
            return None
        self.hits += 1
        return model

    def store(self, rel: str, content: str | bytes, model) -> None:
        path = self._path(rel, content)
        if path is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            key = _integrity_key(self.directory)
            if key is None:
                self.errors += 1
                return
            payload = json.dumps({**model, "rel": rel}, separators=(",", ":")).encode("utf-8")
            signature = hmac.new(key, payload, hashlib.sha256).hexdigest().encode()
            blob = _MAGIC + b"\n" + signature + b"\n" + payload
            fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(blob)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except (OSError, TypeError, ValueError):
            self.errors += 1  # cache is best-effort; never fail analysis for it

    @property
    def stats(self) -> dict:
        return {"files_reused": self.hits, "files_parsed": self.misses,
                "cache_writes_failed": self.errors, "enabled": self.enabled}
