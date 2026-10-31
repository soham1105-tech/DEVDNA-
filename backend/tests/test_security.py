import io
import stat
import zipfile

import pytest

from app.analyzer.ingest import ZipSecurityError, safe_extract_zip, scan_tree
from app.analyzer.redact import REDACTED, redact
from app.config import settings


def make_zip(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


@pytest.mark.parametrize("name", ["../evil.py", "a/../../evil.py", "/abs/evil.py", "C:/evil.py", "..\\evil.py"])
def test_rejects_path_traversal(tmp_path, name):
    with pytest.raises(ZipSecurityError):
        safe_extract_zip(make_zip({"ok.py": b"x=1", name: b"bad"}), tmp_path / "out")
    assert not (tmp_path / "evil.py").exists()


def test_rejects_symlinks(tmp_path):
    with pytest.raises(ZipSecurityError, match="symbolic link"):
        safe_extract_zip(make_zip({"ok.py": b"x=1"}, symlink="link"), tmp_path / "out")


def test_rejects_too_many_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_zip_entries", 5)
    with pytest.raises(ZipSecurityError, match="entries"):
        safe_extract_zip(make_zip({f"f{i}.py": b"" for i in range(6)}), tmp_path / "out")


def test_rejects_zip_bomb(tmp_path):
    bomb = make_zip({"bomb.txt": b"\0" * (5 * 1024 * 1024)})  # compresses ~1000:1
    with pytest.raises(ZipSecurityError, match="ZIP bomb"):
        safe_extract_zip(bomb, tmp_path / "out")


def test_rejects_oversized_uncompressed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_uncompressed_bytes", 1000)
    with pytest.raises(ZipSecurityError, match="uncompressed"):
        safe_extract_zip(make_zip({"a.py": b"x" * 2000}), tmp_path / "out")


def test_rejects_non_zip(tmp_path):
    with pytest.raises(ZipSecurityError, match="not a valid ZIP"):
        safe_extract_zip(b"hello", tmp_path / "out")


def test_skips_sensitive_and_ignored_files_and_strips_top_folder(tmp_path):
    data = make_zip({
        "repo-main/app.py": b"print(1)",
        "repo-main/.env": b"SECRET=1",
        "repo-main/.env.example": b"SECRET=",
        "repo-main/keys/id_rsa": b"key",
        "repo-main/node_modules/x/index.js": b"",
        "repo-main/.git/config": b"",
        "repo-main/__pycache__/app.cpython-314.pyc": b"",
    })
    stats = safe_extract_zip(data, tmp_path / "out")
    files = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert files == [".env.example", "app.py"]
    assert stats["skipped_sensitive"] == 2 and stats["skipped_ignored"] == 3


def test_scan_tree_ignores_sensitive(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / ".env").write_text("K=v")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("")
    s = scan_tree(tmp_path)
    assert s["files"] == ["a.py"] and s["skipped_sensitive"] == [".env"]


@pytest.mark.parametrize("text,secret", [
    ('api_key = "sk-live-abcdef1234567890XYZ"', "sk-live-abcdef1234567890XYZ"),
    ("password: 'hunter2hunter2'", "hunter2hunter2"),
    ("postgres://admin:s3cr3tpw@db.example.com/x", "s3cr3tpw"),
    ("key = AIza" + "B" * 35, "AIza" + "B" * 35),
    ("token ghp_" + "a" * 36, "ghp_" + "a" * 36),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvwx"),
])
def test_redaction(text, secret):
    out, n = redact(text)
    assert secret not in out and REDACTED in out and n >= 1


def test_redaction_leaves_normal_code_alone():
    code = 'self.base_url = os.environ["SHIPPING_API_URL"]'
    assert redact(code) == (code, 0)


# ---- redaction hardening: one test per secret class -------------------------

def test_redacts_unquoted_assignment_secrets():
    """password = hunter2hunter2 (no quotes) is redacted, but numbers, env
    expansions and placeholders are left alone."""
    out, n = redact("password = hunter2hunter2")
    assert "hunter2hunter2" not in out and REDACTED in out and n == 1

    for safe in ("password = None", "token_count = 12345678", "API_KEY = ${ENV_VAR}",
                 "secret = 'already quoted handled separately'"):
        out, _ = redact(safe)
        if "None" in safe or "${" in safe or "12345678" in safe:
            assert safe == out, safe


def test_redacts_stripe_live_keys():
    key = "sk_live_" + "a1B2c3D4e5F6g7H8i9J0"
    assert len(key) > 24
    # standalone (no sensitive-looking variable name around it)
    out, n = redact(f"config.stripe = {key}")
    assert key not in out and REDACTED in out and n == 1
    out, _ = redact(f"charge('tok', '{key}')")
    assert key not in out


def test_redacts_aws_secret_access_keys():
    key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # classic 40-char shape
    assert len(key) == 40
    out, n = redact(f"backup = {key}")
    assert key not in out and REDACTED in out and n == 1
    out, _ = redact(f"AWS_SECRET_ACCESS_KEY = '{key}'")
    assert key not in out


def test_redacts_secrets_in_url_query_strings():
    text = 'client.get("https://api.example.com/v1/data?api_key=SK1234567890abcdef&x=1")'
    out, n = redact(text)
    assert "SK1234567890abcdef" not in out and n == 1
    assert "?api_key=" + REDACTED in out  # the parameter name survives
    assert "&x=1" in out  # non-sensitive params are untouched

    out, _ = redact("https://gw.example.com/pay?signature=9f8e7d6c5b4a3210&amount=5")
    assert "9f8e7d6c5b4a3210" not in out and "amount=5" in out


def test_redacts_multiline_pem_bodies():
    body = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIICXgIBAAKBgQDfm6Jh6B0JhV8Sg8vJ1hF0uCIuK1oLqyOZmQhRJgUh2b3xUvS0m\n"
        "b3vKQq0e2v9z8uLmM2kQpP3qZmRrT5uYvNwXjH6cD1aS9tGgKwIDAQABAoGBAM0x\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out, n = redact(f"key = {body}")
    assert "MIICXgIBAAKBgQDfm" not in out and "PRIVATE KEY" not in out and n == 1

    # unterminated PEM (truncated file) is redacted to the end of the text
    out, _ = redact("-----BEGIN PRIVATE KEY-----\nline1\nline2")
    assert "line1" not in out and REDACTED in out


def test_redaction_is_idempotent():
    text = 'api_key = "sk-live-abcdef1234567890XYZ"\nurl = "https://x.example.com?token=abcdefghijklmnop"'
    once, n1 = redact(text)
    twice, n2 = redact(once)
    assert once == twice and n2 == 0


def test_stored_snippets_are_redacted_on_read(client, sample_project):
    """A secret stored raw by an older analyzer version is still redacted when
    the API serves the finding: evidence re-redacts on read."""
    from app import db as dbmod
    from app.models import Finding, Project

    secret = "sk_live_" + "a1B2c3D4e5F6g7H8i9J0"
    db = dbmod.SessionLocal()
    try:
        project = db.get(Project, sample_project)
        f = Finding(project_id=sample_project, run_id=project.latest_run_id, rule="t", title="t",
                    severity="info", description="d",
                    evidence=[{"file": "cfg.py", "line": 1, "snippet": f"stripe_key = '{secret}'"}],
                    confidence="high", assumptions=[])
        db.add(f)
        db.commit()
        fid = f.id
    finally:
        db.close()

    rows = client.get(f"/api/projects/{sample_project}/findings").json()
    detail = next(r for r in rows if r["id"] == fid)
    assert secret not in str(detail["evidence"])
    assert REDACTED in str(detail["evidence"])
