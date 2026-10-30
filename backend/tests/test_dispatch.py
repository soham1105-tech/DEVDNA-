"""Tests for the language dispatcher (python / JS / mixed / empty repositories)."""
from pathlib import Path

import pytest

from app.analyzer.dispatch import analyse_repository


@pytest.fixture()
def repo(tmp_path):
    return tmp_path


def _types(result):
    return {c["type"] for c in result["components"]}


def test_python_only_repo_uses_python_analyzer(repo):
    (repo / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/ping')\n"
        "def ping():\n"
        "    return {'ok': True}\n"
    )
    result = analyse_repository(repo, use_cache=False)
    assert "route:GET /ping" in {c["id"] for c in result["components"]}
    assert result["scan"]["analysed_languages"] == ["Python"]


def test_js_only_repo_uses_js_analyzer(repo):
    (repo / "index.js").write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "app.get('/ping', (req, res) => res.json({ ok: true }));\n"
        "export default app;\n"
    )
    result = analyse_repository(repo, use_cache=False)
    assert "route:GET /ping" in {c["id"] for c in result["components"]}
    assert result["scan"]["analysed_languages"] == ["JavaScript"]


def test_mixed_repo_merges_both_analyses(repo):
    (repo / "api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/py')\n"
        "def py():\n"
        "    return {}\n"
    )
    (repo / "web.js").write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "app.get('/js', (req, res) => res.json({}));\n"
        "export default app;\n"
    )
    result = analyse_repository(repo, use_cache=False)
    ids = {c["id"] for c in result["components"]}
    assert "route:GET /py" in ids
    assert "route:GET /js" in ids
    assert set(result["scan"]["analysed_languages"]) == {"Python", "JavaScript"}
    assert result["coverage"]["routes"] == 2  # summed across languages


def test_empty_repo_produces_valid_skeleton(repo):
    result = analyse_repository(repo, use_cache=False)
    assert result["components"] == []
    assert result["edges"] == []
    assert result["scan"]["file_count"] == 0
    assert result["coverage"]["parse_errors"] == []


def test_incremental_second_run_reuses_all_files(repo):
    (repo / "svc.py").write_text("def a():\n    return 1\n")
    (repo / "lib.js").write_text("export function b() { return 2; }\n")
    analyse_repository(repo)
    second = analyse_repository(repo)
    inc = second["scan"]["incremental"]
    assert inc["files_reused"] == 2
    assert inc["files_parsed"] == 0


def test_minified_and_declaration_files_are_skipped(repo):
    (repo / "app.js").write_text("export const x = 1;\n")
    (repo / "vendor.min.js").write_text("var a=1;")
    (repo / "types.d.ts").write_text("export declare const x: number;")
    result = analyse_repository(repo, use_cache=False)
    analysed = {c["file"] for c in result["components"] if c["file"]}
    assert "app.js" in analysed
    assert not any(f in analysed for f in ("vendor.min.js", "types.d.ts"))
