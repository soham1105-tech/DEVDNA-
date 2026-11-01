"""AgentGuard audit service: parse agent configs, build the permission graph,
and derive evidence-backed findings.

Honesty rules encoded here:
- Findings are labelled "verified" only when the claim is read directly from
  an explicit configuration value; anything inferred across nodes or from
  prose is labelled "suspected".
- The data-exfiltration category reports a static *path* on the permission
  graph, the required permission, and the missing safeguard — never a claim
  that the path was exploited.
- Secret values never enter records, findings, or the graph (names and
  redacted snippets only).
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx

from app.agentguard.parsers import PARSERS, classify
from app.analyzer.redact import redact
from app.graph.engine import build_graph

PROPAGATING = {"grants", "executes", "connects", "references", "reads", "writes"}
MAX_EXFIL_PATHS = 8
MAX_PATH_LENGTH = 8

CATEGORIES = ("excessive_permissions", "dangerous_tool_execution", "credential_exposure",
              "mcp_supply_chain", "prompt_injection", "data_exfiltration")


def _finding(category: str, title: str, *, severity: str, status: str, confidence: str,
             evidence: list[dict], recommendation: str, required_permission: str | None = None,
             missing_safeguard: str | None = None) -> dict:
    return {
        "id": f"ag:{category}:{evidence[0]['file']}:{evidence[0].get('line') or 0}",
        "category": category,
        "title": title,
        "severity": severity,
        "status": status,          # "verified" = read directly from config; "suspected" = inferred risk
        "confidence": confidence,  # how strongly the config supports the claim
        "evidence": evidence,
        "provenance": "static configuration analysis (no code executed)",
        "required_permission": required_permission,
        "missing_safeguard": missing_safeguard,
        "recommendation": recommendation,
    }


def _collect_files(root: Path) -> tuple[list[tuple[Path, str, str]], list[dict]]:
    """Walk the tree; return (path, rel, format) for agent-config files and an
    unsupported report for agent-related files that do not match any format."""
    import os

    parsed: list[tuple[Path, str, str]] = []
    unsupported: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "node_modules", ".venv", "__pycache__"))
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            fmt = classify(rel)
            if fmt is None:
                continue
            if fmt == "mcp_server_config" or fmt == "tool_settings" or fmt == "dependency_manifest":
                # parse first: only report unsupported when the shape is wrong
                parser = PARSERS[fmt]
                result = parser(full, rel)
                if result.unsupported:
                    unsupported.append({"file": rel, "expected_format": fmt, "reason": result.reason})
                    continue
                parsed.append((full, rel, fmt))
            else:
                parsed.append((full, rel, fmt))
    return parsed, unsupported


def _parse_all(files: list[tuple[Path, str, str]]) -> tuple[list[dict], list[dict]]:
    parsed_files: list[dict] = []
    records: list[dict] = []
    for path, rel, fmt in files:
        result = PARSERS[fmt](path, rel)
        parsed_files.append({"format": result.format, "file": rel,
                             "record_count": len(result.records),
                             "unsupported": result.unsupported, "reason": result.reason})
        for record in result.records:
            records.append({**record, "file": rel, "_format": result.format})
    return parsed_files, records


def _records_by_kind(records: list[dict], kind: str) -> list[dict]:
    return [r for r in records if r.get("kind") == kind]


# ---------------------------------------------------------------------------
# Findings rules
# ---------------------------------------------------------------------------

_UNRESTRICTED_SPECIFIERS = {"*", ""}
_BROAD_WRITE_TOOLS = {"Write", "Edit", "fs_write", "filesystem_write"}
_SHELL_TOOLS = {"Bash", "Shell", "bash", "shell", "terminal"}
_NETWORK_TOOLS = {"WebFetch", "WebSearch", "web_fetch", "http", "fetch"}
_DANGEROUS_SPEC_PREFIXES = ("rm -rf", "sudo", "git push --force", "curl ", "wget ", "mkfs", "dd if=")
_DESTRUCTIVE_TOOLS = {"rm", "sudo", "dd", "mkfs"}


def _excessive_permissions(records: list[dict]) -> list[dict]:
    findings = []
    for r in _records_by_kind(records, "permission_rule") + _records_by_kind(records, "tool_grant"):
        tool, spec = r.get("tool", ""), r.get("specifier", "")
        mode = r.get("mode", "allow")
        if mode == "deny":
            continue
        ev = [{"file": r["file"], "line": r.get("line") or 0, "snippet": redact(r.get("rule") or f"{tool}({spec})")[0][:200],
               "detail": f"{mode} rule"}]
        if tool in _SHELL_TOOLS and spec in _UNRESTRICTED_SPECIFIERS:
            findings.append(_finding(
                "excessive_permissions", f"Unrestricted shell access granted to '{tool}'",
                severity="high", status="verified", confidence="high", evidence=ev,
                recommendation="Restrict the rule to specific command prefixes and require approval for unmatched commands."))
            continue
        if tool in _BROAD_WRITE_TOOLS and spec in _UNRESTRICTED_SPECIFIERS | {"/", "**"}:
            findings.append(_finding(
                "excessive_permissions", f"Unrestricted filesystem write granted to '{tool}'",
                severity="high", status="verified", confidence="high", evidence=ev,
                recommendation="Limit write rules to explicit project subdirectories."))
            continue
        if tool in _NETWORK_TOOLS and spec in _UNRESTRICTED_SPECIFIERS:
            findings.append(_finding(
                "excessive_permissions", f"Unrestricted network access granted to '{tool}'",
                severity="medium", status="verified", confidence="high", evidence=ev,
                recommendation="Restrict network tools to an allowlist of domains."))
            continue
        if any(spec.strip().lower().startswith(p) for p in _DANGEROUS_SPEC_PREFIXES) or tool.lower() in _DESTRUCTIVE_TOOLS:
            findings.append(_finding(
                "dangerous_tool_execution", f"Destructive command allowlisted: '{tool}({spec})'",
                severity="high", status="verified", confidence="high", evidence=ev,
                recommendation="Remove destructive prefixes from the allowlist; require per-call approval."))
    for r in _records_by_kind(records, "default_mode"):
        if r["mode"] in ("bypassPermissions", "bypassAll", "yolo"):
            findings.append(_finding(
                "excessive_permissions", f"Approval gate disabled: defaultMode '{r['mode']}'",
                severity="high", status="verified", confidence="high",
                evidence=[{"file": r["file"], "line": 0, "snippet": f"defaultMode: {r['mode']}",
                           "detail": "permission rules are bypassed by default"}],
                recommendation="Set defaultMode to 'ask' or 'acceptEdits' so dangerous tools require approval."))
    return findings


def _credential_exposure(records: list[dict]) -> list[dict]:
    findings = []
    for r in _records_by_kind(records, "env_var"):
        if not r.get("secret_shaped"):
            continue
        findings.append(_finding(
            "credential_exposure", f"Credential material committed in env file: {r['key']}",
            severity="high", status="verified", confidence="high",
            evidence=[{"file": r["file"], "line": r.get("line") or 0,
                       "snippet": f"{r['key']}={REDACTED_SNIPPET}", "detail": "value redacted"}],
            recommendation="Move the value out of the repository into a secret store; add the file to .gitignore."))
    for r in _records_by_kind(records, "secret_ref"):
        if r.get("interpolated_into_run"):
            findings.append(_finding(
                "credential_exposure", f"Secret '{r['name']}' interpolated into a shell command",
                severity="high", status="verified", confidence="high",
                evidence=[{"file": r["file"], "line": r.get("line") or 0,
                           "snippet": f"secrets.{r['name']} -> run step", "detail": "secret reaches a shell command"}],
                recommendation="Pass secrets via env vars or a masked input, never as inline shell arguments."))
        else:
            findings.append(_finding(
                "credential_exposure", f"Workflow references secret '{r['name']}'",
                severity="low", status="verified", confidence="high",
                evidence=[{"file": r["file"], "line": r.get("line") or 0,
                           "snippet": f"secrets.{r['name']}", "detail": "reference only"}],
                recommendation="Ensure the secret is scoped to the minimum jobs that need it."))
    return findings


def _mcp_supply_chain(records: list[dict]) -> list[dict]:
    findings = []
    for r in _records_by_kind(records, "mcp_server"):
        if r.get("transport") == "http":
            url = r.get("url") or ""
            cleartext = url.startswith("http://")
            findings.append(_finding(
                "mcp_supply_chain", f"MCP server '{r['name']}' uses a {'cleartext HTTP' if cleartext else 'remote'} endpoint",
                severity="high" if cleartext else "medium",
                status="verified" if cleartext else "suspected", confidence="high",
                evidence=[{"file": r["file"], "line": 0, "snippet": redact(url)[0][:200],
                           "detail": f"remote MCP server: {r['name']}"}],
                recommendation="Pin the server, verify its publisher, and prefer stdio servers from a reviewed source."))
            continue
        pkg = r.get("package")
        if pkg and not r.get("package_pinned"):
            findings.append(_finding(
                "mcp_supply_chain", f"MCP server '{r['name']}' runs an unpinned package ('{pkg}')",
                severity="medium", status="suspected", confidence="medium",
                evidence=[{"file": r["file"], "line": 0, "snippet": f"{r.get('runner')} {pkg}",
                           "detail": "package fetched from a registry at launch"}],
                recommendation="Pin the package to an exact version and vet the publisher."))
    for r in _records_by_kind(records, "action_ref"):
        if r.get("first_party") or r.get("sha_pinned"):
            continue
        findings.append(_finding(
            "mcp_supply_chain", f"Third-party GitHub Action '{r['action']}' is not SHA-pinned",
            severity="medium", status="verified", confidence="high",
            evidence=[{"file": r["file"], "line": r.get("line") or 0, "snippet": f"uses: {r['action']}@{r['ref']}",
                       "detail": "pinned to a mutable ref"}],
            recommendation="Pin third-party actions to a full commit SHA."))
    return findings


def _prompt_injection(records: list[dict]) -> list[dict]:
    findings = []
    for r in _records_by_kind(records, "remote_content_reference"):
        findings.append(_finding(
            "prompt_injection", "Instruction file directs the agent to load remote content",
            severity="medium", status="suspected", confidence="medium",
            evidence=[{"file": r["file"], "line": r.get("line") or 0,
                       "snippet": r.get("snippet") or "", "detail": f"url: {r.get('url')}"}],
            recommendation="Treat fetched content as untrusted: never let it grant permissions or trigger tools."))
    return findings


def _dangerous_tool_execution(records: list[dict]) -> list[dict]:
    findings = []
    for r in _records_by_kind(records, "curl_pipe_shell"):
        findings.append(_finding(
            "dangerous_tool_execution", "Instruction file contains a curl-pipe-to-shell pattern",
            severity="high", status="verified", confidence="high",
            evidence=[{"file": r["file"], "line": r.get("line") or 0, "snippet": r.get("snippet") or "",
                       "detail": "executes remotely fetched code"}],
            recommendation="Remove the pattern; vendor the script and review it."))
    return findings


def _data_exfiltration_paths(g: nx.MultiDiGraph, deny_tools: set[str]) -> list[dict]:
    """Static exfiltration paths: an agent that (a) consumes untrusted input,
    (b) holds a credential reference, and (c) can invoke an outbound channel.

    The finding reports the chain (untrusted source -> config -> agent ->
    outbound channel, with the credential held by the agent), the required
    permission (the grant edges), and the missing safeguard (no deny rule
    covers the channel). Always 'suspected': a static path is not an exploit,
    and this module executes nothing.
    """
    findings = []
    agent_ids = [n for n, d in g.nodes(data=True) if d.get("type") == "agent"]
    for agent in agent_ids:
        ancestors = nx.ancestors(g, agent)
        sources = [n for n in ancestors if g.nodes[n].get("data", {}).get("role") == "untrusted_source"]
        credentials = [n for n in ancestors if g.nodes[n].get("data", {}).get("role") == "sensitive"]
        channels = [n for n, d in g.nodes(data=True)
                    if d.get("data", {}).get("role") == "outbound_channel" and n in nx.descendants(g, agent)]
        if not sources or not credentials or not channels:
            continue
        source = sorted(sources)[0]
        credential = sorted(credentials)[0]
        for channel in sorted(channels)[:3]:
            outbound_tool = channel.split(":", 1)[-1]
            if outbound_tool in deny_tools:
                continue  # safeguard present: a deny rule covers this channel
            findings.append(_finding(
                "data_exfiltration",
                f"Untrusted input reaches '{outbound_tool}', which runs alongside credential '{credential.split(':', 1)[-1]}'",
                severity="high", status="suspected", confidence="medium",
                evidence=[
                    {"file": source.split(":", 1)[-1], "line": 0,
                     "snippet": "untrusted input -> agent", "detail": f"untrusted source: {source}"},
                    {"file": credential.split(":", 1)[-1], "line": 0,
                     "snippet": "credential -> agent", "detail": f"credential held by the agent: {credential}"},
                    {"file": agent.split(":", 1)[-1], "line": 0,
                     "snippet": f"agent -> {outbound_tool}", "detail": "outbound channel granted to the agent"},
                ],
                required_permission="grants/executes edges from the agent to the outbound channel",
                missing_safeguard=f"No deny rule or approval gate covers '{outbound_tool}'.",
                recommendation="Add a deny rule for the outbound tool, or require approval before it runs "
                               "alongside credential access. Treat untrusted input as data, never as instructions."))
    return findings


REDACTED_SNIPPET = "***"

def _build_permission_graph(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Build components/edges for the permission graph in the same shape the
    existing graph engine consumes.

    Structure: one agent node per audited repository; config-file nodes
    configure the agent; grants flow agent -> tool; credentials provide the
    agent's env; outbound channels (network/shell tools, remote MCP servers,
    curl-pipe commands) are what untrusted input could reach.
    """
    components: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    agent_id = "agent:repository"
    roles: dict[str, str] = {}

    def node(cid, ctype, name, **data):
        if cid not in components:
            components[cid] = {"id": cid, "type": ctype, "name": name, "file": data.pop("file", None),
                               "line": data.pop("line", None), "data": data}
        return cid

    def edge(src, dst, etype, classification, detail):
        eid = f"{etype}|{src}|{dst}"
        if eid not in edges:
            edges[eid] = {"id": eid, "source": src, "target": dst, "type": etype,
                          "classification": classification, "evidence": [{"detail": detail}],
                          "call_sites": []}

    node(agent_id, "agent", "AI agent (repository config)")

    def _config_node(rel: str) -> str:
        cid = f"config:{rel}"
        node(cid, "policy", rel, file=rel)
        edge(cid, agent_id, "configures", "confirmed", f"{rel} configures the agent")
        return cid

    for r in _records_by_kind(records, "tool_grant") + _records_by_kind(records, "permission_rule"):
        tool = r.get("tool", "")
        if not tool:
            continue
        tid = f"tool:{tool}"
        node(tid, "tool", tool)
        spec = r.get("specifier", "")
        if tool in _NETWORK_TOOLS:
            roles.setdefault(tid, "outbound_channel")
        if tool in _SHELL_TOOLS and spec in _UNRESTRICTED_SPECIFIERS:
            roles[tid] = "outbound_channel"
        if r.get("kind") == "permission_rule":
            node(f"policy:{r.get('rule', '')[:120]}", "policy", r.get("rule", ""), file=r.get("file"))
            if r.get("mode") == "allow":
                edge(agent_id, tid, "grants", "confirmed", f"allow rule {r.get('rule')}")
            else:
                edge(agent_id, tid, "denies", "confirmed", f"deny rule {r.get('rule')}")
        else:  # tool grant from an instruction file
            edge(agent_id, tid, "grants", "confirmed", f"tool grant {tool}({spec})")
        if spec.startswith("/"):
            fid = f"fs:{spec}"
            node(fid, "fs_resource", spec)
            edge(tid, fid, "writes", "confirmed", f"{tool}({spec})")
            roles[fid] = "sensitive" if spec in ("/", "**") else roles.get(fid)

    for r in _records_by_kind(records, "env_var"):
        cid = f"credential:{r['key']}"
        node(cid, "credential", r["key"], file=r["file"], line=r.get("line"),
             secret_shaped=bool(r.get("secret_shaped")))
        if r.get("secret_shaped"):
            roles[cid] = "sensitive"
        edge(cid, _config_node(r["file"]), "provides_env", "confirmed",
             "credential defined in the agent environment")

    for r in _records_by_kind(records, "secret_ref"):
        cid = f"credential:{r['name']}"
        node(cid, "credential", r["name"], file=r["file"], line=r.get("line"))
        roles[cid] = "sensitive"
        edge(cid, _config_node(r["file"]), "references", "confirmed", "workflow secret reference")

    for r in _records_by_kind(records, "mcp_server"):
        cid = f"mcp:{r['name']}"
        node(cid, "tool", f"MCP server: {r['name']}", file=r["file"], transport=r.get("transport"))
        edge(agent_id, cid, "executes", "confirmed", "MCP server configured for the agent")
        if r.get("transport") == "http":
            roles[cid] = "outbound_channel"
        url = r.get("url")
        if isinstance(url, str) and "://" in url:
            host = url.split("://", 1)[1].split("/", 1)[0]
            eid_node = f"ext:{host}"
            node(eid_node, "external_service", host)
            edge(cid, eid_node, "connects", "confirmed", "remote MCP endpoint")
        for key in r.get("env_keys", []):
            cid_key = f"credential:{key}"
            node(cid_key, "credential", key)
            roles.setdefault(cid_key, "sensitive")
            edge(cid_key, cid, "references", "confirmed", "env var passed to MCP server")
        edge(_config_node(r["file"]), cid, "configures", "confirmed", "MCP server definition")
        pkg = r.get("package")
        if pkg:
            host = r.get("runner", "registry")
            eid_node = f"ext:{host}"
            node(eid_node, "external_service", host)
            edge(eid_node, cid, "references", "confirmed", f"package {pkg} fetched at launch")

    for r in _records_by_kind(records, "remote_content_reference"):
        url = r.get("url") or ""
        host = url.split("://", 1)[-1].split("/", 1)[0]
        cid = f"ext:{host}" if host else f"ext:{r['file']}"
        node(cid, "external_service", host or r["file"], file=r["file"], line=r.get("line"))
        roles[cid] = "untrusted_source"
        edge(cid, _config_node(r["file"]), "feeds", "confirmed", "remote content loaded into instructions")

    for r in _records_by_kind(records, "curl_pipe_shell"):
        cid = f"tool:shell-pipe:{r['file']}:{r.get('line')}"
        node(cid, "tool", "curl | shell", file=r["file"], line=r.get("line"))
        roles[cid] = "outbound_channel"
        edge(agent_id, cid, "executes", "confirmed", "curl-pipe command in instructions")

    for r in _records_by_kind(records, "dependency"):
        cid = f"repo:{r['name']}"
        node(cid, "repo", f"{r['name']}@{r['spec']}")
        roles[cid] = "untrusted_source" if r.get("section") == "dependencies" else None

    for r in _records_by_kind(records, "action_ref"):
        cid = f"repo:{r['action']}"
        node(cid, "repo", f"{r['action']}@{r['ref']}", file=r["file"], line=r.get("line"),
             sha_pinned=r.get("sha_pinned"))
        roles[cid] = "untrusted_source" if not r.get("first_party") else None

    for cid, data in components.items():
        role = roles.get(cid)
        if role:
            data["data"]["role"] = role
    return list(components.values()), list(edges.values())


def audit_repository(root: Path) -> dict:
    """Run the full AgentGuard audit over a repository. Returns findings, the
    permission graph, parse coverage and the module's limitations."""
    root = Path(root)
    files, unsupported = _collect_files(root)
    parsed_files, records = _parse_all(files)

    findings = []
    findings += _excessive_permissions(records)
    findings += _dangerous_tool_execution(records)
    findings += _credential_exposure(records)
    findings += _mcp_supply_chain(records)
    findings += _prompt_injection(records)

    components, edges = _build_permission_graph(records)
    g = build_graph(components, edges)
    findings += _data_exfiltration_paths(g, deny_tools={
        r["tool"] for r in _records_by_kind(records, "permission_rule") if r.get("mode") == "deny"})

    findings.sort(key=lambda f: ({"high": 0, "medium": 1, "low": 2, "info": 3}[f["severity"]],
                                 f["category"], f["id"]))
    status_counts = {"verified": sum(1 for f in findings if f["status"] == "verified"),
                     "suspected": sum(1 for f in findings if f["status"] == "suspected")}
    category_counts = {c: sum(1 for f in findings if f["category"] == c) for c in CATEGORIES}

    return {
        "findings": findings,
        "status_counts": status_counts,
        "category_counts": category_counts,
        "graph": {"components": components, "edges": edges},
        "scanned": {"files": len(parsed_files),
                    "formats": sorted({p["format"] for p in parsed_files}),
                    "records": len(records)},
        "unsupported": unsupported,
        "limitations": [
            "Static configuration analysis only: nothing was executed, so no finding is proof of exploitability.",
            "Only the documented config formats are parsed; agent configs in other shapes are reported as unsupported, never guessed.",
            "Path findings ('suspected') show that a permission chain exists in configuration, not that it was exercised at runtime.",
            "Secret values are never recorded; credential findings reference names and redacted placeholders only.",
            "First-party GitHub actions (actions/*, github/*, docker/*) are treated as trusted publishers.",
        ],
    }
