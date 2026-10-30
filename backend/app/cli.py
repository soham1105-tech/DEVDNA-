"""Generate prediction-only Markdown and JSON impact reports for changed files."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

from app.analyzer.dispatch import analyse_repository
from app.graph.engine import build_graph, compute_impact


def _relative_file(value: str) -> str:
    normalized = value.replace("\\", "/").strip().lstrip("\ufeff")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Changed file must be a repository-relative path: {value}")
    return str(path)


def _route_tests(analysis: dict) -> dict[str, set[str]]:
    components = {component["id"]: component for component in analysis["components"]}
    covered: dict[str, set[str]] = {}
    for edge in analysis["edges"]:
        source = components.get(edge["source"], {})
        target = components.get(edge["target"], {})
        if edge["type"] == "exercises" and source.get("type") == "test" and target.get("type") == "route":
            covered.setdefault(target["id"], set()).add(source["id"])
    return covered


def _inline_code(value: str) -> str:
    text = " ".join(str(value).splitlines())
    longest = max((len(ticks) for ticks in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    padding = " " if text.startswith(("`", " ")) or text.endswith(("`", " ")) else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def build_report(repo: Path, changed_files: list[str]) -> dict:
    repo = repo.expanduser().resolve(strict=True)
    if not repo.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo}")

    analysis = analyse_repository(repo)
    graph = build_graph(analysis["components"], analysis["edges"])
    components = {component["id"]: component for component in analysis["components"]}
    route_tests = _route_tests(analysis)
    files = []

    for changed_file in dict.fromkeys(_relative_file(value) for value in changed_files):
        matching = [
            component for component in analysis["components"]
            if component.get("file") and component["file"].replace("\\", "/") == changed_file
            and component["type"] not in {"module", "test"}
        ]
        route_results: dict[str, dict] = {}
        failed_tests: set[str] = set()
        uncertain_tests: set[str] = set()

        for component in matching:
            impact = compute_impact(
                graph,
                "changed_component",
                component["id"],
                custom_spec={
                    "label": f"Potential failure in {changed_file}",
                    "fault": "A failure introduced in the changed component.",
                    "exceptions": ["Exception", "RuntimeError", "Error", "OSError", "ConnectionError"],
                },
            )
            target_routes = {
                route["id"]: route
                for route in impact["affected"]
                if route["type"] == "route"
            }
            if component["type"] == "route":
                target_routes[component["id"]] = {
                    "id": component["id"], "name": component["name"], "certainty": "definite",
                }

            target_failed = {test["id"] for test in impact["tests"] if test["prediction"] == "fail"}
            target_uncertain = {test["id"] for test in impact["tests"] if test["prediction"] == "may_fail"}
            failed_tests.update(target_failed)
            uncertain_tests.update(target_uncertain)

            for route_id, route in target_routes.items():
                item = route_results.setdefault(route_id, {
                    "id": route_id,
                    "name": components[route_id]["name"],
                    "certainty": route.get("certainty", "definite"),
                    "tests_that_would_fail": set(),
                    "tests_that_may_fail": set(),
                })
                if route.get("certainty") == "definite":
                    item["certainty"] = "definite"
                item["tests_that_would_fail"].update(target_failed & route_tests.get(route_id, set()))
                item["tests_that_may_fail"].update(target_uncertain & route_tests.get(route_id, set()))

        uncertain_tests.difference_update(failed_tests)
        routes = []
        for route in sorted(route_results.values(), key=lambda value: value["name"]):
            route["tests_that_may_fail"].difference_update(route["tests_that_would_fail"])
            route["tests_that_would_fail"] = sorted(route["tests_that_would_fail"])
            route["tests_that_may_fail"] = sorted(route["tests_that_may_fail"])
            route["uncovered"] = not route["tests_that_would_fail"]
            routes.append(route)

        files.append({
            "file": changed_file,
            "components_analyzed": len(matching),
            "impacted_routes": routes,
            "tests_predicted_to_fail": sorted(failed_tests),
            "tests_predicted_may_fail": sorted(uncertain_tests),
            "uncovered_routes": [route["id"] for route in routes if route["uncovered"]],
        })

    return {
        "repository": str(repo),
        "analysis_mode": "static prediction only; no experiments were run",
        "files": files,
        "uncovered_routes": [
            {"file": file["file"], "route": route_id}
            for file in files for route_id in file["uncovered_routes"]
        ],
        "summary": {
            "changed_files": len(files),
            "impacted_routes": sum(len(file["impacted_routes"]) for file in files),
            "uncovered_routes": sum(len(file["uncovered_routes"]) for file in files),
        },
        "limitations": [
            "Impact is estimated by treating each analyzed component in a changed file as a possible failure target.",
            "A route is considered covered only when a statically mapped test is predicted to fail; possible failures do not count as confirmed coverage.",
            "Test-to-route mapping depends on statically recognized test-client paths and may miss dynamic or indirect test coverage.",
            "No tests or repository code are executed.",
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        "## DevDNA PR impact report",
        "",
        "_Static prediction only; no experiments were run._",
        "",
    ]
    if not report["files"]:
        lines.extend(["No changed files were provided.", ""])

    for file_report in report["files"]:
        lines.extend([
            f"### {_inline_code(file_report['file'])}",
            "",
        ])
        if not file_report["components_analyzed"]:
            lines.extend(["No analyzable Python components were found for this file.", ""])
            continue
        if not file_report["impacted_routes"]:
            lines.extend(["**Impacted routes:** None identified.", ""])
        else:
            lines.append("**Impacted routes:**")
            for route in file_report["impacted_routes"]:
                certainty = f" ({route['certainty']})"
                coverage = "UNCOVERED" if route["uncovered"] else "covered by a predicted-failing test"
                lines.append(f"- {_inline_code(route['name'])}{certainty} — **{coverage}**")
            lines.append("")

        lines.append("**Tests predicted to fail:**")
        lines.extend(
            [f"- {_inline_code(test_id)}" for test_id in file_report["tests_predicted_to_fail"]]
            or ["- None identified."]
        )
        lines.append("")

        lines.append("**Tests that may fail (uncertain paths):**")
        lines.extend(
            [f"- {_inline_code(test_id)}" for test_id in file_report["tests_predicted_may_fail"]]
            or ["- None identified."]
        )
        lines.append("")

        lines.append("**Impacted routes with no statically mapped test predicted to catch the fault:**")
        lines.extend(
            [f"- {_inline_code(route_id)}" for route_id in file_report["uncovered_routes"]]
            or ["- None."]
        )
        lines.append("")

    lines.extend(["### Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    return "\n".join(lines) + "\n"


def _write_output(destination: str | None, content: str) -> None:
    if destination is None:
        print(content, end="")
        return
    path = Path(destination).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a static impact report for changed repository files.")
    parser.add_argument("repo", type=Path, help="Repository directory to analyze")
    parser.add_argument("--changed-files-file", type=Path, help="Text file containing one repository-relative path per line")
    parser.add_argument("--changed-file", action="append", default=[], help="Changed repository-relative file (repeatable)")
    parser.add_argument("--markdown-output", help="Write Markdown to this path (default: stdout)")
    parser.add_argument("--json-output", help="Write JSON to this path")
    parser.add_argument(
        "--fail-on-uncovered-route",
        action="store_true",
        help="Exit 1 when an impacted route has no statically mapped predicted-failing test",
    )
    args = parser.parse_args(argv)

    try:
        changed_files = (
            args.changed_files_file.read_text(encoding="utf-8").splitlines()
            if args.changed_files_file else []
        )
        changed_files.extend(args.changed_file)
        report = build_report(args.repo, changed_files)
    except (OSError, ValueError) as exc:
        print(f"devdna: {exc}", file=sys.stderr)
        return 2

    markdown = render_markdown(report)
    _write_output(args.markdown_output, markdown)
    if args.json_output:
        _write_output(args.json_output, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 1 if args.fail_on_uncovered_route and report["uncovered_routes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
