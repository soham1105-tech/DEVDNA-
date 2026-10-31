"""Shared dependency spec parser: only safe name[extras] spec lines survive."""
import pytest

from app.services.depspec import DependencySpecError, parse_requirements_text


def test_plain_pinned_lines_pass_through():
    parsed = parse_requirements_text("fastapi==0.115.0\nhttpx>=0.27\n")
    assert parsed.lines == ["fastapi==0.115.0", "httpx>=0.27"]
    assert parsed.rejections == []


def test_poetry_caret_is_translated_not_widened():
    assert parse_requirements_text("foo ^1.2.3").lines == ["foo>=1.2.3,<2.0.0"]
    assert parse_requirements_text("foo ^0.2.3").lines == ["foo>=0.2.3,<0.3.0"]
    assert parse_requirements_text("foo ^0.0.3").lines == ["foo>=0.0.3,<0.0.4"]
    assert parse_requirements_text("foo ^1.2").lines == ["foo>=1.2,<2.0.0"]


def test_poetry_tilde_is_translated_not_widened():
    assert parse_requirements_text("foo ~1.2.3").lines == ["foo>=1.2.3,<1.3.0"]
    assert parse_requirements_text("foo ~1.2").lines == ["foo>=1.2,<1.3.0"]


def test_pep440_compatible_release_is_kept():
    assert parse_requirements_text("foo~=1.2.3").lines == ["foo~=1.2.3"]


def test_bare_version_is_pinned():
    assert parse_requirements_text("foo 1.2.3").lines == ["foo==1.2.3"]


def test_wildcard_versions_are_rejected():
    with pytest.raises(DependencySpecError, match="wildcard"):
        parse_requirements_text("requests*")
    with pytest.raises(DependencySpecError, match="wildcard"):
        parse_requirements_text('requests = "*"')


def test_index_options_are_rejected():
    for line in ("--index-url https://evil.example.com/simple",
                 "--extra-index-url https://evil.example.com/simple",
                 "--trusted-host evil.example.com"):
        with pytest.raises(DependencySpecError, match="pip options"):
            parse_requirements_text(line)


def test_vcs_and_url_specs_are_rejected():
    for line in ("foo @ git+https://github.com/evil/evil",
                 "git+https://github.com/evil/evil",
                 "foo @ https://evil.example.com/pkg.whl",
                 "foo @ file:///tmp/evil.whl"):
        with pytest.raises(DependencySpecError, match="VCS, URL"):
            parse_requirements_text(line)


def test_options_inside_a_requirement_line_are_rejected():
    with pytest.raises(DependencySpecError, match="pip options"):
        parse_requirements_text("requests==2.0 --index-url https://evil")


def test_lenient_mode_collects_rejections_instead_of_raising():
    parsed = parse_requirements_text(
        "fastapi==0.115.0\n--index-url https://evil\ngit+https://github.com/evil/x",
        strict=False,
    )
    assert parsed.lines == ["fastapi==0.115.0"]
    assert len(parsed.rejections) == 2
    assert all("rejected" in r for r in parsed.rejections)


def test_markers_and_extras_survive():
    parsed = parse_requirements_text('uvicorn[standard]>=0.30 ; python_version >= "3.11"')
    assert parsed.lines == ['uvicorn[standard]>=0.30 ; python_version >= "3.11"']
