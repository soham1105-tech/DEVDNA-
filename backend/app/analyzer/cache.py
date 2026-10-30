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
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import tempfile
from pathlib import Path

log = logging.getLogger("devdna.cache")


def default_cache_dir(root: Path) -> Path:
    from app.config import settings

    key = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(settings.data_dir) / "analyzer-cache" / key


class FileCache:
    """Content-addressed store for per-file extraction models.

    `format="json"` for plain-dict models (JS analyzer); `format="pickle"` for
    payloads holding Python objects (e.g. parsed ASTs). Entries are written
    atomically; any read/write failure just counts as a miss.
    """

    def __init__(self, directory: Path | None, use_cache: bool = True, fmt: str = "json"):
        self.enabled = use_cache and bool(directory) and \
            os.environ.get("DEVDNA_INCREMENTAL_CACHE", "1") != "0"
        self.directory = Path(directory) if directory else None
        self.fmt = fmt
        self.hits = 0
        self.misses = 0
        self.errors = 0

    @staticmethod
    def key(rel: str, content: str | bytes) -> str:
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")
        return hashlib.sha256(rel.encode("utf-8") + b"\x00" + content).hexdigest()

    def _path(self, rel: str, content: str | bytes) -> Path | None:
        if not self.enabled:
            return None
        return self.directory / f"{self.key(rel, content)}.{self.fmt}"

    def load(self, rel: str, content: str | bytes):
        """Return the cached model for (rel, content), or None."""
        path = self._path(rel, content)
        if path is None:
            return None
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        try:
            if self.fmt == "json":
                model = json.loads(raw)
                if not isinstance(model, dict) or model.get("rel") != rel:
                    return None
            else:
                model = pickle.loads(raw)
                if not isinstance(model, dict) or model.get("rel") != rel:
                    return None
        except (ValueError, pickle.PickleError, AttributeError, ImportError, EOFError):
            return None
        self.hits += 1
        return model

    def store(self, rel: str, content: str | bytes, model) -> None:
        path = self._path(rel, content)
        if path is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({**model, "rel": rel}, separators=(",", ":")).encode("utf-8") \
                if self.fmt == "json" else pickle.dumps({**model, "rel": rel}, protocol=pickle.HIGHEST_PROTOCOL)
            fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(payload)
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
