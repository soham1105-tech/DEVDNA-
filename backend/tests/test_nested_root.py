"""Regression tests for the nested-container-root import resolution fix.

A whole-repo upload whose code lives under a container directory (e.g.
`backend/`) must produce module names relative to the import root, so
internal imports (`from app.db import ...`) resolve into cross-module
edges. See docs/CHANGE_IMPACT-demo.md for the failure this fixes.
"""
from app.analyzer.python_analyzer import analyse_project


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_container_dir_above_first_package_is_stripped(tmp_path):
    _write(tmp_path, "README.md", "container repo\n")
    _write(tmp_path, "backend/app/__init__.py", "")
    _write(tmp_path, "backend/app/db.py", "def get_conn():\n    return 1\n")
    _write(tmp_path, "backend/app/service.py",
           "from app.db import get_conn\n\n\ndef run():\n    return get_conn()\n")

    result = analyse_project(tmp_path, use_cache=False)
    names = {c["id"] for c in result["components"]}
    assert "module:app.db" in names
    assert "module:app.service" in names
    assert not any(n.startswith("module:backend.") for n in names)
    # the internal import resolved into a function-level edge
    assert any(e["source"] == "func:app.service.run" and e["target"] == "func:app.db.get_conn"
               for e in result["edges"])


def test_deeper_container_layers_are_stripped(tmp_path):
    _write(tmp_path, "services/api/app/__init__.py", "")
    _write(tmp_path, "services/api/app/core.py", "VALUE = 1\n")
    _write(tmp_path, "services/api/app/main.py",
           "from app.core import VALUE\n\n\ndef get():\n    return VALUE\n")

    result = analyse_project(tmp_path, use_cache=False)
    names = {c["id"] for c in result["components"]}
    assert "module:app.core" in names and "module:app.main" in names
    assert any(e["source"] == "func:app.main.get" and e["target"] == "class:app.core.VALUE"
               or e["target"] == "module:app.core"
               for e in result["edges"])


def test_repos_without_init_files_keep_current_behaviour(tmp_path):
    # namespace-style repo: no __init__.py anywhere, imports use full dotted
    # paths relative to the root — stripping anything here would break them
    _write(tmp_path, "a/b.py", "def f():\n    return 1\n")
    _write(tmp_path, "a/c.py", "from a.b import f\n\n\ndef g():\n    return f()\n")

    result = analyse_project(tmp_path, use_cache=False)
    names = {c["id"] for c in result["components"]}
    assert "module:a.b" in names and "module:a.c" in names
    assert any(e["source"] == "func:a.c.g" and e["target"] == "func:a.b.f"
               for e in result["edges"])


def test_package_at_repo_root_is_untouched(tmp_path):
    _write(tmp_path, "shoplite/__init__.py", "")
    _write(tmp_path, "shoplite/db.py", "def get_conn():\n    return 1\n")
    _write(tmp_path, "shoplite/api.py",
           "from shoplite.db import get_conn\n\n\ndef handler():\n    return get_conn()\n")

    result = analyse_project(tmp_path, use_cache=False)
    names = {c["id"] for c in result["components"]}
    assert "module:shoplite.db" in names and "module:shoplite.api" in names
    assert any(e["source"] == "func:shoplite.api.handler" and e["target"] == "func:shoplite.db.get_conn"
               for e in result["edges"])
