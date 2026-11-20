"""DevDNA Change Intelligence - Change Detection Service.

Extracts structured code changes from git diffs, pull requests, or revisions:
- Added, modified, deleted, renamed files
- Changed line ranges per file
- AST-level changed functions, classes, and imports (Python & JS/TS)
- Honest fallback to file-level granularity when AST resolution cannot be established.
"""
from __future__ import annotations

import ast
import logging
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services.change_impact import normalise_path

log = logging.getLogger("devdna.change_detection")

DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<old_path>.+) b/(?P<new_path>.+)$")
HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


@dataclass
class ChangedComponent:
    id: str
    name: str
    type: str  # function, class, route, module
    change_type: str  # added, modified, deleted
    file: str
    line_range: tuple[int, int]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChangedImport:
    name: str
    change_type: str  # added, removed, modified
    file: str
    line: int | None = None


@dataclass
class FileChange:
    path: str
    status: str  # added, modified, deleted, renamed
    old_path: str | None = None
    changed_lines: list[tuple[int, int]] = field(default_factory=list)
    granularity: str = "file_level"  # function_level or file_level
    granularity_reason: str | None = None
    changed_components: list[dict[str, Any]] = field(default_factory=list)
    changed_imports: list[dict[str, Any]] = field(default_factory=list)
    patch: str | None = None


def parse_unified_diff(diff_text: str) -> list[FileChange]:
    """Parse unified git diff output into structured FileChange objects."""
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    file_changes: list[FileChange] = []
    current_file: FileChange | None = None
    patch_lines: list[str] = []

    def _flush_current():
        nonlocal current_file, patch_lines
        if current_file:
            if patch_lines:
                current_file.patch = "\n".join(patch_lines)
            file_changes.append(current_file)
            current_file = None
            patch_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]
        header_match = DIFF_HEADER_RE.match(line)
        if header_match:
            _flush_current()
            old_p = normalise_path(header_match.group("old_path"))
            new_p = normalise_path(header_match.group("new_path"))
            current_file = FileChange(path=new_p, status="modified", old_path=old_p if old_p != new_p else None)
            patch_lines = [line]
            i += 1
            continue

        if current_file:
            patch_lines.append(line)
            if line.startswith("new file mode"):
                current_file.status = "added"
            elif line.startswith("deleted file mode"):
                current_file.status = "deleted"
            elif line.startswith("rename from "):
                current_file.status = "renamed"
                current_file.old_path = normalise_path(line[12:].strip())
            elif line.startswith("rename to "):
                current_file.path = normalise_path(line[10:].strip())
            else:
                hunk_match = HUNK_HEADER_RE.match(line)
                if hunk_match:
                    new_start = int(hunk_match.group("new_start"))
                    new_count = int(hunk_match.group("new_count") or "1")
                    if new_count > 0:
                        current_file.changed_lines.append((new_start, new_start + new_count - 1))
                    else:
                        current_file.changed_lines.append((new_start, new_start))
        i += 1

    _flush_current()
    return file_changes


def _extract_python_ast_components(source: str, rel_path: str) -> tuple[dict[str, tuple[int, int, str]], list[tuple[str, int]]]:
    """Extract (id -> (start_line, end_line, node_dump)) and imports from Python source."""
    components: dict[str, tuple[int, int, str]] = {}
    imports: list[tuple[str, int]] = []
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return components, imports

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cid = f"func:{rel_path}:{node.name}"
            end_line = getattr(node, "end_lineno", node.lineno)
            dump = ast.dump(node, annotate_fields=False)
            components[cid] = (node.lineno, end_line, dump)
        elif isinstance(node, ast.ClassDef):
            cid = f"class:{rel_path}:{node.name}"
            end_line = getattr(node, "end_lineno", node.lineno)
            dump = ast.dump(node, annotate_fields=False)
            components[cid] = (node.lineno, end_line, dump)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append((f"{mod}.{alias.name}" if mod else alias.name, node.lineno))

    return components, imports


def enrich_file_changes_with_ast(
    file_changes: list[FileChange],
    base_root: Path | None = None,
    head_root: Path | None = None,
) -> None:
    """Enhance FileChange list with AST-level component and import changes."""
    for change in file_changes:
        rel = change.path
        if not rel.endswith(".py"):
            change.granularity = "file_level"
            change.granularity_reason = "Non-Python file: static AST function inspection currently supported for Python."
            continue

        base_source = ""
        head_source = ""
        old_rel = change.old_path or rel

        if base_root and (base_root / old_rel).is_file():
            try:
                base_source = (base_root / old_rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                base_source = ""

        if head_root and (head_root / rel).is_file():
            try:
                head_source = (head_root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                head_source = ""

        if not head_source and not base_source:
            change.granularity = "file_level"
            change.granularity_reason = "Source content unavailable on disk for AST comparison."
            continue

        base_comps, base_imports = _extract_python_ast_components(base_source, old_rel) if base_source else ({}, [])
        head_comps, head_imports = _extract_python_ast_components(head_source, rel) if head_source else ({}, [])

        # If head source failed to parse or produced no components
        if head_source and not head_comps and change.status != "deleted":
            change.granularity = "file_level"
            change.granularity_reason = "Syntax error or module-level script with no explicit function/class definitions."
            continue

        changed_comps: list[dict[str, Any]] = []

        # Find deleted components
        for cid, (start, end, dump) in base_comps.items():
            if cid not in head_comps:
                name = cid.split(":")[-1]
                ctype = "class" if cid.startswith("class:") else "function"
                changed_comps.append(asdict(ChangedComponent(
                    id=cid, name=name, type=ctype, change_type="deleted",
                    file=old_rel, line_range=(start, end),
                )))

        # Find added or modified components
        for cid, (start, end, dump) in head_comps.items():
            name = cid.split(":")[-1]
            ctype = "class" if cid.startswith("class:") else "function"
            if cid not in base_comps:
                changed_comps.append(asdict(ChangedComponent(
                    id=cid, name=name, type=ctype, change_type="added",
                    file=rel, line_range=(start, end),
                )))
            else:
                base_start, base_end, base_dump = base_comps[cid]
                # Check if AST dump changed or line range intersected diff hunk
                line_intersect = any(
                    not (end < h_start or start > h_end)
                    for (h_start, h_end) in change.changed_lines
                )
                if dump != base_dump or line_intersect:
                    changed_comps.append(asdict(ChangedComponent(
                        id=cid, name=name, type=ctype, change_type="modified",
                        file=rel, line_range=(start, end),
                    )))

        # Detect changed imports
        base_imp_names = {name for name, _ in base_imports}
        head_imp_names = {name for name, _ in head_imports}
        changed_imps: list[dict[str, Any]] = []
        for name, line in head_imports:
            if name not in base_imp_names:
                changed_imps.append(asdict(ChangedImport(name=name, change_type="added", file=rel, line=line)))
        for name, line in base_imports:
            if name not in head_imp_names:
                changed_imps.append(asdict(ChangedImport(name=name, change_type="removed", file=old_rel, line=line)))

        change.changed_components = changed_comps
        change.changed_imports = changed_imps
        if changed_comps:
            change.granularity = "function_level"
            change.granularity_reason = f"Identified {len(changed_comps)} changed function(s)/class(es) via Python AST."
        else:
            change.granularity = "file_level"
            change.granularity_reason = "Changes occurred outside function/class bodies (module-level code, comments, or imports)."


def detect_git_changes(
    repo_path: Path,
    base_ref: str | None = None,
    head_ref: str | None = None,
) -> tuple[list[FileChange], dict[str, Any]]:
    """Detect changes directly from a local git repository.

    Supports:
    - base_ref and head_ref (e.g. 'main' and 'feature-branch' or commit SHAs)
    - base_ref alone (compares working tree against base_ref)
    - neither (compares working tree against HEAD)
    """
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        raise ValueError(f"Path '{repo}' is not a valid git repository (missing .git).")

    cmd = ["git", "-C", str(repo), "diff", "--unified=3"]
    if base_ref and head_ref:
        cmd.extend([f"{base_ref}...{head_ref}"])
    elif base_ref:
        cmd.extend([base_ref])
    else:
        cmd.extend(["HEAD"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("Failed to execute git diff on %s: %s", repo, e)
        raise RuntimeError(f"Git diff failed or timed out: {e}") from e

    if proc.returncode != 0:
        log.warning("git diff returned exit code %d: %s", proc.returncode, proc.stderr)
        # Fallback to status if diff returned non-zero (e.g. empty new repo)
        return [], {"error": proc.stderr.strip() or "git diff failed", "exit_code": proc.returncode}

    file_changes = parse_unified_diff(proc.stdout)
    enrich_file_changes_with_ast(file_changes, base_root=repo, head_root=repo)

    summary = {
        "total_files": len(file_changes),
        "added": sum(1 for f in file_changes if f.status == "added"),
        "modified": sum(1 for f in file_changes if f.status == "modified"),
        "deleted": sum(1 for f in file_changes if f.status == "deleted"),
        "renamed": sum(1 for f in file_changes if f.status == "renamed"),
        "changed_functions": sum(len(f.changed_components) for f in file_changes),
        "changed_imports": sum(len(f.changed_imports) for f in file_changes),
    }

    return file_changes, summary


def build_change_manifest(file_changes: list[FileChange]) -> dict[str, Any]:
    """Serialize file changes and summary into a clean, deterministic dictionary."""
    file_changes.sort(key=lambda f: f.path)
    return {
        "summary": {
            "total_files": len(file_changes),
            "added": sum(1 for f in file_changes if f.status == "added"),
            "modified": sum(1 for f in file_changes if f.status == "modified"),
            "deleted": sum(1 for f in file_changes if f.status == "deleted"),
            "renamed": sum(1 for f in file_changes if f.status == "renamed"),
            "changed_functions": sum(len(f.changed_components) for f in file_changes),
            "changed_imports": sum(len(f.changed_imports) for f in file_changes),
        },
        "files": [
            {
                "path": f.path,
                "status": f.status,
                "old_path": f.old_path,
                "changed_lines": f.changed_lines,
                "granularity": f.granularity,
                "granularity_reason": f.granularity_reason,
                "changed_components": f.changed_components,
                "changed_imports": f.changed_imports,
            }
            for f in file_changes
        ],
    }
