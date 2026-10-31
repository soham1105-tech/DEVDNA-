"""Server-side configuration loaded from environment variables (.env supported).

API keys are read here only and are never returned by any endpoint.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DEVDNA_DATA_DIR", BACKEND_DIR / "data")))
    database_url: str = field(default_factory=lambda: os.environ.get("DEVDNA_DATABASE_URL", ""))
    sample_project_dir: Path = BACKEND_DIR / "sample_project"
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.environ.get("DEVDNA_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
            if o.strip()
        ]
    )
    # Upload / ZIP safety limits
    max_upload_bytes: int = field(default_factory=lambda: _int("DEVDNA_MAX_UPLOAD_MB", 150) * 1024 * 1024)
    max_zip_entries: int = field(default_factory=lambda: _int("DEVDNA_MAX_ZIP_ENTRIES", 10000))
    max_uncompressed_bytes: int = field(default_factory=lambda: _int("DEVDNA_MAX_UNCOMPRESSED_MB", 500) * 1024 * 1024)
    max_compression_ratio: int = 100
    max_source_file_bytes: int = 512 * 1024
    # Runtime trace uploads
    max_trace_bytes: int = field(default_factory=lambda: _int("DEVDNA_MAX_TRACE_MB", 50) * 1024 * 1024)
    # Local repositories may only be analysed inside these roots (explicit opt-in).
    allowed_local_roots: list[str] = field(
        default_factory=lambda: [p for p in os.environ.get("DEVDNA_ALLOWED_LOCAL_ROOTS", "").split(os.pathsep) if p]
    )
    # Experiments
    experiment_timeout_s: int = field(default_factory=lambda: _int("DEVDNA_EXPERIMENT_TIMEOUT", 90))
    # Opt-in API auth: when DEVDNA_API_TOKEN is set, every /api route except
    # /api/health requires a matching X-DevDNA-Token header. Empty = local,
    # unauthenticated mode (a startup warning is logged).
    api_token: str = field(default_factory=lambda: os.environ.get("DEVDNA_API_TOKEN", "").strip())
    # Simple per-IP request budget applied to /api routes.
    rate_limit_per_minute: int = field(default_factory=lambda: _int("DEVDNA_RATE_LIMIT_PER_MINUTE", 120))
    # AI
    gemma_model: str = field(default_factory=lambda: os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it"))
    ai_timeout_s: int = field(default_factory=lambda: _int("DEVDNA_AI_TIMEOUT", 180))

    @property
    def api_key(self) -> str | None:
        k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None
        if k and k.strip() in ("your-key-here", "your_api_key_here", "your-api-key", "None", ""):
            return None
        return k

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'devdna.db').as_posix()}"


settings = Settings()
