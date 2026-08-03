"""Tests for project-memory discovery, precedence, and truncation."""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenclaw.agentserver.deep_agent.rails.project_memory.files import (
    _extract_include_spec,
    clear_project_memory_cache,
    discover_and_load_memory_files,
    merge_memory_content,
)


def test_discovery_loads_project_and_rules_with_invalid_extra_dir(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".jiuwen" / "rules").mkdir(parents=True)
    (tmp_path / "JIUWENSWARM.md").write_text("project-rule", encoding="utf-8")
    (tmp_path / ".jiuwen" / "rules" / "01-base.md").write_text(
        "base-rule", encoding="utf-8"
    )
    (tmp_path / ".jiuwen" / "rules" / "ignored.md").mkdir()
    clear_project_memory_cache()

    files = discover_and_load_memory_files(
        workspace=str(tmp_path),
        additional_directories=[str(tmp_path / "missing"), str(tmp_path / ".git")],
    )

    contents = "\n".join(item.content for item in files)
    assert "project-rule" in contents
    assert "base-rule" in contents
    assert not any(item.path.endswith("ignored.md") for item in files)
    project_index = next(
        index
        for index, item in enumerate(files)
        if item.path.endswith("JIUWENSWARM.md")
    )
    rules_index = next(
        index for index, item in enumerate(files) if item.path.endswith("01-base.md")
    )
    assert project_index < rules_index
    assert all(Path(item.path).exists() for item in files)
    clear_project_memory_cache()


def test_merge_memory_content_has_soft_character_cap(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "JIUWENSWARM.md").write_text("x" * 200, encoding="utf-8")
    clear_project_memory_cache()
    files = discover_and_load_memory_files(workspace=str(tmp_path))

    merged = merge_memory_content(files, max_chars=40)

    assert "project memory truncated" in merged
    clear_project_memory_cache()


def test_include_cannot_escape_workspace_or_follow_external_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    outside = tmp_path / "secret.env"
    outside.write_text("TOP-SECRET", encoding="utf-8")
    (workspace / "safe.md").write_text("SAFE-INCLUDE", encoding="utf-8")
    link = workspace / "linked-secret.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    (workspace / "JIUWENSWARM.md").write_text(
        f"@include safe.md\n@include {outside}\n@include ../secret.env\n@include linked-secret.md",
        encoding="utf-8",
    )
    clear_project_memory_cache()

    files = discover_and_load_memory_files(workspace=str(workspace))

    assert "SAFE-INCLUDE" in "\n".join(item.content for item in files)
    assert "TOP-SECRET" not in "\n".join(item.content for item in files)
    clear_project_memory_cache()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("@include shared.md", "shared.md"),
        ("@import shared.md", "shared.md"),
        ("@property", None),
        ("@author Jane Doe", None),
        ("@see https://example.com", None),
        ("@@include shared.md", None),
    ],
)
def test_extract_include_spec_only_accepts_include_directives(
    line: str,
    expected: str | None,
) -> None:
    assert _extract_include_spec(line) == expected
