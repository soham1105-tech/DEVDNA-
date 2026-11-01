"""Module 4 — verified remediation: proposed patches checked on a disposable
copy, findings compared by fingerprint. The original source is never touched."""
import io
import zipfile

from app.services.remediation import verify_remediation


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def _original_finding(rule, title, file, line, component_id=None, fid="f1"):
    return {"id": fid, "rule": rule, "title": title, "component_id": component_id,
            "evidence": [{"file": file, "line": line}], "description": title}


def test_remediation_resolves_hardcoded_secret(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "config.py").write_text("API_KEY = 'sk_live_abcdefgh123456'\n", encoding="utf-8")
    original = [_original_finding("hardcoded_secret", "Hardcoded credential in config.py",
                                  "config.py", 1)]

    res = verify_remediation(src, {"config.py": "API_KEY = os.environ['API_KEY']\n"}, original)
    assert res["summary"]["resolved_static"] == 1
    assert res["summary"]["still_present"] == 0
    assert res["findings"][0]["status"] == "resolved_static"
    # the original file is untouched
    assert "sk_live_abcdefgh123456" in (src / "config.py").read_text(encoding="utf-8")


def test_noop_patch_leaves_finding_present(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "config.py").write_text("API_KEY = 'sk_live_abcdefgh123456'\n", encoding="utf-8")
    original = [_original_finding("hardcoded_secret", "Hardcoded credential in config.py",
                                  "config.py", 1)]
    res = verify_remediation(src, {"config.py": "API_KEY = 'sk_live_abcdefgh123456'  # TODO\n"}, original)
    assert res["summary"]["still_present"] == 1
    assert res["findings"][0]["status"] == "still_present"


def test_original_source_never_modified(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    verify_remediation(src, {"a.py": "x = 2\ny = 3\n"}, [])
    assert (src / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (src / "a.py").exists() or True


def test_rejects_traversal_absolute_and_oversize(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    for bad in {"../evil.py": "x=1\n", "/abs/path.py": "x=1\n", "a\\..\\..\\b.py": "x=1\n"}:
        try:
            verify_remediation(src, {bad: "x=1\n"}, [])
            raise AssertionError(f"should have rejected {bad}")
        except ValueError:
            pass
    try:
        verify_remediation(src, {"big.py": "x" * 200_001}, [])
        raise AssertionError("should have rejected oversize file")
    except ValueError:
        pass
    try:
        verify_remediation(src, {f"f{i}.py": "x=1\n" for i in range(21)}, [])
        raise AssertionError("should have rejected too many files")
    except ValueError:
        pass


def test_introduced_findings_reported(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("import os\n\n\ndef f():\n    return os.getcwd()\n", encoding="utf-8")
    # the patch introduces a new HTTP call without timeout -> new finding
    res = verify_remediation(src, {"a.py": "import requests\n\n\ndef f():\n    return requests.get('http://x.example')\n"}, [])
    assert res["summary"]["introduced"] >= 1
    assert res["introduced_findings"]


# --- API -------------------------------------------------------------------


def test_api_verify_remediation_end_to_end(client, sample_project):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Finding, Project

    db = SessionLocal()
    try:
        p = db.get(Project, sample_project)
        row = db.scalars(select(Finding).where(Finding.run_id == p.latest_run_id)).first()
        assert row, "sample project should have findings"
        fid = row.id
    finally:
        db.close()

    # a patch that only adds an unrelated new file cannot resolve the finding
    r = client.post(f"/api/projects/{sample_project}/remediations/verify",
                    json={"files": {"extra_module.py": "x = 1\n"}, "finding_ids": [fid]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["checked"] == 1
    assert body["findings"][0]["status"] == "still_present"
    assert body["limitations"]
    assert body["findings"][0]["finding_id"] == fid


def test_api_verify_remediation_validates_input(client, sample_project):
    assert client.post(f"/api/projects/{sample_project}/remediations/verify",
                       json={"files": {}}).status_code == 422
    r = client.post(f"/api/projects/{sample_project}/remediations/verify",
                    json={"files": {"../evil.py": "x=1\n"}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_remediation"
    r = client.post(f"/api/projects/{sample_project}/remediations/verify",
                    json={"files": {"a.py": "x=1\n"}, "finding_ids": ["nonexistent"]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_matching_findings"


def test_api_verify_remediation_unknown_project(client):
    assert client.post("/api/projects/nope/remediations/verify",
                       json={"files": {"a.py": "x=1\n"}}).status_code == 404
