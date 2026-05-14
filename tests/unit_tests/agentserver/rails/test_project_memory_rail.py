# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for ProjectMemoryRail (jiuwenclaw product-side)."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agents.harness.common.rails import project_memory_rail as _rail_mod
from jiuwenclaw.agents.harness.common.rails.project_memory import SECTION_NAME
from jiuwenclaw.agents.harness.common.rails.project_memory import (
    files as _files_mod,
)
from jiuwenclaw.agents.harness.common.rails.project_memory_rail import (
    ProjectMemoryRail,
)


@pytest.fixture(autouse=True)
def _isolate_user_and_managed_memory(monkeypatch):
    """Force test-local memory sources and clear cache between tests."""
    monkeypatch.setattr(_files_mod, "USER_MEMORY_FILES", ())
    monkeypatch.setattr(_files_mod, "USER_MEMORY_GLOBS", ())
    monkeypatch.setattr(_files_mod, "MANAGED_MEMORY_FILES", ())
    monkeypatch.setattr(_files_mod, "MANAGED_MEMORY_GLOBS", ())
    _files_mod.clear_project_memory_cache()
    yield
    _files_mod.clear_project_memory_cache()


def _touch(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_agent_with_builder() -> MagicMock:
    builder = MagicMock()
    builder.added_sections = []

    def _add(section):
        builder.added_sections = [
            s for s in builder.added_sections if s.name != section.name
        ]
        builder.added_sections.append(section)
        return builder

    def _remove(name):
        builder.added_sections = [s for s in builder.added_sections if s.name != name]
        return builder

    builder.add_section = MagicMock(side_effect=_add)
    builder.remove_section = MagicMock(side_effect=_remove)
    agent = MagicMock()
    agent.system_prompt_builder = builder
    return agent


@pytest.mark.asyncio
async def test_loads_jiuwenclaw_md_from_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "# test\nPROJECT RULE 1\n")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        sections = agent.system_prompt_builder.added_sections
        assert sections
        section = sections[-1]
        assert section.name == SECTION_NAME
        assert "PROJECT RULE 1" in section.content["en"]
        assert "PROJECT RULE 1" in section.content["cn"]


@pytest.mark.asyncio
async def test_section_is_bilingual_with_localized_headers():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "BODY")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        section = agent.system_prompt_builder.added_sections[-1]
        assert "Project Memory" in section.content["en"]
        assert "项目记忆" in section.content["cn"]


@pytest.mark.asyncio
async def test_merges_nested_project_memory_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "JIUWEN-CONTENT")
        _touch(root, ".jiuwen/JIUWENCLAW.md", "NESTED-JIUWEN-CONTENT")

        rail = ProjectMemoryRail(workspace=str(root), language="cn")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["cn"]
        assert "JIUWEN-CONTENT" in body
        assert "NESTED-JIUWEN-CONTENT" in body


@pytest.mark.asyncio
async def test_local_takes_highest_priority_position():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "PROJECT-LINE")
        _touch(root, "JIUWENCLAW.local.md", "LOCAL-LINE")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert body.index("PROJECT-LINE") < body.index("LOCAL-LINE")


@pytest.mark.asyncio
async def test_empty_directory_no_section():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        project_sections = [
            s for s in agent.system_prompt_builder.added_sections
            if s.name == SECTION_NAME
        ]
        assert project_sections == []


@pytest.mark.asyncio
async def test_walk_up_from_subdir():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "ROOT-RULE")
        sub = root / "src" / "feature"
        sub.mkdir(parents=True)

        rail = ProjectMemoryRail(workspace=str(sub), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "ROOT-RULE" in body


@pytest.mark.asyncio
async def test_refreshes_after_file_change():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "VERSION-1")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())
        body1 = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "VERSION-1" in body1

        _touch(root, "JIUWENCLAW.md", "VERSION-2")
        await rail.before_model_call(ctx=MagicMock())
        body2 = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "VERSION-2" in body2
        assert "VERSION-1" not in body2


@pytest.mark.asyncio
async def test_ignores_legacy_runtime_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, ".cursorrules", "CURSOR-RULE")
        _touch(root, "AGENTS.md", "AGENTS-RULE")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        sections = [
            s for s in agent.system_prompt_builder.added_sections
            if s.name == SECTION_NAME
        ]
        assert sections == []


@pytest.mark.asyncio
async def test_reads_rules_glob_dir():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, ".jiuwen/rules/01_style.md", "STYLE-RULE")
        _touch(root, ".jiuwen/rules/02_testing.md", "TEST-RULE")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "STYLE-RULE" in body
        assert "TEST-RULE" in body


@pytest.mark.asyncio
async def test_supports_include_directives():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, ".jiuwen/rules/shared.md", "SHARED-RULE")
        _touch(
            root,
            "JIUWENCLAW.md",
            "ROOT-LINE\n@include .jiuwen/rules/shared.md\nTAIL-LINE\n",
        )

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "ROOT-LINE" in body
        assert "TAIL-LINE" in body
        assert "SHARED-RULE" in body
        assert "@include" not in body


@pytest.mark.asyncio
async def test_conditional_rule_matches_workspace_subdir():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subdir = root / "src" / "feature"
        _touch(root, ".git/HEAD", "")
        subdir.mkdir(parents=True)
        _touch(
            root,
            ".jiuwen/rules/scoped.md",
            "---\npaths:\n  - src/**\n---\nSCOPED-RULE\n",
        )

        rail = ProjectMemoryRail(workspace=str(subdir), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "SCOPED-RULE" in body


@pytest.mark.asyncio
async def test_conditional_rule_matches_inline_paths_list():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subdir = root / "src" / "feature"
        _touch(root, ".git/HEAD", "")
        subdir.mkdir(parents=True)
        _touch(
            root,
            ".jiuwen/rules/scoped.md",
            "---\npaths: [src/**, lib/**]\n---\nINLINE-SCOPED-RULE\n",
        )

        rail = ProjectMemoryRail(workspace=str(subdir), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "INLINE-SCOPED-RULE" in body


@pytest.mark.asyncio
async def test_conditional_rule_skips_nonmatching_workspace():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        docs = root / "docs"
        _touch(root, ".git/HEAD", "")
        docs.mkdir(parents=True)
        _touch(
            root,
            ".jiuwen/rules/scoped.md",
            "---\npaths:\n  - src/**\n---\nSCOPED-RULE\n",
        )

        rail = ProjectMemoryRail(workspace=str(docs), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        sections = [
            s for s in agent.system_prompt_builder.added_sections
            if s.name == SECTION_NAME
        ]
        assert sections == []


@pytest.mark.asyncio
async def test_frontmatter_stripped():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(
            root,
            "JIUWENCLAW.md",
            "---\npaths: 'src/**'\nversion: 1\n---\nBODY-ONLY\n",
        )

        rail = ProjectMemoryRail(workspace=str(root / "src"), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "BODY-ONLY" in body
        assert "paths:" not in body
        assert "version:" not in body


@pytest.mark.asyncio
async def test_frontmatter_at_end_of_file_stripped():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "---\nkey: v\n---")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        sections = [
            s for s in agent.system_prompt_builder.added_sections
            if s.name == SECTION_NAME
        ]
        assert sections == []


@pytest.mark.asyncio
async def test_uninit_clears_section():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "BEFORE-UNINIT")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())
        assert agent.system_prompt_builder.added_sections

        rail.uninit(agent)
        assert [
            s for s in agent.system_prompt_builder.added_sections
            if s.name == SECTION_NAME
        ] == []


@pytest.mark.asyncio
async def test_no_builder_is_safe():
    rail = ProjectMemoryRail(workspace="/tmp", language="en")
    agent = MagicMock()
    agent.system_prompt_builder = None
    rail.init(agent)
    await rail.before_model_call(ctx=MagicMock())


@pytest.mark.asyncio
async def test_set_language_switches_active_language():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "BODY")

        rail = ProjectMemoryRail(workspace=str(root), language="cn")
        rail.set_language("en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        assert rail.get_language() == "en"


@pytest.mark.asyncio
async def test_loads_user_and_managed_rules(monkeypatch):
    with (
        tempfile.TemporaryDirectory() as td,
        tempfile.TemporaryDirectory() as user_td,
        tempfile.TemporaryDirectory() as managed_td,
    ):
        root = Path(td)
        user_root = Path(user_td)
        managed_root = Path(managed_td)
        _touch(root, ".git/HEAD", "")
        _touch(user_root, "JIUWENCLAW.md", "USER-MEMORY")
        _touch(user_root, "rules/user_rule.md", "USER-RULE")
        _touch(managed_root, "JIUWENCLAW.md", "MANAGED-MEMORY")
        _touch(managed_root, "rules/managed_rule.md", "MANAGED-RULE")

        monkeypatch.setattr(_files_mod, "USER_MEMORY_FILES", (str(user_root / "JIUWENCLAW.md"),))
        monkeypatch.setattr(_files_mod, "USER_MEMORY_GLOBS", (str(user_root / "rules" / "*.md"),))
        monkeypatch.setattr(_files_mod, "MANAGED_MEMORY_FILES", (str(managed_root / "JIUWENCLAW.md"),))
        monkeypatch.setattr(_files_mod, "MANAGED_MEMORY_GLOBS", (str(managed_root / "rules" / "*.md"),))
        _files_mod.clear_project_memory_cache()

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "USER-MEMORY" in body
        assert "USER-RULE" in body
        assert "MANAGED-MEMORY" in body
        assert "MANAGED-RULE" in body


@pytest.mark.asyncio
async def test_additional_directories_load_project_memory():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as extra_td:
        root = Path(td)
        extra = Path(extra_td)
        _touch(root, ".git/HEAD", "")
        _touch(extra, "JIUWENCLAW.md", "EXTRA-PROJECT-RULE")

        rail = ProjectMemoryRail(
            workspace=str(root),
            language="en",
            additional_directories=(str(extra),),
        )
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "EXTRA-PROJECT-RULE" in body


@dataclass
class _FakeWorkspace:
    root_path: str


@pytest.mark.asyncio
async def test_additional_directories_relative_to_workspace():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / "repo"
        extra = base / "shared-memory"
        _touch(root, ".git/HEAD", "")
        _touch(extra, "JIUWENCLAW.md", "RELATIVE-EXTRA-RULE")

        rail = ProjectMemoryRail(
            workspace=str(root),
            language="en",
            additional_directories=("../shared-memory",),
        )
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "RELATIVE-EXTRA-RULE" in body


@pytest.mark.asyncio
async def test_b1_parent_set_workspace_does_not_break_path_resolution():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "STILL-WORKING")

        rail = ProjectMemoryRail(workspace="/nonexistent-stub-path", language="en")
        rail.set_workspace(_FakeWorkspace(root_path=str(root)))  # type: ignore[arg-type]

        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        sections = [
            s for s in agent.system_prompt_builder.added_sections
            if s.name == SECTION_NAME
        ]
        assert sections
        assert "STILL-WORKING" in sections[-1].content["en"]


@pytest.mark.asyncio
async def test_b1_no_workspace_falls_back_to_constructor_path():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, ".git/HEAD", "")
        _touch(root, "JIUWENCLAW.md", "FROM-CTOR-PATH")

        rail = ProjectMemoryRail(workspace=str(root), language="en")
        assert rail.workspace is None

        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        section = agent.system_prompt_builder.added_sections[-1]
        assert "FROM-CTOR-PATH" in section.content["en"]


@pytest.mark.asyncio
async def test_nested_worktree_skips_canonical_project_but_keeps_local(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        canonical = Path(td) / "repo"
        worktree = canonical / ".jiuwen" / "worktrees" / "feature"
        _touch(canonical, ".git/HEAD", "")
        _touch(worktree, ".git/HEAD", "")
        _touch(canonical, "JIUWENCLAW.md", "CANONICAL-PROJECT")
        _touch(canonical, "JIUWENCLAW.local.md", "CANONICAL-LOCAL")
        _touch(worktree, "JIUWENCLAW.md", "WORKTREE-PROJECT")

        monkeypatch.setattr(
            _files_mod,
            "_detect_git_worktree",
            lambda _cwd: _files_mod.GitWorktreeInfo(
                worktree_root=worktree.resolve(),
                canonical_root=canonical.resolve(),
            ),
        )
        _files_mod.clear_project_memory_cache()

        rail = ProjectMemoryRail(workspace=str(worktree), language="en")
        agent = _make_agent_with_builder()
        rail.init(agent)
        await rail.before_model_call(ctx=MagicMock())

        body = agent.system_prompt_builder.added_sections[-1].content["en"]
        assert "WORKTREE-PROJECT" in body
        assert "CANONICAL-LOCAL" in body
        assert "CANONICAL-PROJECT" not in body


@pytest.mark.asyncio
async def test_after_tool_call_invalidates_cache_for_write_tools(monkeypatch):
    rail = ProjectMemoryRail(workspace="/tmp/project", language="en")
    clear_mock = MagicMock()
    monkeypatch.setattr(_rail_mod, "clear_project_memory_cache", clear_mock)

    ctx = SimpleNamespace(inputs=SimpleNamespace(tool_name="write_file"))
    await rail.after_tool_call(ctx)

    clear_mock.assert_called_once_with("/tmp/project")


def test_resolve_workspace_path_prefers_workspace_root_path():
    rail = ProjectMemoryRail(workspace="/ctor-fallback", language="en")
    rail.set_workspace(_FakeWorkspace(root_path="/from-injection"))  # type: ignore[arg-type]
    assert rail.resolve_workspace_path() == "/from-injection"


def test_resolve_workspace_path_falls_back_when_workspace_missing():
    rail = ProjectMemoryRail(workspace="/ctor-fallback", language="en")
    assert rail.workspace is None
    assert rail.resolve_workspace_path() == "/ctor-fallback"


def test_module_exports_project_memory_rail():
    assert hasattr(_rail_mod, "ProjectMemoryRail")
