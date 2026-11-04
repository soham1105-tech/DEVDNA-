"""DevDNA MCP server: a read-only, static-analysis stdio JSON-RPC server.

Run with::

    python -m app.mcp_server

The server speaks MCP's newline-delimited JSON transport on stdin/stdout and
exposes two tools. It uses only the Python standard library and the existing
DevDNA engine (``analyse_repository``, ``build_graph``, ``compute_impact``).
It never executes repository code, runs shell commands, or uses the network,
and tool outputs contain graph metadata only - never source text.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, TextIO

from app.analyzer.dispatch import analyse_repository
from app.graph.engine import build_graph, compute_impact

SERVER_NAME = "devdna-mcp"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
PREDICTION_LABEL = "PREDICTED"
STATIC_ONLY = "static analysis only; no repository code was executed"

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SEPARATOR_RE = re.compile(r"[\\/]+")

_LIMITATIONS = [
    "Impact is estimated by treating each analyzed component in the target file as a possible failure target.",
    "A route is considered covered only when a statically mapped test is predicted to fail.",
    "Test-to-route mapping depends on statically recognized test-client paths and may miss dynamic coverage.",
    "This is a PREDICTED result from static analysis: no tests were run and nothing was verified.",
]


class ToolError(ValueError):
    """A safe, user-facing validation error raised by an MCP tool."""


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- path safety -----------------------------------------------------------


def _path_parts(raw: str) -> list[str]:
    return [part for part in _SEPARATOR_RE.split(raw) if part]


def _reject_traversal(raw: str, label: str) -> None:
    if any(part == ".." for part in _path_parts(raw)):
        raise ToolError(f"{label} contains '..' traversal, which is not allowed")


def _resolve_repository(value: Any) -> Path:
    """Resolve a local directory path, refusing traversal, URLs and UNC paths."""
    if not isinstance(value, str) or not value.strip():
        raise ToolError("repo_path must be a non-empty local directory path")
    raw = value.strip().lstrip("\ufeff")
    if "\x00" in raw:
        raise ToolError("repo_path contains a null byte")
    if _SCHEME_RE.match(raw):
        raise ToolError("repo_path must be a local directory path, not a URL")
    if raw.startswith(("\\\\", "//")):
        raise ToolError("repo_path must be a local directory path, not a UNC/network path")
    _reject_traversal(raw, "repo_path")
    if _DRIVE_RE.match(raw) and not Path(raw).is_absolute():
        raise ToolError("repo_path must not be a drive-relative path")
    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ToolError(f"repo_path does not resolve to an accessible local directory: {exc}") from exc
    if not resolved.is_dir():
        raise ToolError("repo_path must point to a directory")
    if str(resolved.drive).startswith("\\\\"):
        raise ToolError("repo_path must be a local directory, not a network share")
    return resolved


def _relative_target(value: Any) -> str:
    """Validate a repository-relative target file path (no traversal/absolute paths)."""
    if not isinstance(value, str) or not value.strip():
        raise ToolError("target_file must be a non-empty repository-relative path")
    raw = value.strip().lstrip("\ufeff").replace("\\", "/")
    if "\x00" in raw:
        raise ToolError("target_file contains a null byte")
    _reject_traversal(raw, "target_file")
    if _DRIVE_RE.match(raw):
        raise ToolError("target_file must be repository-relative, not drive-qualified")
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise ToolError("target_file must be repository-relative, not an absolute path")
    if not path.parts or path.as_posix() == ".":
        raise ToolError("target_file must name a file inside the repository")
    return path.as_posix()


def _check_target_inside(repo: Path, relative: str) -> None:
    try:
        candidate = (repo / relative).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ToolError(f"target_file could not be resolved: {exc}") from exc
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise ToolError("target_file escapes the repository directory") from exc
    if not candidate.is_file():
        raise ToolError("target_file must be a file inside the repository")


# --- analysis helpers ------------------------------------------------------


def _route_tests(analysis: dict) -> dict[str, set[str]]:
    """Map each route id to the statically mapped test ids that exercise it."""
    components = {component["id"]: component for component in analysis["components"]}
    covered: dict[str, set[str]] = {}
    for edge in analysis["edges"]:
        source = components.get(edge["source"], {})
        target = components.get(edge["target"], {})
        if edge["type"] == "exercises" and source.get("type") == "test" and target.get("type") == "route":
            covered.setdefault(target["id"], set()).add(source["id"])
    return covered


def _matching_components(analysis: dict, relative: str, component_name: Any) -> list[dict]:
    matching = [
        component for component in analysis["components"]
        if component.get("file")
        and str(component["file"]).replace("\\", "/") == relative
        and component["type"] not in {"module", "test"}
    ]
    if component_name is not None:
        if not isinstance(component_name, str) or not component_name.strip():
            raise ToolError("component_name must be a non-empty string when provided")
        wanted = component_name.strip()
        if "\x00" in wanted:
            raise ToolError("component_name contains a null byte")
        matching = [
            component for component in matching
            if component.get("name") == wanted or component.get("id") == wanted
        ]
        if not matching:
            raise ToolError(f"No component named '{wanted}' was found in target_file '{relative}'")
    if not matching:
        raise ToolError(f"No analyzable components were found for target_file '{relative}'")
    return matching


# --- tools -----------------------------------------------------------------


def repo_summary(repo_path: Any) -> dict:
    """Return static component/edge/language counts for a local repository."""
    repo = _resolve_repository(repo_path)
    analysis = analyse_repository(repo)
    counts = Counter(component["type"] for component in analysis["components"])
    languages = dict(analysis.get("scan", {}).get("languages", {}))
    return {
        "analysis_mode": STATIC_ONLY,
        "repository": str(repo),
        "component_total": sum(counts.values()),
        "component_counts": dict(sorted(counts.items())),
        "routes": counts.get("route", 0),
        "edge_count": len(analysis["edges"]),
        "languages": dict(sorted(languages.items())),
        "frameworks": list(analysis.get("frameworks", [])),
    }


def blast_radius(repo_path: Any, target_file: Any, component_name: Any = None) -> dict:
    """Predict impacted routes/tests for one file, using only static analysis."""
    repo = _resolve_repository(repo_path)
    relative = _relative_target(target_file)
    _check_target_inside(repo, relative)

    analysis = analyse_repository(repo)
    graph = build_graph(analysis["components"], analysis["edges"])
    components = {component["id"]: component for component in analysis["components"]}
    route_tests = _route_tests(analysis)
    matching = _matching_components(analysis, relative, component_name)

    route_results: dict[str, dict] = {}
    failed_tests: set[str] = set()
    uncertain_tests: set[str] = set()
    affected_components: dict[str, str] = {}

    for component in matching:
        impact = compute_impact(
            graph,
            "changed_component",
            component["id"],
            custom_spec={
                "label": f"Potential failure in {relative}",
                "fault": "A failure introduced in the changed component.",
                "exceptions": ["Exception", "RuntimeError", "Error", "OSError", "ConnectionError"],
            },
        )
        for affected in impact["affected"]:
            affected_components[affected["id"]] = affected["type"]

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
            affected_components.setdefault(route_id, "route")
            item = route_results.setdefault(route_id, {
                "id": route_id,
                "name": components.get(route_id, {}).get("name", route.get("name", route_id)),
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
        route["prediction"] = PREDICTION_LABEL
        routes.append(route)

    affected_counts = Counter(affected_components.values())
    return {
        "prediction": PREDICTION_LABEL,
        "verified": False,
        "disclaimer": (
            "PREDICTED from static analysis only. No tests or repository code were run, "
            "and this result is never verified."
        ),
        "analysis_mode": STATIC_ONLY,
        "repository": str(repo),
        "target_file": relative,
        "component_name": component_name.strip() if isinstance(component_name, str) else None,
        "components_analyzed": len(matching),
        "affected_component_count": len(affected_components),
        "affected_components_by_type": dict(sorted(affected_counts.items())),
        "impacted_routes": routes,
        "tests_predicted_to_fail": sorted(failed_tests),
        "tests_predicted_may_fail": sorted(uncertain_tests),
        "uncovered_routes": [route["id"] for route in routes if route["uncovered"]],
        "limitations": _LIMITATIONS,
    }


# --- MCP protocol ----------------------------------------------------------

TOOLS = [
    {
        "name": "devdna_blast_radius",
        "description": (
            "PREDICT the routes and tests impacted by a file in a local repository. "
            "Static graph analysis only: results are PREDICTED and never verified. "
            "No code is executed, no shell or network access is used, and no source "
            "text is returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Local repository directory path (traversal is rejected).",
                },
                "target_file": {
                    "type": "string",
                    "description": "Repository-relative file path, for example shoplite/services/pricing.py.",
                },
                "component_name": {
                    "type": "string",
                    "description": "Optional component name or id to narrow the analysis within the file.",
                },
            },
            "required": ["repo_path", "target_file"],
            "additionalProperties": False,
        },
    },
    {
        "name": "devdna_repo_summary",
        "description": (
            "Summarise a local repository's static graph: component counts by type, "
            "edge count, and language counts. Metadata only; no source text is returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Local repository directory path (traversal is rejected).",
                },
            },
            "required": ["repo_path"],
            "additionalProperties": False,
        },
    },
]


def _tool_result(data: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, sort_keys=True)}],
        "isError": False,
    }


def _tool_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "isError": True,
    }


def _call_tool(name: str, arguments: Any) -> dict:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _tool_error("arguments must be a JSON object")
    try:
        if name == "devdna_blast_radius":
            data = blast_radius(
                arguments.get("repo_path"),
                arguments.get("target_file"),
                arguments.get("component_name"),
            )
        elif name == "devdna_repo_summary":
            data = repo_summary(arguments.get("repo_path"))
        else:  # guarded by _dispatch, kept for direct callers
            return _tool_error(f"unknown tool: {name}")
    except ToolError as exc:
        return _tool_error(str(exc))
    except Exception as exc:  # never expose a traceback or source text
        return _tool_error(f"analysis failed ({type(exc).__name__}): {exc}")
    return _tool_result(data)


def _dispatch(method: str, params: Any) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise _RpcError(-32602, "Invalid params: expected an object")

    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if isinstance(requested, str) and requested else DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "DevDNA exposes static, prediction-only repository analysis. "
                "Results are PREDICTED, never verified."
            ),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _RpcError(-32602, "Invalid params: tool name is required")
        if name not in {tool["name"] for tool in TOOLS}:
            raise _RpcError(-32602, f"Unknown tool: {name}")
        return _call_tool(name, params.get("arguments"))
    if method in {"notifications/initialized", "initialized", "shutdown"}:
        return {}
    raise _RpcError(-32601, f"Method not found: {method}")


def _rpc_error(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle_message(message: Any) -> dict | None:
    """Handle one decoded JSON-RPC message. Returns None for notifications."""
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid Request: expected a JSON object")
    if message.get("jsonrpc") != "2.0":
        return _rpc_error(message.get("id"), -32600, "Invalid Request: jsonrpc must be '2.0'")

    request_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")
    if not isinstance(method, str) or not method:
        return None if is_notification else _rpc_error(request_id, -32600, "Invalid Request: method is required")

    try:
        result = _dispatch(method, message.get("params"))
    except _RpcError as exc:
        return None if is_notification else _rpc_error(request_id, exc.code, exc.message)
    except Exception:
        return None if is_notification else _rpc_error(request_id, -32603, "Internal error")

    return None if is_notification else {"jsonrpc": "2.0", "id": request_id, "result": result}


def _write_message(stream: TextIO, message: dict) -> None:
    stream.write(json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n")
    stream.flush()


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Serve newline-delimited JSON-RPC until EOF or the MCP ``exit`` notification."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        raw = line.strip().lstrip("\ufeff")
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            _write_message(stdout, _rpc_error(None, -32700, "Parse error: invalid JSON"))
            continue

        response = handle_message(message)
        if response is not None:
            _write_message(stdout, response)
        if isinstance(message, dict) and message.get("method") == "exit":
            break
    return 0


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def main() -> int:
    _configure_stdio()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
