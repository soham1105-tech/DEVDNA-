from unittest.mock import Mock

from fastapi.testclient import TestClient

import main


def _mock_github(monkeypatch):
    tree_response = Mock(status_code=200)
    tree_response.json.return_value = {"tree": [{"type": "blob", "path": "app.py"}]}
    source_response = Mock(status_code=200, text="print('ok')")
    requested_urls = []

    def fake_get(url, headers=None):
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
