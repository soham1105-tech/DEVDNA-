"""Resolve finding owners from CODEOWNERS or the most recent file committer."""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

CODEOWNERS_FILES = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def _codeowners_rules(root: Path) -> list[tuple[str, tuple[str, ...]]]:
    for candidate in CODEOWNERS_FILES:
        path = root / candidate
        if path.is_file():
            rules = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.split("#", 1)[0].strip()
                parts = line.split()
                if len(parts) > 1:
                    rules.append((parts[0], tuple(parts[1:])))
            return rules
    return []


def _matches(pattern: str, relative_path: str) -> bool:
    pattern = pattern.replace("\\", "/")
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if pattern.endswith("/"):
        return relative_path == pattern[:-1] or relative_path.startswith(pattern)
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in relative_path.split("/"))
    return fnmatch.fnmatchcase(relative_path, pattern)


def _blame_owner(root: Path, relative_path: str, line: int | None) -> str | None:
    commands = []
    if line and line > 0:
        commands.append(["git", "-C", str(root), "blame", "--line-porcelain", "-L", f"{line},{line}", "--", relative_path])
    commands.append(["git", "-C", str(root), "log", "-1", "--format=%an <%ae>", "--", relative_path])
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode:
            continue
        if command[3] == "blame":
            author = next((entry[7:] for entry in result.stdout.splitlines() if entry.startswith("author ")), "")
            email = next((entry[12:-1] for entry in result.stdout.splitlines() if entry.startswith("author-mail ")), "")
            owner = f"{author} <{email}>" if author and email else author
        else:
            owner = result.stdout.strip()
        if owner and owner != "Not Committed Yet":
            return owner
    return None


def assign_finding_owners(root: Path, findings: list[dict], components: list[dict]) -> None:
    """Attach an owner to every finding in place, preferring the matching CODEOWNERS rule."""
    root = root.resolve()
    rules = _codeowners_rules(root)
    component_locations = {
        component["id"]: {"file": component.get("file"), "line": component.get("line")}
        for component in components
    }
    owners_by_location: dict[tuple[str, int | None], str] = {}
    for finding in findings:
        evidence = finding.get("evidence") or []
        component_location = component_locations.get(finding.get("component_id"), {})
        location = next((entry for entry in evidence if entry.get("file")), {})
        source_file = component_location.get("file") or location.get("file")
        line = component_location.get("line") if component_location.get("file") else location.get("line")
        if not source_file:
            finding["owner"] = "Unassigned"
            continue
        relative = str(source_file).replace("\\", "/").lstrip("/")
        if ".." in Path(relative).parts:
            finding["owner"] = "Unassigned"
            continue
        owner = None
        for pattern, candidates in rules:
            if _matches(pattern, relative):
                owner = " ".join(candidates)
        if owner is None:
            key = (relative, line)
            if key not in owners_by_location:
                owners_by_location[key] = _blame_owner(root, relative, line) or "Unassigned"
            owner = owners_by_location[key]
        finding["owner"] = owner
