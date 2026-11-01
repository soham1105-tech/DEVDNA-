"""AgentGuard API endpoint: project-scoped audit over real source."""
import json

import pytest

from app.agentguard.service import audit_repository


@pytest.fixture()
def bad_repo(tmp_path):
    root = tmp_path / "bad"
    root.mkdir()
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash"], "defaultMode": "bypassPermissions"}}), encoding="utf-8")
    (root / ".env").write_text("API_KEY=supersecretvalue12345\n", encoding="utf-8")
    return root


def test_agentguard_api_returns_audit(client, bad_repo):
    """The API runs the audit against the project's stored source_path and
    returns findings, graph, coverage and limitations. Uses a throwaway
    project so the session fixture's source_path is never mutated."""
    project = client.post("/api/projects/sample").json()["project"]["id"]

    from app import db as dbmod
    from app.models import Project

    db = dbmod.SessionLocal()
    try:
        p = db.get(Project, project)
        p.source_path = str(bad_repo)
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/projects/{project}/agentguard")
    assert r.status_code == 200
    body = r.json()
    assert body["project"]["id"] == project
    cats = {f["category"] for f in body["findings"]}
    assert "excessive_permissions" in cats
    assert "credential_exposure" in cats
    assert body["graph"]["components"] and body["graph"]["edges"]
    assert body["scanned"]["records"] > 0
    assert isinstance(body["unsupported"], list)
    assert any("nothing was executed" in lim for lim in body["limitations"])


def test_agentguard_api_404_for_unknown_project(client):
    assert client.get("/api/projects/does-not-exist/agentguard").status_code == 404


def test_agentguard_real_repo_demo():
    """Demonstration on a real repo: DevDNA's own backend has CI workflows with
    third-party actions and dependency manifests, so the audit must surface
    supply-chain findings — and it must not crash or leak secrets."""
    from tests.conftest import SAMPLE

    repo_root = SAMPLE.parent.parent  # the DevDNA repository root
    result = audit_repository(repo_root)
    cats = result["category_counts"]
    assert sum(cats.values()) > 0, "the DevDNA repo itself must produce findings"
    # its workflows use third-party actions unpinned -> supply-chain findings
    assert cats["mcp_supply_chain"] >= 1
    blob = json.dumps(result)
    assert "supersecret" not in blob.lower()
