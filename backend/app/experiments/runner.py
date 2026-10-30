"""Controlled experiment execution and prediction-vs-observation comparison.

Execution policy (enforced in `choose_mode`):
  * Only the bundled, trusted sample project runs in the local restricted harness.
  * Any uploaded / local project requires Docker (no network, read-only source,
    memory/CPU/pid limits). Without Docker the experiment is BLOCKED.
  * The test command is fixed (`python -m pytest ...`); no user-supplied commands
    are ever executed.

Local harness restrictions: a disposable copy of the project in a temp folder,
a scrubbed environment (no API keys or server secrets), outbound network blocked
by the fault plugin, a hard timeout, output size caps and full cleanup.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from app.analyzer.redact import redact
from app.config import settings

HARNESS_DIR = Path(__file__).resolve().parent / "harness"
SANDBOX_IMAGE = "devdna-sandbox:latest"
MAX_OUTPUT_CHARS = 20000
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "*.db",
                                     "*.sqlite", "*.sqlite3", ".env", ".env.*")


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def choose_mode(trusted: bool) -> tuple[str, str | None]:
    if trusted:
        return "local_harness", None
    if docker_available():
        return "docker", None
    return "blocked", ("This project was uploaded or loaded from disk, so its code is untrusted. Untrusted code only runs "
                       f"inside the Docker sandbox image '{SANDBOX_IMAGE}', and Docker or that image is not available on this "
                       "machine. No code was executed.")


def fault_spec(impact: dict) -> dict:
    if impact.get("custom_fault"):
        return impact["custom_fault"]
    target = impact["target"]
    data = target.get("data", {})
    scenario = impact["scenario"]
    if scenario == "db_unavailable":
        return {"kind": scenario, "engine": data.get("engine")}
    if scenario == "external_api_unavailable":
        return {"kind": scenario, "hint": data.get("hint")}
    if scenario == "missing_env_var":
        return {"kind": scenario, "var": target["name"]}
    if scenario == "worker_unavailable":
        return {"kind": scenario, "module": data.get("module"), "cls": data.get("cls"),
                "launchers": data.get("launcher_methods", [])}
    return {
        "kind": "dynamic_fault",
        "scenario": scenario,
        "target_id": target.get("id"),
        "target_type": target.get("type"),
        "target_name": target.get("name"),
        "target_data": data,
        "exceptions": impact.get("relevant_exceptions", ["RuntimeError"]),
        "fault_description": impact.get("fault_description", ""),
    }


def _scrubbed_env(workdir: Path, project_dir: Path, fault: dict | None, report: Path) -> dict:
    env = {
        "PYTHONPATH": os.pathsep.join([str(HARNESS_DIR), str(project_dir)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "TMP": str(workdir), "TEMP": str(workdir), "TMPDIR": str(workdir),
        "HOME": str(workdir), "USERPROFILE": str(workdir),
        "DEVDNA_FAULT": json.dumps(fault) if fault else "",
        "DEVDNA_FAULT_REPORT": str(report),
        "NO_PROXY": "*",
    }
    for key in ("SYSTEMROOT", "PATH", "COMSPEC", "PATHEXT", "WINDIR"):  # required for Python to start on Windows
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _posix_limits():  # pragma: no cover - not available on Windows
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_CPU, (settings.experiment_timeout_s, settings.experiment_timeout_s))
    resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))


def _dependency_requirements(project_dir: Path) -> str:
    """Return installable dependencies from the project's most specific manifest."""
    project_dir = project_dir.resolve()
    requirements = project_dir / "requirements.txt"
    if requirements.is_file():
        visited: set[Path] = set()

        def read_requirements(path: Path) -> list[str]:
            path = path.resolve(strict=True)
            if project_dir not in path.parents and path != project_dir:
                raise ValueError(f"Dependency include escapes the project directory: {path.name}")
            if path in visited:
                raise ValueError(f"Circular dependency include in {path.name}")
            visited.add(path)
            lines = []
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^\s*(?:-r|--requirement(?:=|\s+))\s*(.+?)\s*$", line)
                if match:
                    include = match.group(1).strip().strip("\"'")
                    lines.extend(read_requirements(path.parent / include))
                elif line.strip() and not re.fullmatch(r"(?:-e|--editable(?:=|\s+))\s*\.\/?(?:\[[^\]]+\])?", line.strip()):
                    lines.append(line)
            visited.remove(path)
            return lines

        return "\n".join(read_requirements(requirements))

    uv_lock = project_dir / "uv.lock"
    poetry_lock = project_dir / "poetry.lock"
    pipfile_lock = project_dir / "Pipfile.lock"
    pyproject = project_dir / "pyproject.toml"
    if uv_lock.is_file():
        lock = tomllib.loads(uv_lock.read_text(encoding="utf-8"))
        entries = []
        for package in lock.get("package", []):
            source = package.get("source", {})
            if source and source.get("registry") not in (None, "https://pypi.org/simple"):
                continue
            name, version = package.get("name"), package.get("version")
            if name and version:
                markers = package.get("resolution-markers", [])
                marker = f"; {markers[0]}" if len(markers) == 1 else ""
                entries.append(f"{name}=={version}{marker}")
        return "\n".join(entries)
    if poetry_lock.is_file():
        lock = tomllib.loads(poetry_lock.read_text(encoding="utf-8"))
        entries = []
        for package in lock.get("package", []):
            source = package.get("source", {})
            if source and source.get("type") not in ("legacy", "file"):
                continue
            name, version = package.get("name"), package.get("version")
            if name and version:
                marker = f"; {package['marker']}" if package.get("marker") else ""
                entries.append(f"{name}=={version}{marker}")
        return "\n".join(entries)
    if pipfile_lock.is_file():
        lock = json.loads(pipfile_lock.read_text(encoding="utf-8"))
        entries = []
        for section in ("default", "develop"):
            for name, package in lock.get(section, {}).items():
                version = package.get("version")
                if version:
                    marker = f"; {package['markers']}" if package.get("markers") else ""
                    entries.append(f"{name}{version}{marker}")
        return "\n".join(entries)
    if pyproject.is_file():
        config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = config.get("project", {})
        dependencies = list(project.get("dependencies", []))
        poetry = config.get("tool", {}).get("poetry", {}).get("dependencies", {})
        for name, value in poetry.items():
            if name.lower() == "python":
                continue
            if isinstance(value, str):
                spec = value
            elif isinstance(value, dict):
                version = value.get("version", "")
                spec = version
                extras = value.get("extras", [])
                if extras:
                    name += f"[{','.join(extras)}]"
                marker = value.get("markers")
                if marker:
                    spec += f"; {marker}"
                if value.get("optional") and not extras:
                    continue
            else:
                continue
            spec = spec.replace("^", ">=").replace("~", ">=")
            dependencies.append(f"{name}{spec}" if spec.startswith(("<", ">", "=", "!", "~")) else f"{name}=={spec}" if spec else name)
        return "\n".join(dependencies)
    return ""


def _build_sandbox_image(project_dir: Path, workdir: Path) -> str:
    """Build a throwaway sandbox image with the repository's declared dependencies."""
    context = workdir / "image-context"
    context.mkdir()
    (context / "requirements.txt").write_text(_dependency_requirements(project_dir), encoding="utf-8")
    image = f"devdna-sandbox:{uuid.uuid4().hex[:12]}"
    dockerfile = Path(__file__).resolve().parents[2] / "sandbox" / "Dockerfile"
    proc = subprocess.run(
        ["docker", "build", "--tag", image, "--file", str(dockerfile), str(context)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=settings.experiment_timeout_s,
    )
    if proc.returncode:
        raise RuntimeError(f"Could not build experiment sandbox image: {(proc.stderr or proc.stdout)[-2000:]}")
    return image


def parse_junit(path: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    if not path.exists():
        return results
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return results
    for case in root.iter("testcase"):
        name = case.get("name", "").split("[")[0]
        key = f"{case.get('classname', '')}::{name}"
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = redact((el.get("message") or "") + "\n" + (el.text or ""))[0][-1500:]
                break
        prev = results.get(key)
        if prev and prev["outcome"] in ("failed", "error"):
            continue  # parametrised: any failure wins
        results[key] = {"outcome": outcome, "message": message, "time": float(case.get("time") or 0)}
    return results


def _run_pytest(project_dir: Path, workdir: Path, fault: dict | None, label: str, mode: str,
                sandbox_image: str | None = None) -> dict:
    xml = workdir / f"{label}.xml"
    report = workdir / f"{label}-fault.json"
    args = ["-m", "pytest", "-q", "-p", "devdna_faults", "-p", "no:cacheprovider", f"--junitxml={xml}",
            "-o", "junit_family=xunit2", "--tb=short", "-rfE", "--color=no"]
    env = _scrubbed_env(workdir, project_dir, fault, report)
    started = time.monotonic()
    container_name = f"devdna-exp-{uuid.uuid4().hex[:12]}"
    if mode == "docker":  # pragma: no cover - requires Docker
        image = sandbox_image or SANDBOX_IMAGE
        workdir.chmod(0o777)
        cmd = ["docker", "run", "--rm", "--name", container_name, "--network", "none", "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
               "--read-only", "--tmpfs", "/tmp:rw,size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "-v", f"{project_dir}:/src:ro", "-v", f"{HARNESS_DIR}:/harness:ro", "-v", f"{workdir}:/out:rw",
               "-e", "PYTHONPATH=/harness:/src", "-e", f"DEVDNA_FAULT={env['DEVDNA_FAULT']}",
               "-e", f"DEVDNA_FAULT_REPORT=/out/{report.name}", "-w", "/src", image,
               "python", *[a.replace(str(xml), f"/out/{xml.name}") for a in args]]
        kwargs = {}
    else:
        cmd = [sys.executable, *args]
        kwargs = {"env": env, "cwd": str(project_dir)}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:  # pragma: no cover
            kwargs["preexec_fn"] = _posix_limits
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=settings.experiment_timeout_s, **kwargs)
        timed_out, code, out = False, proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        timed_out, code = True, None
        out = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
        if mode == "docker":
            cleanup = subprocess.run(
                ["docker", "rm", "--force", container_name],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if cleanup.returncode and "no such container" not in (cleanup.stderr or "").lower():
                out += f"\nDocker container cleanup failed: {(cleanup.stderr or cleanup.stdout)[-1000:]}"
    duration = round(time.monotonic() - started, 2)
    out = redact(out.replace(str(workdir), "<sandbox>"))[0]
    fault_state = {}
    if report.exists():
        try:
            fault_state = json.loads(report.read_text(encoding="utf-8"))
        except ValueError:
            fault_state = {"errors": ["fault report unreadable"]}
    results = parse_junit(xml)
    counts = {k: sum(1 for r in results.values() if r["outcome"] == k) for k in ("passed", "failed", "error", "skipped")}
    return {
        "label": label, "exit_code": code, "timed_out": timed_out, "duration_s": duration,
        "output": out[-MAX_OUTPUT_CHARS:], "output_truncated": len(out) > MAX_OUTPUT_CHARS,
        "results": results, "counts": counts, "fault_state": fault_state,
    }


def execute(project_dir: Path, fault: dict, mode: str) -> dict:
    """Run baseline and faulted tests in separate disposable project copies."""
    workdir = Path(tempfile.mkdtemp(prefix="devdna-exp-"))
    image = None
    try:
        baseline_project = workdir / "baseline-project"
        faulted_project = workdir / "faulted-project"
        shutil.copytree(project_dir, baseline_project, ignore=COPY_IGNORE)
        shutil.copytree(project_dir, faulted_project, ignore=COPY_IGNORE)
        if mode == "docker":
            image = _build_sandbox_image(project_dir, workdir)
            _run_pytest.sandbox_image = image
        baseline = _run_pytest(baseline_project, workdir, None, "baseline", mode, image)
        faulted = _run_pytest(faulted_project, workdir, fault, "faulted", mode, image)
        return {"mode": mode, "command": "python -m pytest -q (fixed command; plugin devdna_faults)",
                "baseline": baseline, "faulted": faulted, "fault": fault}
    finally:
        if image:
            subprocess.run(
                ["docker", "image", "rm", "--force", image],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
        shutil.rmtree(workdir, ignore_errors=True)


def compare(impact: dict, observation: dict) -> dict:
    """Compare per-test predictions with observed outcomes. Pure function (unit tested)."""
    base = observation["baseline"]
    fault = observation["faulted"]
    notes: list[str] = []
    if base["timed_out"] or fault["timed_out"]:
        return {"status": "inconclusive", "reason": "A test run hit the time limit.", "rows": [], "metrics": {}, "notes": notes}
    if not base["results"]:
        return {"status": "inconclusive", "reason": "The baseline run produced no test results (tests could not be collected).",
                "rows": [], "metrics": {}, "notes": notes}
    if fault["fault_state"].get("errors"):
        return {"status": "inconclusive", "reason": "Fault injection reported errors: " + "; ".join(fault["fault_state"]["errors"]),
                "rows": [], "metrics": {}, "notes": notes}

    rows = []
    predicted_keys = set()
    for t in impact["tests"]:
        key = t.get("junit_key")
        predicted_keys.add(key)
        b = base["results"].get(key)
        f = fault["results"].get(key)
        row = {"test": t["name"], "nodeid": t.get("nodeid"), "prediction": t["prediction"], "path": t.get("path", []),
               "handled_at": t.get("handled_at"),
               "baseline": b["outcome"] if b else "not_run", "observed": f["outcome"] if f else "not_run",
               "message": (f or {}).get("message", "")[-600:], "fault_marker": "DevDNA injected fault" in (f or {}).get("message", "")}
        observed_failed = row["observed"] in ("failed", "error")
        if row["baseline"] != "passed":
            row["verdict"] = "excluded"
            row["why"] = "Test did not pass in the baseline run, so it cannot show the effect of the fault."
        elif row["observed"] == "not_run":
            row["verdict"] = "excluded"
            row["why"] = "Test did not run in the faulted run."
        elif t["prediction"] == "fail":
            row["verdict"] = "match" if observed_failed else "mismatch"
        elif t["prediction"] == "pass":
            row["verdict"] = "mismatch" if observed_failed else "match"
        else:
            row["verdict"] = "uncertain_failed" if observed_failed else "uncertain_passed"
        rows.append(row)
    unmapped = [k for k in fault["results"] if k not in predicted_keys]
    if unmapped:
        notes.append(f"{len(unmapped)} executed test(s) were not mapped to the graph: {', '.join(unmapped[:5])}")

    tp = sum(1 for r in rows if r["prediction"] == "fail" and r["verdict"] == "match")
    fp = sum(1 for r in rows if r["prediction"] == "fail" and r["verdict"] == "mismatch")
    tn = sum(1 for r in rows if r["prediction"] == "pass" and r["verdict"] == "match")
    fn = sum(1 for r in rows if r["prediction"] == "pass" and r["verdict"] == "mismatch")
    unc_f = sum(1 for r in rows if r["verdict"] == "uncertain_failed")
    unc_p = sum(1 for r in rows if r["verdict"] == "uncertain_passed")
    definite = tp + fp + tn + fn
    metrics = {
        "true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn,
        "uncertain_failed": unc_f, "uncertain_passed": unc_p,
        "excluded": sum(1 for r in rows if r["verdict"] == "excluded"),
        "accuracy": round((tp + tn) / definite, 3) if definite else None,
        "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
        "recall": round(tp / (tp + fn), 3) if (tp + fn) else None,
        "fault_triggers": fault["fault_state"].get("triggers", 0),
    }
    triggers = metrics["fault_triggers"]
    all_covering_mocked = (
        impact.get("prediction") == "uncertain — covering tests mock the dependency"
        or impact.get("summary", {}).get("prediction") == "uncertain — covering tests mock the dependency"
        or (
            any(t.get("mocked") for t in impact.get("tests", []))
            and all(t.get("mocked") for t in impact.get("tests", []) if t.get("path") and t.get("junit_key") in fault["results"])
        )
    )
    if all_covering_mocked:
        status, reason = "uncertain — covering tests mock the dependency", "All covering tests mock the dependency, so the prediction is uncertain."
    elif triggers == 0:
        status, reason = "inconclusive", "The injected fault was never triggered, so the run says nothing about this failure."
    elif definite == 0:
        status, reason = "inconclusive", "No test had a definite prediction that could be checked."
    elif fp == 0 and fn == 0:
        status, reason = "prediction_confirmed", f"All {definite} definite test predictions matched the observed outcomes."
    elif tp == 0 and tn == 0:
        status, reason = "prediction_contradicted", "None of the definite test predictions matched the observed outcomes."
    else:
        status, reason = "partially_confirmed", f"{tp + tn} of {definite} definite test predictions matched; {fp + fn} did not."
    if unc_f or unc_p:
        notes.append(f"Uncertain (error handled on path) tests: {unc_f} failed, {unc_p} passed.")
    notes.append("This result covers this test suite under this injected fault only. It does not guarantee production behaviour.")

    ai_reconciliation = {
        "verdict_summary": f"DevDNA Empirical Verification: {definite} definite test prediction(s) reconciled against live pytest sandbox fault run.",
        "confusion_matrix": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "accuracy": round((tp + tn) / definite, 3) if definite else None,
        },
        "discrepancy_attribution": [
            f"Test '{r['test']}': predicted '{r['prediction']}' but observed '{r['observed']}' (message: {r.get('message', '')[:120]})."
            for r in rows if r["verdict"] == "mismatch"
        ][:5],
        "deterministic_grade": "A+" if (fp == 0 and fn == 0 and definite > 0) else ("B" if (tp + tn) > (fp + fn) else "C"),
    }

    return {"status": status, "reason": reason, "rows": rows, "metrics": metrics, "notes": notes, "ai_reconciliation": ai_reconciliation}
