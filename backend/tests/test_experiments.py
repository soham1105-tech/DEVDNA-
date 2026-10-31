import subprocess
from pathlib import Path

import pytest

from app.experiments import runner
from app.experiments.runner import _run_pytest, _scrubbed_env, choose_mode, compare


def _impact(preds):
    return {"tests": [{"name": n, "nodeid": f"tests/t.py::{n}", "junit_key": f"tests.t::{n}", "prediction": p, "path": []}
                      for n, p in preds.items()]}


def _obs(baseline, faulted, triggers=3, errors=None):
    def res(d):
        return {f"tests.t::{n}": {"outcome": o, "message": "", "time": 0} for n, o in d.items()}
    return {"baseline": {"timed_out": False, "results": res(baseline), "fault_state": {}},
            "faulted": {"timed_out": False, "results": res(faulted),
                        "fault_state": {"triggers": triggers, "errors": errors or []}}}


def test_compare_confirmed():
    c = compare(_impact({"a": "fail", "b": "pass"}), _obs({"a": "passed", "b": "passed"}, {"a": "error", "b": "passed"}))
    assert c["status"] == "prediction_confirmed"
    assert c["metrics"]["accuracy"] == 1.0 and c["metrics"]["true_positive"] == 1


def test_compare_partial_and_contradicted():
    c = compare(_impact({"a": "fail", "b": "fail"}), _obs({"a": "passed", "b": "passed"}, {"a": "failed", "b": "passed"}))
    assert c["status"] == "partially_confirmed" and c["metrics"]["false_positive"] == 1
    c = compare(_impact({"a": "fail", "b": "pass"}), _obs({"a": "passed", "b": "passed"}, {"a": "passed", "b": "failed"}))
    assert c["status"] == "prediction_contradicted"


def test_compare_inconclusive_cases():
    imp = _impact({"a": "fail"})
    assert compare(imp, _obs({}, {}))["status"] == "inconclusive"  # baseline produced nothing
    assert compare(imp, _obs({"a": "passed"}, {"a": "failed"}, triggers=0))["status"] == "inconclusive"
    assert compare(imp, _obs({"a": "passed"}, {"a": "failed"}, errors=["boom"]))["status"] == "inconclusive"
    # a test failing in baseline is excluded, never counted as confirmation
    c = compare(_impact({"a": "fail"}), _obs({"a": "failed"}, {"a": "failed"}))
    assert c["status"] == "inconclusive" and c["rows"][0]["verdict"] == "excluded"


def test_uncertain_predictions_never_count_as_matches():
    c = compare(_impact({"a": "may_fail", "b": "pass"}), _obs({"a": "passed", "b": "passed"}, {"a": "passed", "b": "passed"}))
    assert c["rows"][0]["verdict"] == "uncertain_passed"
    assert c["metrics"]["true_negative"] == 1 and c["metrics"]["uncertain_passed"] == 1


def test_untrusted_projects_are_blocked_without_docker(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    mode, reason = choose_mode(trusted=False)
    assert mode == "blocked" and "No code was executed" in reason
    assert choose_mode(trusted=True)[0] == "local_harness"


def test_sandbox_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-secret")
    env = _scrubbed_env(tmp_path, tmp_path, {"kind": "db_unavailable"}, tmp_path / "r.json")
    assert "GEMINI_API_KEY" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert "super-secret" not in str(env)
    assert env["TMP"] == str(tmp_path)


def test_docker_run_uses_required_isolation_flags_and_named_container(tmp_path, monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    _run_pytest(tmp_path, tmp_path, None, "baseline", "docker", "devdna-sandbox:test")

    docker_command = commands[0]
    assert docker_command[:2] == ["docker", "run"]
    assert "--name" in docker_command
    assert "--network" in docker_command and docker_command[docker_command.index("--network") + 1] == "none"
    assert "--read-only" in docker_command
    assert docker_command[docker_command.index("--cap-drop") + 1] == "ALL"
    assert "devdna-sandbox:test" in docker_command
    assert "python" in docker_command and "-m" in docker_command and "pytest" in docker_command
    assert f"{tmp_path}:/out:rw" in docker_command
    if runner.os.name != "nt":
        assert tmp_path.stat().st_mode & 0o777 == 0o777


def test_docker_timeout_force_removes_named_container(tmp_path, monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(command, timeout=1, output=b"timed out")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = _run_pytest(tmp_path, tmp_path, None, "baseline", "docker", "devdna-sandbox:test")

    run_command = commands[0]
    container_name = run_command[run_command.index("--name") + 1]
    assert result["timed_out"] is True
    assert commands[1] == ["docker", "rm", "--force", container_name]


def test_execute_uses_two_fresh_project_copies(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "test_file.py").write_text("original", encoding="utf-8")
    copies = []

    def fake_copytree(src, dst, ignore=None):
        copies.append(Path(dst))
        Path(dst).mkdir(parents=True)
        (Path(dst) / "test_file.py").write_text(Path(src, "test_file.py").read_text(encoding="utf-8"), encoding="utf-8")

    calls = []

    def fake_run(project_dir, workdir, fault, label, mode, sandbox_image=None):
        initial_content = Path(project_dir, "test_file.py").read_text(encoding="utf-8")
        calls.append((Path(project_dir), fault, label, sandbox_image, initial_content))
        Path(project_dir, "test_file.py").write_text(label, encoding="utf-8")
        return {"label": label}

    monkeypatch.setattr(runner.shutil, "copytree", fake_copytree)
    monkeypatch.setattr(runner, "_run_pytest", fake_run)
    result = runner.execute(source, {"kind": "test"}, "local_harness")

    assert len(copies) == 2 and copies[0] != copies[1]
    assert calls[0][0] != calls[1][0]
    assert calls[0][0].name == "baseline-project" and calls[1][0].name == "faulted-project"
    assert [call[4] for call in calls] == ["original", "original"]
    assert result["baseline"]["label"] == "baseline" and result["faulted"]["label"] == "faulted"


def test_execute_builds_image_with_project_dependencies(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    monkeypatch.setattr(runner.shutil, "copytree", lambda src, dst, ignore=None: Path(dst).mkdir())
    built = []

    def fake_build(project_dir, workdir):
        built.append((project_dir, workdir, runner._dependency_requirements(project_dir)))
        return "devdna-sandbox:test"

    calls = []
    monkeypatch.setattr(runner, "_build_sandbox_image", fake_build)
    monkeypatch.setattr(
        runner,
        "_run_pytest",
        lambda project, workdir, fault, label, mode, image=None: calls.append((label, image)) or {"label": label},
    )
    removed = []
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: removed.append(command) or subprocess.CompletedProcess(command, 0))

    runner.execute(source, {"kind": "test"}, "docker")

    assert built[0][2] == "fastapi==0.115.0"
    assert [image for _, image in calls] == ["devdna-sandbox:test"] * 2
    assert removed == [["docker", "image", "rm", "--force", "devdna-sandbox:test"]]


def test_dependency_requirements_reads_project_manifests(tmp_path):
    (tmp_path / "requirements.txt").write_text("httpx>=0.27\n-e .\n", encoding="utf-8")
    assert runner._dependency_requirements(tmp_path) == "httpx>=0.27"

    (tmp_path / "requirements.txt").unlink()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi>=0.115", "httpx>=0.27"]\n',
        encoding="utf-8",
    )
    assert runner._dependency_requirements(tmp_path) == "fastapi>=0.115\nhttpx>=0.27"

    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "fastapi"\nversion = "0.115.0"\n',
        encoding="utf-8",
    )
    assert runner._dependency_requirements(tmp_path) == "fastapi==0.115.0"


@pytest.mark.skipif(not runner.docker_available(), reason="Docker daemon or CLI is unavailable")
def test_real_docker_sandbox_writes_reports_as_sandbox_user(tmp_path):
    project = tmp_path / "docker-project"
    project.mkdir()
    (project / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = runner.execute(project, {"kind": "test"}, "docker")

    assert result["baseline"]["counts"]["passed"] == 1, result["baseline"]["output"]
    assert result["faulted"]["counts"]["passed"] == 1, result["faulted"]["output"]
    assert result["baseline"]["results"] and result["faulted"]["results"]
    assert result["faulted"]["fault_state"] == {
        "fault": {"kind": "test"}, "applied": [], "triggers": 0, "network_blocked_attempts": 0, "errors": [],
    }


def test_fault_spec_for_each_scenario(sample_graph):
    from app.graph.engine import compute_impact

    spec = runner.fault_spec(compute_impact(sample_graph, "missing_env_var", "env:SHIPPING_API_URL"))
    assert spec == {"kind": "missing_env_var", "var": "SHIPPING_API_URL"}
    spec = runner.fault_spec(compute_impact(sample_graph, "worker_unavailable", "worker:shoplite.worker.NotificationWorker"))
    assert spec["launchers"] == ["start"] and spec["cls"] == "NotificationWorker"


def test_real_db_experiment_on_sample(sample_graph):
    """Executes the sample test suite twice in the restricted harness with the DB fault injected."""
    from app.graph.engine import compute_impact
    from pathlib import Path

    SAMPLE = Path(__file__).resolve().parent.parent / "sample_project"

    impact = compute_impact(sample_graph, "db_unavailable", "database:sqlite")
    obs = runner.execute(SAMPLE, runner.fault_spec(impact), "local_harness")
    assert obs["baseline"]["counts"]["passed"] == 12, obs["baseline"]["output"][-2000:]
    assert obs["faulted"]["fault_state"]["triggers"] > 0
    assert any("sqlite3.connect" in a for a in obs["faulted"]["fault_state"]["applied"])
    c = compare(impact, obs)
    assert c["status"] == "prediction_confirmed", c
    assert all(r["fault_marker"] for r in c["rows"] if r["observed"] in ("failed", "error"))


def test_stale_queued_experiment_fails_on_startup_recovery(client, sample_project):
    """An experiment left queued by a previous process becomes failed with the
    restart message when the new process starts. It is kept, not re-run or
    deleted."""
    from datetime import datetime, timezone

    from app import db as dbmod
    from app.api.experiments import recover_stale_experiments
    from app.models import Experiment

    pred = client.post(f"/api/projects/{sample_project}/predictions",
                       json={"scenario": "db_unavailable", "target_id": "database:sqlite"}).json()

    db = dbmod.SessionLocal()
    try:
        stale = Experiment(project_id=sample_project, prediction_id=pred["id"],
                           status="queued", approved_at=datetime.now(timezone.utc))
        db.add(stale)
        db.commit()
        exp_id = stale.id
        # a finished experiment from before must be left untouched
        done = Experiment(project_id=sample_project, prediction_id=pred["id"], status="prediction_confirmed")
        db.add(done)
        db.commit()
        done_id = done.id
    finally:
        db.close()

    recovered = recover_stale_experiments()

    assert recovered == 1
    db = dbmod.SessionLocal()
    try:
        exp = db.get(Experiment, exp_id)
        assert exp.status == "failed"
        assert exp.error == "Server restarted before this experiment finished — please re-run it."
        assert exp.finished_at is not None
        untouched = db.get(Experiment, done_id)
        assert untouched.status == "prediction_confirmed" and untouched.error is None
    finally:
        db.close()


def test_dependency_requirements_translates_poetry_constraints(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.11"\nfoo = "^1.2.3"\nbar = "~1.2.3"\n',
        encoding="utf-8",
    )
    assert runner._dependency_requirements(tmp_path) == "foo>=1.2.3,<2.0.0\nbar>=1.2.3,<1.3.0"


def test_dependency_requirements_rejects_pipfile_star(tmp_path):
    (tmp_path / "Pipfile.lock").write_text(
        '{"default": {"requests": {"version": "*"}}, "develop": {}}', encoding="utf-8",
    )
    with pytest.raises(ValueError, match="wildcard"):
        runner._dependency_requirements(tmp_path)


def test_build_sandbox_rejects_risky_requirements_before_pip(tmp_path, monkeypatch):
    """--index-url, wildcard and git+ specs raise before any subprocess runs,
    so nothing unsafe ever reaches pip or docker."""
    (tmp_path / "requirements.txt").write_text(
        "--index-url https://evil.example.com/simple\n"
        "foo*\n"
        "bar @ git+https://github.com/evil/evil\n",
        encoding="utf-8",
    )
    called = []
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: called.append(command))

    with pytest.raises(ValueError):
        runner._build_sandbox_image(tmp_path, tmp_path)

    assert called == []  # pip download and docker build were never invoked


def test_build_sandbox_vendors_wheels_and_builds_offline(tmp_path, monkeypatch):
    """A normal pinned requirements.txt vendors binary wheels on the host and
    the docker build runs with --network=none; no index options anywhere."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        import subprocess as sp
        return sp.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    workdir = tmp_path / "work"
    workdir.mkdir()
    image = runner._build_sandbox_image(source, workdir)

    assert image.startswith("devdna-sandbox:")
    vendor_cmd, build_cmd = calls[0], calls[1]
    assert vendor_cmd[1:3] == ["-m", "pip"] and "download" in vendor_cmd
    assert "--only-binary=:all:" in vendor_cmd  # wheels only: no setup.py execution
    assert "--index-url" not in vendor_cmd and "--extra-index-url" not in vendor_cmd
    assert "--network=none" in build_cmd and build_cmd[0] == "docker"

    context = workdir / "image-context"
    reqs = (context / "requirements.txt").read_text(encoding="utf-8")
    assert reqs == "fastapi==0.115.0\npytest>=8.3\n"
    assert "--index-url" not in reqs
    assert (context / "wheels").is_dir()
