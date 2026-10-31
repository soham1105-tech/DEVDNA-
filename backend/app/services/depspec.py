"""Strict, shared parsing of pip-style dependency manifests.

One parser, two consumers: the runner (which builds the sandbox image) and the
analyser's dependency display. Only `name[extras] spec [; marker]` requirement
lines are trusted. Everything else — index options (`--index-url`,
`--extra-index-url`, `--trusted-host`), editables, `-r` includes that escape
the project, VCS/URL/file/directory/git specs and wildcard (`*`) versions — is
rejected with a clear `DependencySpecError`, never silently widened.

Poetry caret and tilde constraints are translated to comparison pairs:
`^1.2.3` becomes `>=1.2.3,<2.0.0` (and `^0.2.3` becomes `>=0.2.3,<0.3.0`),
`~1.2.3` becomes `>=1.2.3,<1.3.0`. Bare versions are pinned (`1.2.3` ->
`==1.2.3`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_REDACTION_NOTE = "line dropped for sandbox safety"


class DependencySpecError(ValueError):
    """A dependency manifest contains lines that may not reach pip."""


_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_REQ_LINE = re.compile(rf"^(?P<name>{_NAME})(?P<extras>\[[A-Za-z0-9_. ,-]*\])?(?P<rest>.*)$")
_MARKER = re.compile(r'^[A-Za-z0-9_ .<>=!",()\'-]+$')
_SPEC_CHARS = re.compile(r"^[A-Za-z0-9.+!=<>,~^ \-]+$")
_VERSION = re.compile(r"^\d+(?:\.\d+)*$")
_OPTION = re.compile(r"^\s*-{1,2}[A-Za-z]")


@dataclass
class ParsedRequirements:
    lines: list[str] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)


def _split_version(version: str) -> list[int]:
    return [int(part) for part in version.split(".")]


def _caret_upper(version: str) -> str:
    parts = _split_version(version)
    while len(parts) < 3:
        parts.append(0)
    if parts[0] > 0:
        return f"{parts[0] + 1}.0.0"
    if parts[1] > 0:
        return f"0.{parts[1] + 1}.0"
    return f"0.0.{parts[2] + 1}"


def _translate_part(part: str) -> str:
    part = part.strip()
    if part.startswith("^"):
        version = part[1:].strip()
        if not _VERSION.match(version):
            raise DependencySpecError(f"unsupported Poetry caret version '{part}'")
        return f">={version},<{_caret_upper(version)}"
    if part.startswith("~") and not part.startswith("~="):
        version = part[1:].strip()
        if not _VERSION.match(version):
            raise DependencySpecError(f"unsupported Poetry tilde version '{part}'")
        segments = _split_version(version)
        if len(segments) >= 2:
            upper = f"{segments[0]}.{segments[1] + 1}.0"
        else:
            upper = f"{segments[0] + 1}.0.0"
        return f">={version},<{upper}"
    return part


def _normalise_spec(spec: str) -> str:
    spec = spec.strip()
    if not spec:
        return ""
    if "*" in spec:
        raise DependencySpecError("wildcard versions cannot be pinned")
    if "--" in spec:
        raise DependencySpecError("pip options are not allowed in requirement lines")
    if not _SPEC_CHARS.match(spec):
        raise DependencySpecError(f"unsupported characters in version spec '{spec}'")
    parts = [_translate_part(piece) for piece in spec.split(",")]
    out = []
    for piece in ", ".join(parts).split(","):
        piece = piece.strip()
        if not piece:
            continue
        if re.match(r"^(?:<|>|<=|>=|==|!=|~=)\s*\S+$", piece) or _VERSION.match(piece):
            out.append(piece)
        elif _VERSION.match(piece.lstrip("<>=!~")):
            out.append(piece)
        else:
            # a bare version with no operator must be pinned, never ranged
            raise DependencySpecError(f"unsupported version spec '{piece}'")
    normalised = []
    for piece in out:
        if _VERSION.match(piece):
            normalised.append(f"=={piece}")
        elif re.match(r"^[<>]", piece):
            operator, _, version = piece.partition(" ")
            normalised.append(f"{operator.strip()}{version.strip()}" if version else piece)
        else:
            normalised.append(piece)
    result = ",".join(normalised)
    return result


def parse_requirements_text(text: str, *, source: str = "requirements.txt", strict: bool = True) -> ParsedRequirements:
    """Validate pip-style requirement lines.

    Returns the sanitised lines (and structured entries for display). In
    strict mode (runner) any disallowed line raises DependencySpecError
    naming every offending line; in lenient mode (analyser display) they are
    collected in `rejections` and skipped.
    """
    parsed = ParsedRequirements()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith("\\"):
            _reject(parsed, line, source, "line continuations are not supported", strict)
            continue
        if _OPTION.match(line):
            _reject(parsed, line, source,
                    "pip options (--index-url, --extra-index-url, --trusted-host, --find-links, -e ...) "
                    "are not allowed: they can redirect package sources or execute code", strict)
            continue
        match = _REQ_LINE.match(line)
        if not match:
            _reject(parsed, line, source, "not a name[extras] spec requirement line", strict)
            continue
        name, extras, rest = match.group("name"), match.group("extras") or "", match.group("rest").strip()
        marker = ""
        if ";" in rest:
            spec, marker = rest.split(";", 1)
            spec, marker = spec.strip(), marker.strip()
            if marker and not _MARKER.match(marker):
                _reject(parsed, line, source, "unsupported environment marker", strict)
                continue
        else:
            spec = rest
        if spec and (re.match(r"^[A-Za-z0-9+.\-]+://", spec)
                     or re.match(r"^(?:file:|\.{1,2}/|[A-Za-z]:[\\/])", spec)
                     or spec.startswith("@")):
            _reject(parsed, line, source,
                    "VCS, URL and local file/directory specs cannot be pinned or vendored", strict)
            continue
        try:
            normalised = _normalise_spec(spec)
        except DependencySpecError as exc:
            _reject(parsed, line, source, str(exc), strict)
            continue
        if normalised and "--" in normalised:  # defence in depth
            _reject(parsed, line, source, "pip options are not allowed in requirement lines", strict)
            continue
        rebuilt = f"{name}{extras}{normalised}"
        if marker:
            rebuilt = f"{rebuilt} ; {marker}"
        parsed.lines.append(rebuilt)
        parsed.entries.append({"name": name, "spec": normalised, "manifest": source})
    return parsed


def _reject(parsed: ParsedRequirements, line: str, source: str, reason: str, strict: bool) -> None:
    message = f"{source}: rejected '{line}' — {reason}"
    parsed.rejections.append(message)
    if strict:
        raise DependencySpecError(message)


def parse_requirements_file(path: Path, project_dir: Path, *, strict: bool = True) -> ParsedRequirements:
    """Read a requirements file, following -r includes that stay inside the project.

    Includes escaping the project directory and circular includes are rejected
    with clear errors. Editables (`-e`) pointing inside the project are dropped
    with a note (the project source is mounted at run time; building it during
    the image build would execute untrusted code) — an editable that escapes
    the project is rejected. Nothing ever widens what pip sees.
    """
    project_dir = project_dir.resolve()
    path = path.resolve(strict=True)
    if project_dir not in path.parents and path != project_dir:
        raise DependencySpecError(f"Dependency include escapes the project directory: {path.name}")

    parsed = ParsedRequirements()
    visited: set[Path] = {path}

    def read(current: Path) -> None:
        raw_lines = current.read_text(encoding="utf-8").splitlines()
        body_lines = []
        for line in raw_lines:
            if re.match(r"^\s*(?:-r|--requirement(?:=|\s+))", line):
                # -r includes are resolved below; the line parser must not see them
                body_lines.append(line)
                continue
            editable = re.match(r"^\s*(?:-e|--editable(?:=|\s+))\s*(.+?)\s*$", line)
            if editable:
                target_arg = editable.group(1).strip().strip("\"'")
                target = Path(target_arg)
                target = target.resolve() if target.is_absolute() else (current.parent / target).resolve()
                if project_dir in target.parents or target == project_dir:
                    parsed.notes.append(
                        f"{current.name}: editable install of {target.name} dropped — "
                        "the project source is mounted at run time instead"
                    )
                    continue
                # an editable escaping the project stays in the body so the
                # line parser rejects it with the options error
            body_lines.append(line)
        # -r includes are resolved below; the line parser must not see them
        body = "\n".join(line for line in body_lines
                         if not re.match(r"^\s*(?:-r|--requirement(?:=|\s+))", line))
        inner = parse_requirements_text(body, source=current.name, strict=False)
        parsed.lines.extend(inner.lines)
        parsed.entries.extend(inner.entries)
        parsed.rejections.extend(inner.rejections)
        parsed.notes.extend(inner.notes)
        for line in raw_lines:
            match = re.match(r"^\s*(?:-r|--requirement(?:=|\s+))\s*(.+?)\s*$", line)
            if not match:
                continue
            include = Path(match.group(1).strip().strip("\"'"))
            if include.is_absolute():
                target = include.resolve()
            else:
                target = (current.parent / include).resolve()
            if project_dir not in target.parents and target != project_dir:
                raise DependencySpecError(f"Dependency include escapes the project directory: {target.name}")
            if target in visited:
                raise DependencySpecError(f"Circular dependency include in {target.name}")
            visited.add(target)
            read(target)

    read(path)
    if strict and parsed.rejections:
        raise DependencySpecError("; ".join(parsed.rejections))
    return parsed
