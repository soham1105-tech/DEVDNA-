import json
from pathlib import Path

from app.cli import main


APP_SOURCE = """\
from fastapi import FastAPI

app = FastAPI()


def work():
    raise RuntimeError("simulated failure")


@app.get("/covered")
def covered():
    return work()


@app.get("/uncovered")
def uncovered():
    return work()
"""


def _repo(root: Path, tested_routes: list[str]) -> Path:
    root.mkdir()
    (root / "app.py").write_text(APP_SOURCE, encoding="utf-8")
    tests = [
        "from fastapi.testclient import TestClient",
        "from app import app",
        "",
        "client = TestClient(app)",
        "",
    ]
    for route in tested_routes:
        name = route.strip("/").replace("/", "_")
        tests.extend([f"def test_{name}():", f"    client.get({route!r})", ""])
    (root / "tests").mkdir()
    (root / "tests" / "test_api.py").write_text("\n".join(tests), encoding="utf-8")
    return root


def _run_cli(tmp_path: Path, repo: Path, capsys, fail: bool) -> tuple[int, str, dict]:
    changed = tmp_path / "changed-files.txt"
    changed.write_text("app.py\n", encoding="utf-8")
    json_output = tmp_path / "impact.json"
    argv = [str(repo), "--changed-files-file", str(changed), "--json-output", str(json_output)]
    if fail:
        argv.append("--fail-on-uncovered-route")
    code = main(argv)
    markdown = capsys.readouterr().out
    return code, markdown, json.loads(json_output.read_text(encoding="utf-8"))


def test_cli_reports_impacted_routes_and_fails_for_uncovered_route(tmp_path, capsys):
    repo = _repo(tmp_path / "repo", ["/covered"])
    code, markdown, report = _run_cli(tmp_path, repo, capsys, fail=True)

    assert code == 1
    assert "GET /covered" in markdown
    assert "GET /uncovered" in markdown
    assert "Tests predicted to fail" in markdown
    assert "route:GET /uncovered" in markdown
    assert report["summary"]["uncovered_routes"] == 1
    assert report["files"][0]["uncovered_routes"] == ["route:GET /uncovered"]
    assert report["files"][0]["tests_predicted_to_fail"]


def test_cli_returns_zero_when_each_impacted_route_has_a_predicted_failing_test(tmp_path, capsys):
    repo = _repo(tmp_path / "repo", ["/covered", "/uncovered"])
    code, markdown, report = _run_cli(tmp_path, repo, capsys, fail=True)

    assert code == 0
    assert "UNCOVERED" not in markdown
    assert report["summary"]["impacted_routes"] == 2
    assert report["summary"]["uncovered_routes"] == 0


def test_cli_without_fail_flag_returns_zero_for_uncovered_route(tmp_path, capsys):
    repo = _repo(tmp_path / "repo", [])
    code, _, report = _run_cli(tmp_path, repo, capsys, fail=False)

    assert code == 0
    assert report["summary"]["uncovered_routes"] == 2
