"""Contract tests for the public DevDNA GitHub Action and its CLI entrypoint."""
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SAMPLE = BACKEND / "sample_project"
ACTION = REPO / "action.yml"
EXAMPLE = REPO / "docs" / "examples" / "devdna-action.yml"


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_action_manifest_is_a_composite_action_with_expected_contract():
    action = _load_yaml(ACTION)
    assert action["runs"]["using"] == "composite"

    inputs = action["inputs"]
    assert set(inputs) == {"fail-on-uncovered-route", "comment-on-pr", "github-token"}
    assert inputs["fail-on-uncovered-route"]["default"] == "true"
    assert inputs["comment-on-pr"]["default"] == "true"
    assert inputs["github-token"]["default"] == "${{ github.token }}"

    outputs = action["outputs"]
    assert set(outputs) == {"report-markdown-path", "report-json-path", "uncovered-routes"}
    for output in outputs.values():
        assert output["value"].startswith("${{ steps.report.outputs.")


def test_action_runs_the_existing_cli_without_executing_project_code():
    raw = ACTION.read_text(encoding="utf-8")
    action = _load_yaml(ACTION)
    steps = action["runs"]["steps"]

    assert any(step.get("uses") == "actions/setup-python@v5"
               and step.get("with", {}).get("python-version") == "3.11" for step in steps)
    assert "backend/requirements.lock" in raw
    assert "python -m app.cli" in raw
    assert "$GITHUB_WORKSPACE" in raw
    assert "RUNNER_TEMP" in raw
    assert "<!-- devdna-pr-impact -->" in raw
    assert "--fail-on-uncovered-route" in raw
    assert "pull_request_target" not in raw


def test_example_workflow_is_fork_safe_and_minimal():
    raw = EXAMPLE.read_text(encoding="utf-8")
    example = _load_yaml(EXAMPLE)
    trigger = example.get("on", example.get(True))
    assert "pull_request" in trigger
    assert "pull_request_target" not in raw
    assert example["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "uses: soham1105-tech/DEVDNA-@main" in raw
    assert "actions/checkout@v4" in raw


def test_cli_still_produces_reports_for_sample_project(tmp_path):
    markdown = tmp_path / "devdna-impact.md"
    report = tmp_path / "devdna-impact.json"
    proc = subprocess.run(
        [
            sys.executable, "-m", "app.cli", str(SAMPLE),
            "--changed-file", "shoplite/services/pricing.py",
            "--markdown-output", str(markdown),
            "--json-output", str(report),
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert markdown.is_file() and report.is_file()
    assert "DevDNA PR impact report" in markdown.read_text(encoding="utf-8")

    data = json.loads(report.read_text(encoding="utf-8"))
    changed = data["files"][0]
    assert changed["file"] == "shoplite/services/pricing.py"
    assert any(route["name"] == "POST /api/orders" for route in changed["impacted_routes"])
    assert data["analysis_mode"] == "static prediction only; no experiments were run"
