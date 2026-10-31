"""Secret detection and redaction.

Everything that leaves the analyzer (UI evidence snippets, reports, AI prompts)
passes through `redact()` first. Values are replaced, never stored.

Redaction is applied on write (when snippets are stored) and again on read
(`serialize.py` re-redacts evidence, observation and AI fields; reports and AI
evidence already re-redacted). Values stored by older versions of this module
are therefore still covered by newer patterns, and `redact()` is idempotent:
already-redacted text is never altered.
"""
from __future__ import annotations

import re

REDACTED = "***REDACTED***"

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("stripe_live_key", re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b")),
    # AWS secret access keys are exactly 40 base64-ish characters. The shape
    # has no identifying prefix, so this pattern is generic and runs last
    # (deliberate over-redaction beats leaking the key).
    ("aws_secret_key", re.compile(r"\b[A-Za-z0-9/+=]{40}\b")),
]

# key = "value" style assignments where the key name looks sensitive
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>[A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?secret)[A-Za-z0-9_]*)
    (?P<sep>\s*[:=]\s*)
    (?P<q>['"])(?P<val>[^'"\n]{4,})(?P=q)
    """
)
# scheme://user:password@host
_URL_CREDS = re.compile(r"(?P<pre>[a-z][a-z0-9+.\-]*://[^:/\s@]+:)(?P<pw>[^@\s/]+)(?P<at>@)", re.I)

# unquoted secrets: password = hunter2hunter2 (bare value, no quotes). The
# value must look like a secret (8+ chars, no spaces) and not be a number,
# boolean/None, an env expansion, a placeholder or an f-string start —
# otherwise legitimate code like `token_count = 10` would be redacted.
_ASSIGNMENT_BARE = re.compile(
    r"""(?ix)
    \b(?P<key>[A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?secret)[A-Za-z0-9_]*)
    (?P<sep>\s*[:=]\s*)
    (?![='"$<{0-9])
    (?P<val>[A-Za-z0-9_\-./+=]{8,})\b
    """
)
_BENIGN_VALUES = {"changeme", "placeholder", "your-key-here", "your_key_here", "none", "null", "true", "false", "redacted"}

# sensitive keys in URL query strings: https://api.example.com/v1?api_key=SECRET
_URL_QUERY_KEY = re.compile(
    r"""(?i)([?&][A-Za-z0-9_]*(?:api[_-]?key|apikey|token|secret|password|passwd|access[_-]?key|client[_-]?secret|credential|signature)[A-Za-z0-9_]*=)(?P<val>[^&\s"']+)"""
)


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_redactions)."""
    if not text:
        return text, 0
    count = 0

    def _sub_all(m):
        nonlocal count
        count += 1
        return REDACTED

    for _, pat in _PATTERNS:
        text = pat.sub(_sub_all, text)

    def _sub_assign(m):
        nonlocal count
        val = m.group("val")
        if val == REDACTED or val.startswith("${") or val.lower() in {"changeme", "your-key-here", "placeholder"}:
            return m.group(0)
        count += 1
        return f"{m.group('key')}{m.group('sep')}{m.group('q')}{REDACTED}{m.group('q')}"

    text = _ASSIGNMENT.sub(_sub_assign, text)

    def _sub_url(m):
        nonlocal count
        count += 1
        return f"{m.group('pre')}{REDACTED}{m.group('at')}"

    text = _URL_CREDS.sub(_sub_url, text)

    def _sub_assign_bare(m):
        nonlocal count
        val = m.group("val")
        if val.lower() in _BENIGN_VALUES:
            return m.group(0)
        count += 1
        return f"{m.group('key')}{m.group('sep')}{REDACTED}"

    text = _ASSIGNMENT_BARE.sub(_sub_assign_bare, text)

    def _sub_query(m):
        nonlocal count
        if m.group("val") == REDACTED:
            return m.group(0)
        count += 1
        return f"{m.group(1)}{REDACTED}"

    text = _URL_QUERY_KEY.sub(_sub_query, text)
    return text, count


def redact_obj(obj):
    """Redact every string in a JSON-like structure (dicts, lists, strings).

    Used on read paths that serve stored snippets (serialize.py) so values
    stored before a pattern existed are still covered on their way out.
    """
    if isinstance(obj, str):
        return redact(obj)[0]
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


def find_secrets(text: str) -> list[dict]:
    """Locate likely secrets by line (values are not returned)."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        _, n = redact(line)
        if n:
            hits.append({"line": lineno, "count": n})
    return hits
