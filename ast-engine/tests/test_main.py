from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

import main


def _mock_github(monkeypatch):
    tree_response = Mock(status_code=200)
    tree_response.json.return_value = {"tree": [{"type": "blob", "path": "app.py"}]}
    source_response = Mock(status_code=200, text="print('ok')")
    requested_urls = []

    def fake_get(url, headers=None, timeout=None):
        requested_urls.append(url)
        return tree_response if "api.github.com" in url else source_response

    monkeypatch.setattr(main.requests, "get", fake_get)
    monkeypatch.setattr(
        main,
        "analyze_code_structure",
        lambda source: {"human_architect_score": 87, "ast_stats": {}},
    )
    return requested_urls


def test_analyze_repo_accepts_github_url(monkeypatch):
    requested_urls = _mock_github(monkeypatch)

    response = TestClient(main.app).get(
        "/analyze/repo",
        params={"github_url": "https://github.com/acme/demo.git", "branch": "feature/ast"},
    )

    assert response.status_code == 200
    assert response.json()["repository"] == "acme/demo"
    assert response.json()["branch"] == "feature/ast"
    assert response.json()["overall_architect_score"] == 87
    assert response.json()["analyzed_files"] == 1
    assert requested_urls == [
        "https://api.github.com/repos/acme/demo/git/trees/feature/ast?recursive=1",
        "https://raw.githubusercontent.com/acme/demo/feature/ast/app.py",
    ]


def test_analyze_repo_keeps_owner_repo_parameters(monkeypatch):
    _mock_github(monkeypatch)

    response = TestClient(main.app).get(
        "/analyze/repo",
        params={"owner": "acme", "repo": "demo", "branch": "main"},
    )

    assert response.status_code == 200
    assert response.json()["repository"] == "acme/demo"


def test_analyze_repo_rejects_non_github_url():
    response = TestClient(main.app).get(
        "/analyze/repo",
        params={"github_url": "https://example.com/acme/demo"},
    )

    assert response.status_code == 422


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    main._reset_rate_limit()
    yield
    main._reset_rate_limit()


def test_analyze_requires_token_when_configured(monkeypatch):
    """With AST_ENGINE_API_TOKEN set, /analyze/repo needs a matching
    X-DevDNA-Token header."""
    monkeypatch.setenv("AST_ENGINE_API_TOKEN", "secret-engine-token")
    _mock_github(monkeypatch)
    client = TestClient(main.app)

    r = client.get("/analyze/repo", params={"owner": "acme", "repo": "demo"})
    assert r.status_code == 401
    assert "X-DevDNA-Token" in r.json()["detail"]

    r = client.get("/analyze/repo", params={"owner": "acme", "repo": "demo"},
                   headers={"X-DevDNA-Token": "wrong"})
    assert r.status_code == 401

    r = client.get("/analyze/repo", params={"owner": "acme", "repo": "demo"},
                   headers={"X-DevDNA-Token": "secret-engine-token"})
    assert r.status_code == 200


def test_analyze_github_timeout_is_handled(monkeypatch):
    def timeout_get(url, headers=None, timeout=None):
        raise requests.exceptions.Timeout("github unreachable")

    monkeypatch.setattr(main.requests, "get", timeout_get)
    response = TestClient(main.app).get(
        "/analyze/repo",
        params={"github_url": "https://github.com/acme/demo"},
    )
    assert response.status_code == 504
    assert "GitHub did not respond" in response.json()["detail"]


def test_github_rate_limit_is_surfaced_distinctly(monkeypatch):
    tree_response = Mock(status_code=403)
    monkeypatch.setattr(main.requests, "get", lambda url, headers=None, timeout=None: tree_response)
    response = TestClient(main.app).get(
        "/analyze/repo", params={"owner": "acme", "repo": "demo"})
    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"]


def test_github_missing_repo_is_404(monkeypatch):
    tree_response = Mock(status_code=404)
    monkeypatch.setattr(main.requests, "get", lambda url, headers=None, timeout=None: tree_response)
    response = TestClient(main.app).get(
        "/analyze/repo", params={"owner": "acme", "repo": "ghost"})
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
