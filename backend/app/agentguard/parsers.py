"""AgentGuard: deterministic, evidence-backed security audit of AI-agent
configurations.

One parser per explicitly documented format. A file that looks agent-related
but does not match its format's documented shape is reported as
"unsupported" — never guessed at. Parsers extract structured records only;
raw secret values are never recorded (credential references store names,
values are redacted).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.analyzer.redact import redact

# ---------------------------------------------------------------------------
# Format routing (documented filename patterns; nothing else is parsed)
# ---------------------------------------------------------------------------

INSTRUCTION_NAMES = {"agents.md", "agent.md", "claude.md"}
ENV_PREFIX = ".env"


def classify(rel: str) -> str | None:
    """Return the AgentGuard format name for a repo-relative path, or None
    when the file is not an agent-config file this module documents."""
    name = rel.rsplit("/", 1)[-1].lower()
    posix = rel.lower()
    if name in (".mcp.json", "mcp.json"):
        return "mcp_server_config"
    if posix.startswith(".claude/") and name in ("settings.json", "settings.local.json"):
        return "tool_settings"
    if name in INSTRUCTION_NAMES or name.startswith("agents-"):
        return "agent_instructions"
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return "env_file"
    if posix.startswith(".github/workflows/") and (name.endswith(".yml") or name.endswith(".yaml")):
        return "ci_workflow"
    if name == "package.json":
        return "dependency_manifest"
    return None


@dataclass
class ParsedFile:
    format: str
    file: str
    records: list[dict] = field(default_factory=list)
    unsupported: bool = False
    reason: str | None = None


_SECRET_KEY_RE = re.compile(r"(password|passwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|credential)", re.I)
_BENIGN_VALUES = {"", "changeme", "placeholder", "your-key-here", "todo", "changeme123", "redacted", "example", "test", "none"}

# Instruction verbs that suggest an agent may pull remote content at runtime.
_FETCH_VERBS = re.compile(r"\b(fetch|load|retrieve|download|import|follow|apply|execute|run)\b", re.I)
_URL_RE = re.compile(r"https?://[^\s)>\"']+")
_TOOL_GRANT_RE = re.compile(r"^\s*[-*]\s*([A-Za-z_]\w*)\(([^)]*)\)\s*$")
_CURL_PIPE_RE = re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b")


def _unsupported(parsed_format: str, file: str, reason: str) -> ParsedFile:
    return ParsedFile(format=parsed_format, file=file, unsupported=True, reason=reason)


# ---------------------------------------------------------------------------
# Parsers — one per documented format
# ---------------------------------------------------------------------------

def parse_mcp_server_config(path: Path, rel: str) -> ParsedFile:
    """Documented format: JSON with a top-level `mcpServers` object; each
    entry is {command, args[], env{}} for stdio servers or {url, headers{}}
    for remote servers (MCP server configuration shape)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return _unsupported("mcp_server_config", rel, "not valid JSON")
    if not isinstance(data, dict) or "mcpServers" not in data:
        return _unsupported("mcp_server_config", rel, "no mcpServers object")
    servers = data["mcpServers"]
    if not isinstance(servers, dict):
        return _unsupported("mcp_server_config", rel, "mcpServers is not an object")
    records = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        url = cfg.get("url")
        command = cfg.get("command")
        args = [str(a) for a in cfg.get("args", [])] if isinstance(cfg.get("args"), list) else []
        env_keys = sorted(cfg["env"].keys()) if isinstance(cfg.get("env"), dict) else []
        record = {"kind": "mcp_server", "name": str(name), "line": None,
                  "transport": "http" if url else "stdio",
                  "command": str(command) if command else None,
                  "args": args, "url": url, "env_keys": env_keys}
        if command and not url:
            # npx/uvx/pipx/bunx runners: extract the package ref for pin checks.
            # The runner is the command itself; the package is the first
            # non-flag argument.
            if command in ("npx", "uvx", "pipx", "bunx"):
                record["runner"] = command
                pkg = next((a for a in args if not a.startswith("-")), None)
                if pkg:
                    record["package"] = pkg
                    record["package_pinned"] = "@" in pkg.split("/")[-1]
        records.append(record)
    return ParsedFile(format="mcp_server_config", file=rel, records=records)


def parse_tool_settings(path: Path, rel: str) -> ParsedFile:
    """Documented format: JSON settings with an optional `permissions` object
    holding `allow`[], `deny`[] (rules shaped `Tool(specifier)`) and
    `defaultMode`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return _unsupported("tool_settings", rel, "not valid JSON")
    if not isinstance(data, dict):
        return _unsupported("tool_settings", rel, "not an object")
    records = []
    perms = data.get("permissions")
    if isinstance(perms, dict):
        for mode in ("allow", "deny"):
            for rule in perms.get(mode, []) or []:
                if not isinstance(rule, str):
                    continue
                m = re.match(r"^([A-Za-z_]\w*)(?:\((.*)\))?$", rule.strip(), re.S)
                tool, spec = (m.group(1), m.group(2) or "") if m else (rule.strip(), "")
                records.append({"kind": "permission_rule", "tool": tool, "specifier": spec,
                                "mode": mode, "rule": rule, "line": None})
    if isinstance(data.get("permissions"), dict) and data["permissions"].get("defaultMode"):
        records.append({"kind": "default_mode", "mode": str(data["permissions"]["defaultMode"]), "line": None})
    if isinstance(data.get("hooks"), dict) and data["hooks"]:
        records.append({"kind": "hooks_present", "line": None})
    return ParsedFile(format="tool_settings", file=rel, records=records)


def parse_agent_instructions(path: Path, rel: str) -> ParsedFile:
    """Documented format: Markdown instruction files containing tool-permission
    bullets shaped `ToolName(specifier)` (agent CLI convention) and plain text.
    Only these two record shapes are extracted; prose is never interpreted as
    a permission."""
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m = _TOOL_GRANT_RE.match(line)
        if m:
            records.append({"kind": "tool_grant", "tool": m.group(1), "specifier": m.group(2).strip(),
                            "line": lineno, "snippet": redact(line.strip())[0][:200]})
            continue
        if _CURL_PIPE_RE.search(line):
            records.append({"kind": "curl_pipe_shell", "line": lineno,
                            "snippet": redact(line.strip())[0][:200]})
            continue
        url = _URL_RE.search(line)
        if url and _FETCH_VERBS.search(line):
            records.append({"kind": "remote_content_reference", "url": url.group(0),
                            "line": lineno, "snippet": redact(line.strip())[0][:200]})
    return ParsedFile(format="agent_instructions", file=rel, records=records)


def parse_env_file(path: Path, rel: str) -> ParsedFile:
    """Documented format: KEY=VALUE lines (# comments). Credential references
    record the KEY and whether the value looks secret-shaped; values are never
    stored."""
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        secret_shaped = bool(_SECRET_KEY_RE.search(key)) and value.lower() not in _BENIGN_VALUES and len(value) >= 8
        records.append({"kind": "env_var", "key": key, "secret_shaped": secret_shaped,
                        "line": lineno, "value": REDACTED_VALUE if secret_shaped else redact(value)[0][:40]})
    return ParsedFile(format="env_file", file=rel, records=records)


REDACTED_VALUE = "***"


def parse_ci_workflow(path: Path, rel: str) -> ParsedFile:
    """Documented format: GitHub Actions workflow YAML. Extracts third-party
    `uses:` refs (with pin status), `secrets.*` references, `run:` shell
    snippets (curl-pipe detection) and the workflow-level permissions block."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        return _unsupported("ci_workflow", rel, "pyyaml not installed")
    except ValueError:
        return _unsupported("ci_workflow", rel, "not valid YAML")
    if not isinstance(data, dict):
        return _unsupported("ci_workflow", rel, "not a mapping")
    records = []

    def walk(node, lineno):
        if isinstance(node, dict):
            uses = node.get("uses")
            if isinstance(uses, str):
                ref = uses.split("@", 1)
                owner = ref[0].split("/")[0] if len(ref) == 2 else ""
                sha_pinned = len(ref) == 2 and len(ref[1]) == 40 and all(c in "0123456789abcdef" for c in ref[1].lower())
                records.append({"kind": "action_ref", "action": ref[0], "ref": ref[1] if len(ref) == 2 else "",
                                "sha_pinned": sha_pinned, "first_party": owner in ("actions", "github", "docker"),
                                "line": lineno})
            for k, v in node.items():
                walk(v, getattr(node, "lineno", None) or lineno)
        elif isinstance(node, list):
            for item in node:
                walk(item, lineno)

    if isinstance(data.get("permissions"), dict):
        records.append({"kind": "workflow_permissions", "permissions": data["permissions"], "line": None})
    for job_name, job in (data.get("jobs") or {}).items():
        steps = job.get("steps", []) if isinstance(job, dict) else []
        for step in steps:
            if not isinstance(step, dict):
                continue
            lineno = step.get("__line__")
            if isinstance(step.get("env"), dict):
                for k in step["env"]:
                    if "SECRET" in str(k).upper() or "TOKEN" in str(k).upper() or "KEY" in str(k).upper():
                        records.append({"kind": "secret_ref", "name": str(k), "line": lineno})
            run = step.get("run")
            if isinstance(run, str):
                snippet = redact(run)[0][:200]
                records.append({"kind": "run_command", "snippet": snippet, "line": lineno,
                                "curl_pipe": bool(_CURL_PIPE_RE.search(run))})
                for sm in re.finditer(r"secrets\.([A-Z0-9_]+)", run):
                    records.append({"kind": "secret_ref", "name": sm.group(1), "line": lineno,
                                    "interpolated_into_run": True})
            walk(step, lineno)
    return ParsedFile(format="ci_workflow", file=rel, records=records)


def parse_dependency_manifest(path: Path, rel: str) -> ParsedFile:
    """Documented format: package.json with dependencies/devDependencies
    objects mapping package names to version ranges."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return _unsupported("dependency_manifest", rel, "not valid JSON")
    if not isinstance(data, dict):
        return _unsupported("dependency_manifest", rel, "not an object")
    records = []
    for section in ("dependencies", "devDependencies"):
        deps = data.get(section)
        if isinstance(deps, dict):
            for name, spec in deps.items():
                records.append({"kind": "dependency", "name": str(name), "spec": str(spec),
                                "section": section, "line": None})
    return ParsedFile(format="dependency_manifest", file=rel, records=records)


PARSERS = {
    "mcp_server_config": parse_mcp_server_config,
    "tool_settings": parse_tool_settings,
    "agent_instructions": parse_agent_instructions,
    "env_file": parse_env_file,
    "ci_workflow": parse_ci_workflow,
    "dependency_manifest": parse_dependency_manifest,
}
