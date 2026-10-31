import io
import zipfile
from datetime import datetime, timedelta, timezone

from app import db as dbmod
from app.config import settings
from app.models import AnalysisRun, Component, Finding, FindingSuppression, FindingSuppressionAudit, Project


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] == "ok" and body["ai"]["configured"] is False and body["ai"]["model"]


def test_ai_status_without_key(client):
    r = client.get("/api/ai/status?check=true").json()
    assert r["configured"] is False and "gemma" in r["model"]


def test_sample_analysis_and_persistence(client, sample_project):
    detail = client.get(f"/api/projects/{sample_project}").json()
    assert detail["trusted"] is True and detail["latest_run"]["status"] == "completed"
    assert "source_path" not in detail
    stages = [s["stage"] for s in detail["latest_run"]["stages"]]
    assert stages[0] == "Scanning files" and "Generating findings" in stages
    db = dbmod.SessionLocal()
    try:
        p = db.get(Project, sample_project)
        assert db.query(Component).filter(Component.run_id == p.latest_run_id).count() > 50
        assert db.query(Finding).filter(Finding.project_id == sample_project).count() > 0
    finally:
        db.close()


def test_architecture_and_component_detail(client, sample_project):
    arch = client.get(f"/api/projects/{sample_project}/architecture").json()
    assert len(arch["components"]) > 50 and len(arch["edges"]) > 50
    r = client.get(f"/api/projects/{sample_project}/components/func:shoplite.db.get_connection").json()
    assert r["file"] == "shoplite/db.py"
    assert any(e["target"] == "database:sqlite" for e in r["outgoing"])
    assert "route:GET /health" in r["upstream"]
    assert "sqlite3.connect" in r["source_excerpt"]
    assert client.get(f"/api/projects/{sample_project}/components/func:nope").status_code == 404


def test_findings_and_review_status(client, sample_project):
    findings = client.get(f"/api/projects/{sample_project}/findings").json()
    rules = {f["rule"] for f in findings}
    assert {"startup_dependency", "shared_dependency", "required_env_var"} <= rules
    fid = findings[0]["id"]
    assert client.patch(f"/api/findings/{fid}", json={"review_status": "acknowledged"}).json()["review_status"] == "acknowledged"
    bad = client.patch(f"/api/findings/{fid}", json={"review_status": "deleted"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"


def test_codeowners_assigns_finding_owner(client):
    source = {
        "CODEOWNERS": "*.py @platform-team\n",
        "app.py": "from fastapi import FastAPI\napp = FastAPI()\n"
                  "@app.get('/health')\ndef health():\n    return {'ok': True}\n",
    }
    response = client.post("/api/projects/upload",
                           files={"file": ("owned.zip", _zip(source), "application/zip")})
    assert response.status_code == 201, response.text
    project_id = response.json()["project"]["id"]
    findings = client.get(f"/api/projects/{project_id}/findings").json()
    assert findings and all(finding["owner"] == "@platform-team" for finding in findings)
    client.delete(f"/api/projects/{project_id}")


def test_finding_suppression_requires_reason_and_expires(client, sample_project):
    finding = client.get(f"/api/projects/{sample_project}/findings").json()[0]
    url = f"/api/findings/{finding['id']}/suppress"
    expiry = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    missing_reason = client.post(url, json={"owner": "team@example.com", "expires_at": expiry})
    assert missing_reason.status_code == 422

    suppressed = client.post(url, json={
        "reason": "Accepted until the dependency migration completes",
        "owner": "team@example.com",
        "expires_at": expiry,
    })
    assert suppressed.status_code == 200 and suppressed.json()["is_suppressed"] is True
    assert client.get(f"/api/findings/{finding['id']}/suppression-audit").json()[0]["action"] == "suppressed"

    reanalysis = client.post(f"/api/projects/{sample_project}/analyze")
    assert reanalysis.status_code in (200, 202)
    persisted = next(item for item in client.get(f"/api/projects/{sample_project}/findings").json()
                     if item["fingerprint"] == finding["fingerprint"])
    assert persisted["is_suppressed"] is True

    db = dbmod.SessionLocal()
    try:
        suppression = db.query(FindingSuppression).filter_by(
            project_id=sample_project, fingerprint=finding["fingerprint"],
        ).one()
        suppression.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    current = next(item for item in client.get(f"/api/projects/{sample_project}/findings").json()
                   if item["fingerprint"] == finding["fingerprint"])
    assert current["is_suppressed"] is False
    assert current["suppression"]["active"] is False


def test_drift_reports_new_route_and_blast_radius_growth(client):
    uploaded = client.post("/api/projects/upload", files={
        "file": ("drift.zip", _zip({"app.py": "def main():\n    return 1\n"}), "application/zip"),
    })
    assert uploaded.status_code == 201, uploaded.text
    project_id = uploaded.json()["project"]["id"]
    db = dbmod.SessionLocal()
    try:
        project = db.get(Project, project_id)
        previous_id = project.latest_run_id
        previous_components = db.query(Component).filter_by(run_id=previous_id).all()
        latest = AnalysisRun(project_id=project_id, status="completed", stage="Completed",
                             progress=1.0, finished_at=datetime.now(timezone.utc))
        db.add(latest)
        db.flush()
        for component in previous_components:
            metrics = dict(component.metrics or {})
            if component.type == "function":
                metrics["dependents"] = metrics.get("dependents", 0) + 2
            db.add(Component(run_id=latest.id, id=component.id, type=component.type, name=component.name,
                             file=component.file, line=component.line, data=component.data or {}, metrics=metrics))
        db.add(Component(run_id=latest.id, id="route:GET /new", type="route", name="GET /new",
                         file="app.py", line=10, data={}, metrics={"dependents": 0}))
        project.latest_run_id = latest.id
        db.commit()
    finally:
        db.close()

    drift = client.get(f"/api/projects/{project_id}/drift")
    assert drift.status_code == 200, drift.text
    body = drift.json()
    assert body["previous_run_id"] == previous_id
    assert [route["id"] for route in body["routes_added"]] == ["route:GET /new"]
    assert body["blast_radius_growth"] and body["blast_radius_growth"][0]["growth"] == 2
    client.delete(f"/api/projects/{project_id}")


def test_scenarios_and_analytics(client, sample_project):
    sc = {s["id"]: s for s in client.get(f"/api/projects/{sample_project}/scenarios").json()}
    assert sc["db_unavailable"]["targets"][0]["id"] == "database:sqlite"
    a = client.get(f"/api/projects/{sample_project}/analytics").json()
    assert a["component_counts"]["route"] == 6 and a["centrality"]


def test_prediction_validation(client, sample_project):
    r = client.post(f"/api/projects/{sample_project}/predictions", json={"scenario": "db_unavailable", "target_id": "env:SHIPPING_API_URL"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_scenario"
    r = client.post(f"/api/projects/{sample_project}/predictions", json={"scenario": "nope", "target_id": "database:sqlite"})
    assert r.status_code == 422


def test_ai_requires_consent_and_key(client, sample_project):
    pred = client.post(f"/api/projects/{sample_project}/predictions",
                       json={"scenario": "db_unavailable", "target_id": "database:sqlite"}).json()
    r = client.post(f"/api/predictions/{pred['id']}/explain", json={"preview": True})
    assert r.status_code == 200 and r.json()["preview"] is True
    items = r.json()["evidence"]["items"]
    assert items and all(i["ref"].startswith("E") for i in items)
    r = client.post(f"/api/predictions/{pred['id']}/explain", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "consent_required"
    r = client.post(f"/api/predictions/{pred['id']}/explain", json={"consent": True})
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"
    r = client.post(f"/api/projects/{sample_project}/ai/chat", json={"question": "", "consent": True})
    assert r.status_code == 422


def test_experiment_requires_approval_and_runs(client, sample_project):
    pred = client.post(f"/api/projects/{sample_project}/predictions",
                       json={"scenario": "external_api_unavailable", "target_id": "external:SHIPPING_API_URL"}).json()
    r = client.post(f"/api/predictions/{pred['id']}/experiments", json={"approved": False})
    assert r.status_code == 400 and r.json()["error"]["code"] == "approval_required"
    r = client.post(f"/api/predictions/{pred['id']}/experiments", json={"approved": True})
    assert r.status_code == 202
    exp = client.get(f"/api/experiments/{r.json()['id']}").json()
    # validation short-circuits test_order_validation before the handler: an honest partial confirmation
    assert exp["status"] == "partially_confirmed", exp.get("error")
    mism = [row for row in exp["comparison"]["rows"] if row["verdict"] == "mismatch"]
    assert [m["test"] for m in mism] == ["test_order_validation"]
    assert exp["mode"] == "local_harness"

    md = client.get(f"/api/experiments/{exp['id']}/report?format=md")
    assert md.status_code == 200 and "# Experiment report" in md.text and "attachment" in md.headers["content-disposition"]
    js = client.get(f"/api/experiments/{exp['id']}/report?format=json").json()
    assert js["status"] == "partially_confirmed" and js["recommended_next_test"]
    lst = client.get(f"/api/projects/{sample_project}/experiments").json()
    assert lst and "output" not in lst[0]["observation"].get("faulted", {})


def test_project_report(client, sample_project):
    r = client.get(f"/api/projects/{sample_project}/report?format=md")
    assert r.status_code == 200 and "## Findings" in r.text and "Criticality formula" in r.text
    assert client.get(f"/api/projects/{sample_project}/report?format=json").json()["report_type"] == "devdna_project_report"
    assert client.get(f"/api/projects/{sample_project}/report?format=pdf").status_code == 422
    assert len(client.get(f"/api/projects/{sample_project}/reports").json()) >= 2


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in entries.items():
            zf.writestr(k, v)
    return buf.getvalue()


def test_upload_flow_and_blocked_experiment(client, monkeypatch):
    from app.experiments import runner
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    data = _zip({"proj/app.py": "import sqlite3\n\ndef get():\n    return sqlite3.connect('x.db')\n",
                 "proj/.env": "GEMINI_API_KEY=should-never-be-read"})
    r = client.post(
        "/api/projects/upload",
        files={"file": ("proj.zip", data, "application/zip")},
        data={"github_url": "https://github.com/acme/demo.git", "github_branch": "feature/ast"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["project"]["id"]
    assert r.json()["project"]["ast_repository"] == {
        "owner": "acme", "repo": "demo", "branch": "feature/ast",
    }
    assert client.get(f"/api/projects/{pid}").json()["ast_repository"] == {
        "owner": "acme", "repo": "demo", "branch": "feature/ast",
    }
    assert r.json()["project"]["ingest_stats"]["skipped_sensitive"] == 1
    assert client.get(f"/api/runs/{r.json()['run']['id']}").json()["status"] == "completed"
    pred = client.post(f"/api/projects/{pid}/predictions", json={"scenario": "db_unavailable", "target_id": "database:sqlite"}).json()
    exp = client.post(f"/api/predictions/{pred['id']}/experiments", json={"approved": True}).json()
    exp = client.get(f"/api/experiments/{exp['id']}").json()
    assert exp["status"] == "blocked" and "No code was executed" in exp["error"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_github_project_download_import_and_analysis(client, monkeypatch):
    from app.api import projects as project_api

    downloads = []

    def download_archive(owner, repo, branch):
        downloads.append((owner, repo, branch))
        return _zip({"demo-main/app.py": "def main():\n    return 'ok'\n"})

    monkeypatch.setattr(project_api, "_download_github_archive", download_archive)

    response = client.post("/api/projects/github", json={
        "github_url": "https://github.com/acme/demo",
        "github_branch": "feature/ast",
    })

    assert response.status_code == 201, response.text
    project = response.json()["project"]
    assert project["source_type"] == "github"
    assert project["ast_repository"] == {
        "owner": "acme", "repo": "demo", "branch": "feature/ast",
    }
    assert downloads == [("acme", "demo", "feature/ast")]
    assert client.get(f"/api/runs/{response.json()['run']['id']}").json()["status"] == "completed"
    assert client.get(f"/api/projects/{project['id']}/analytics").status_code == 200
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204


def test_upload_rejections(client):
    r = client.post("/api/projects/upload", files={"file": ("x.txt", b"hi", "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/projects/upload", files={"file": ("x.zip", _zip({"../../evil.py": "x"}), "application/zip")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsafe_archive"
    r = client.post("/api/projects/upload", files={"file": ("x.zip", _zip({"readme.md": "x"}), "application/zip")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_python"
    r = client.post(
        "/api/projects/upload",
        files={"file": ("x.zip", _zip({"app.py": "print('ok')"}), "application/zip")},
        data={"github_url": "https://example.com/owner/repo"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_github_repository"


def test_local_projects_disabled_by_default(client):
    r = client.post("/api/projects/local", json={"path": "C:/"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "local_disabled"


def test_unknown_project_404(client):
    r = client.get("/api/projects/doesnotexist/architecture")
    assert r.status_code == 404 and r.json()["error"]["code"] == "project_not_found"


def test_chat_evidence_includes_deterministic_dependents(client, sample_project):
    r = client.post(f"/api/projects/{sample_project}/ai/chat",
                    json={"question": "Which routes break if the shipping API is down?", "preview": True})
    items = r.json()["evidence"]["items"]
    dependents = [i for i in items if i["kind"] == "dependents" and "external:SHIPPING_API_URL" in i["title"]]
    assert dependents and "POST /api/orders" in dependents[0]["content"]


def test_multimodal_audit_and_dynamic_scenario(client, sample_project):
    # Multimodal audit validation
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    r = client.post(
        f"/api/projects/{sample_project}/ai/multimodal-audit",
        files={"file": ("diagram.png", fake_png, "image/png")},
        data={"consent": "false"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "consent_required"

    r = client.post(
        f"/api/projects/{sample_project}/ai/multimodal-audit",
        files={"file": ("diagram.png", fake_png, "image/png")},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "consent_required"

    r = client.post(
        f"/api/projects/{sample_project}/ai/multimodal-audit",
        files={"file": ("diagram.png", fake_png, "image/png")},
        data={"consent": "true"},
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"

    for fields, expected_code in (
        ({"diagram_type": "x" * 101}, "diagram_type_too_long"),
        ({"notes": "x" * 2001}, "notes_too_long"),
    ):
        response = client.post(
            f"/api/projects/{sample_project}/ai/multimodal-audit",
            files={"file": ("diagram.png", fake_png, "image/png")},
            data={"consent": "true", **fields},
        )
        assert response.status_code == 400 and response.json()["error"]["code"] == expected_code

    response = client.post(
        f"/api/projects/{sample_project}/ai/multimodal-audit",
        files={"file": ("diagram.gif", fake_png, "image/gif")},
        data={"consent": "true"},
    )
    assert response.status_code == 415 and response.json()["error"]["code"] == "unsupported_media_type"

    response = client.post(
        f"/api/projects/{sample_project}/ai/multimodal-audit",
        files={"file": ("diagram.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")},
        data={"consent": "true"},
    )
    assert response.status_code == 413 and response.json()["error"]["code"] == "file_too_large"

    latest = client.get(f"/api/projects/{sample_project}/ai/multimodal-audit/latest")
    assert latest.status_code == 200

    # Dynamic scenario preview
    r = client.post(
        f"/api/projects/{sample_project}/ai/dynamic-scenario",
        json={"prompt": "Simulate Redis cache eviction storm", "preview": True},
    )
    assert r.status_code == 200 and r.json()["preview"] is True

    # Prediction with dynamic custom_spec and deterministic impact assessment
    custom_spec = {
        "label": "Custom Redis Failure",
        "fault": "Redis socket reset error on auth pipeline",
        "exceptions": ["ConnectionError", "RuntimeError"],
        "target_types": ["database"],
    }
    pred_res = client.post(
        f"/api/projects/{sample_project}/predictions",
        json={
            "scenario": "custom_redis_fault",
            "target_id": "database:sqlite",
            "custom_spec": custom_spec,
        },
    )
    assert pred_res.status_code == 201
    pred = pred_res.json()
    assert pred["scenario"] == "custom_redis_fault"
    assert "neuro_symbolic_simulation" in pred["impact"]
    sim = pred["impact"]["neuro_symbolic_simulation"]
    assert sim["cascading_risk_score"] > 0
    assert sim["blast_radius_assessment"] in ("CRITICAL", "HIGH", "MODERATE")
    assert sim["engine"] == "DevDNA deterministic impact engine"
    assert sim["model"] is None and sim["ai_model_used"] is False
    assert sim["neuro_symbolic"] is False
    assert "not an AI prediction" in sim["method"] and "not a failure probability" in sim["method"]
    assert pred["impact"]["custom_fault"]["kind"] == "dynamic_fault"


def test_token_auth_required_when_configured(client, monkeypatch):
    """With DEVDNA_API_TOKEN set, /api routes need a matching X-DevDNA-Token;
    /api/health stays open."""
    monkeypatch.setattr(settings, "api_token", "secret-token-1")
    r = client.get("/api/projects")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    r = client.get("/api/projects", headers={"X-DevDNA-Token": "secret-token-1"})
    assert r.status_code == 200

    r = client.get("/api/projects", headers={"X-DevDNA-Token": "wrong"})
    assert r.status_code == 401

    # health remains reachable without a token even in token mode
    assert client.get("/api/health").status_code == 200


def test_token_auth_off_by_default(client):
    """Without DEVDNA_API_TOKEN the local behaviour is unchanged."""
    assert not settings.api_token
    assert client.get("/api/projects").status_code == 200


def test_rate_limit_burst(client):
    """A burst over the per-IP budget gets 429 with code rate_limited."""
    from app.api import guard

    guard.set_rate_limit(3)
    try:
        guard.reset_rate_limit()
        for _ in range(3):
            assert client.get("/api/health").status_code == 200
        r = client.get("/api/health")
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"
        # the budget recovers after the window; reset is immediate in tests
        guard.reset_rate_limit()
        assert client.get("/api/health").status_code == 200
    finally:
        guard.set_rate_limit(None)
        guard.reset_rate_limit()
