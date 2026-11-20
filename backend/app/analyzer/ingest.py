"""Safe project ingestion: ZIP validation/extraction and source-tree scanning."""
from __future__ import annotations

import fnmatch
import io
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

from app.config import settings

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", ".env", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".nox", "dist", "build", ".idea", ".vscode", "site-packages",
    ".next", "coverage", "htmlcov", ".eggs", ".cache", ".terraform",
}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".o", ".class", ".jar", ".whl", ".egg"}
SENSITIVE_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_dsa*",
    "id_ecdsa*", "id_ed25519*", "credentials.json", "*credentials*.json", "service-account*.json", "secrets.*",
    ".npmrc", ".pypirc", ".netrc", "*.kdbx", ".htpasswd", "*.sqlite", "*.sqlite3", "*.db",
]
SAFE_EXAMPLES = {".env.example", ".env.sample", ".env.template"}

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".java": "Java", ".rb": "Ruby", ".rs": "Rust", ".php": "PHP", ".cs": "C#", ".kt": "Kotlin",
    ".c": "C", ".cpp": "C++", ".h": "C/C++ header", ".sql": "SQL", ".html": "HTML", ".css": "CSS",
    ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".json": "JSON", ".md": "Markdown", ".sh": "Shell",
}
MANIFESTS = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "package.json", "poetry.lock"}


class ZipSecurityError(ValueError):
    """Raised when an archive violates a safety rule. The message is safe to show users."""


def is_sensitive(name: str) -> bool:
    base = PurePosixPath(name).name
    if base in SAFE_EXAMPLES:
        return False
    return any(fnmatch.fnmatch(base.lower(), p) for p in SENSITIVE_PATTERNS)


def is_ignored(rel_parts: tuple[str, ...]) -> bool:
    if any(p in IGNORED_DIRS or p.endswith(".egg-info") for p in rel_parts[:-1]):
        return True
    return PurePosixPath(rel_parts[-1]).suffix.lower() in IGNORED_SUFFIXES


def _validate_member_name(name: str) -> PurePosixPath:
    norm = name.replace("\\", "/")
    p = PurePosixPath(norm)
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":") or p.is_absolute():
        raise ZipSecurityError(f"Archive entry uses an absolute path: {name!r}")
    if any(part == ".." for part in p.parts):
        raise ZipSecurityError(f"Archive entry attempts path traversal: {name!r}")
    if "\x00" in norm:
        raise ZipSecurityError("Archive entry contains a null byte")
    return p


def safe_extract_zip(data: bytes, dest: Path) -> dict:
    """Validate and extract a ZIP archive. Rejects the whole archive on any traversal attempt."""
    if len(data) > settings.max_upload_bytes:
        raise ZipSecurityError(f"Upload exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ZipSecurityError("File is not a valid ZIP archive")

    infos = zf.infolist()
    if len(infos) > settings.max_zip_entries:
        raise ZipSecurityError(f"Archive has {len(infos)} entries; the limit is {settings.max_zip_entries}")

    total = 0
    plan: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    stats = {
        "entries": len(infos),
        "extracted": 0,
        "skipped_ignored": 0,
        "skipped_sensitive": 0,
        "skipped_symlinks": 0,
        "sensitive_files": [],
    }
    for info in infos:
        p = _validate_member_name(info.filename)
        if info.flag_bits & 0x1:
            raise ZipSecurityError("Encrypted archives are not supported")
        if info.is_dir():
            continue
        total += info.file_size
        if total > settings.max_uncompressed_bytes:
            raise ZipSecurityError("Archive expands beyond the uncompressed size limit (possible ZIP bomb)")
        if info.compress_size and info.file_size / max(info.compress_size, 1) > settings.max_compression_ratio and info.file_size > 1024 * 1024:
            raise ZipSecurityError(f"Suspicious compression ratio for {info.filename!r} (possible ZIP bomb)")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            stats["skipped_symlinks"] += 1
            continue
        if is_ignored(p.parts):
            stats["skipped_ignored"] += 1
            continue
        if is_sensitive(p.name):
            stats["skipped_sensitive"] += 1
            stats["sensitive_files"].append(str(p))
            continue
        plan.append((info, p))

    # Strip a single common top-level folder (typical of GitHub ZIP downloads).
    tops = {p.parts[0] for _, p in plan if len(p.parts) > 1}
    strip = len(tops) == 1 and all(len(p.parts) > 1 for _, p in plan)

    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for info, p in plan:
        parts = p.parts[1:] if strip else p.parts
        target = (dest.joinpath(*parts)).resolve()
        if dest not in target.parents:
            raise ZipSecurityError(f"Archive entry escapes the extraction folder: {info.filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with zf.open(info) as src, open(target, "wb") as out:
            while chunk := src.read(65536):
                written += len(chunk)
                if written > info.file_size + 1024:
                    raise ZipSecurityError("Archive entry is larger than declared (possible ZIP bomb)")
                out.write(chunk)
        stats["extracted"] += 1
    return stats


def scan_tree(root: Path) -> dict:
    """Walk a source tree, skipping ignored and sensitive files. Returns file lists and language stats."""
    root = Path(root)
    files: list[str] = []
    languages: dict[str, int] = {}
    manifests: list[str] = []
    skipped_sensitive: list[str] = []
    skipped_large = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.endswith(".egg-info"))
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                continue
            if is_ignored(tuple(rel.split("/"))):
                continue
            if is_sensitive(fn):
                skipped_sensitive.append(rel)
                continue
            if full.stat().st_size > settings.max_source_file_bytes:
                skipped_large += 1
                continue
            files.append(rel)
            lang = LANGUAGES.get(full.suffix.lower())
            if lang:
                languages[lang] = languages.get(lang, 0) + 1
            if fn in MANIFESTS:
                manifests.append(rel)
    return {
        "files": files,
        "file_count": len(files),
        "languages": languages,
        "manifests": manifests,
        "skipped_sensitive": skipped_sensitive,
        "skipped_large": skipped_large,
    }
