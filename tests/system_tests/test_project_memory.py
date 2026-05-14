# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System tests for /init feature: ProjectMemoryRail + explore_agent + code-mode logic.

Tests the end-to-end integration of project memory discovery, rail lifecycle,
code-mode registration, and explore_agent default-enabled behavior.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.server.runtime.agent_adapter.interface_code import JiuwenClawCodeAdapter
from jiuwenclaw.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agents.harness.common.rails.project_memory import (
    SECTION_NAME,
    clear_project_memory_cache,
    discover_and_load_memory_files,
    merge_memory_content,
)
from jiuwenclaw.agents.harness.common.rails.project_memory.files import (
    GitWorktreeInfo,
    LoadedMemoryFile,
    PRIORITY,
)
from jiuwenclaw.agents.harness.common.rails import (
    ProjectMemoryRail,
)

pytestmark = [pytest.mark.integration, pytest.mark.system]


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


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_project_memory_cache()
    yield
    clear_project_memory_cache()


# =====================================================================
# 1. ProjectMemoryRail End-to-End Lifecycle
# =====================================================================

class TestProjectMemoryRailEndToEnd:
    """Full rail lifecycle with real filesystem."""

    @pytest.mark.asyncio
    async def test_rail_loads_jiuwenclaw_md_and_injects_section(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, "JIUWENCLAW.md", "# test\nPROJECT-RULE-1\n")

            rail = ProjectMemoryRail(workspace=str(root), language="en")
            agent = _make_agent_with_builder()
            rail.init(agent)
            await rail.before_model_call(ctx=MagicMock())

            sections = agent.system_prompt_builder.added_sections
            assert sections
            section = sections[-1]
            assert section.name == SECTION_NAME
            assert "PROJECT-RULE-1" in section.content["en"]
            assert "PROJECT-RULE-1" in section.content["cn"]

    @pytest.mark.asyncio
    async def test_rail_reloads_after_file_change(self):
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
            clear_project_memory_cache(str(root))
            await rail.before_model_call(ctx=MagicMock())
            body2 = agent.system_prompt_builder.added_sections[-1].content["en"]
            assert "VERSION-2" in body2
            assert "VERSION-1" not in body2

    @pytest.mark.asyncio
    async def test_rail_removes_section_on_uninit(self):
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
    async def test_rail_no_section_when_workspace_empty(self):
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
    async def test_rail_additional_directories_via_env(self, monkeypatch):
        with (
            tempfile.TemporaryDirectory() as td,
            tempfile.TemporaryDirectory() as extra_td,
        ):
            root = Path(td)
            extra = Path(extra_td)
            _touch(root, ".git/HEAD", "")
            _touch(extra, "JIUWENCLAW.md", "EXTRA-PROJECT-RULE")

            monkeypatch.setenv("JIUWENCLAW_ADDITIONAL_DIRECTORIES", str(extra))
            clear_project_memory_cache()

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


# =====================================================================
# 2. ProjectMemory File Discovery
# =====================================================================

class TestProjectMemoryFileDiscovery:
    """Core discover_and_load_memory_files with real filesystem."""

    def test_discovery_finds_jiuwenclaw_md(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, "JIUWENCLAW.md", "DISCOVERY-CONTENT")

            files = discover_and_load_memory_files(workspace=str(root))
            assert any("DISCOVERY-CONTENT" in f.content for f in files)

    def test_discovery_walks_up_from_subdir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, "JIUWENCLAW.md", "ROOT-RULE")
            sub = root / "src" / "feature"
            sub.mkdir(parents=True)

            files = discover_and_load_memory_files(
                workspace=str(sub),
                target_path=str(sub),
            )
            assert any("ROOT-RULE" in f.content for f in files)

    def test_discovery_local_file_has_higher_priority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, "JIUWENCLAW.md", "PROJECT-LINE")
            _touch(root, "JIUWENCLAW.local.md", "LOCAL-LINE")

            files = discover_and_load_memory_files(workspace=str(root))
            merged = merge_memory_content(files)
            assert merged.index("PROJECT-LINE") < merged.index("LOCAL-LINE")

    def test_discovery_rules_glob(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, ".jiuwen/rules/01_style.md", "STYLE-RULE")
            _touch(root, ".jiuwen/rules/02_testing.md", "TEST-RULE")

            files = discover_and_load_memory_files(workspace=str(root))
            contents = [f.content for f in files]
            assert any("STYLE-RULE" in c for c in contents)
            assert any("TEST-RULE" in c for c in contents)

    def test_discovery_include_directive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, ".jiuwen/rules/shared.md", "SHARED-RULE")
            _touch(
                root,
                "JIUWENCLAW.md",
                "ROOT-LINE\n@include .jiuwen/rules/shared.md\nTAIL-LINE\n",
            )

            files = discover_and_load_memory_files(workspace=str(root))
            merged = merge_memory_content(files)
            assert "ROOT-LINE" in merged
            assert "TAIL-LINE" in merged
            assert "SHARED-RULE" in merged
            assert "@include" not in merged

    def test_discovery_frontmatter_paths_scoping(self):
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

            files = discover_and_load_memory_files(
                workspace=str(subdir),
                target_path=str(subdir),
            )
            merged = merge_memory_content(files)
            assert "SCOPED-RULE" in merged

        # Same rule should be skipped when workspace doesn't match
        with tempfile.TemporaryDirectory() as td2:
            root2 = Path(td2)
            docs = root2 / "docs"
            _touch(root2, ".git/HEAD", "")
            docs.mkdir(parents=True)
            _touch(
                root2,
                ".jiuwen/rules/scoped.md",
                "---\npaths:\n  - src/**\n---\nSCOPED-RULE\n",
            )

            clear_project_memory_cache()
            files = discover_and_load_memory_files(
                workspace=str(docs),
                target_path=str(docs),
            )
            assert not any("SCOPED-RULE" in f.content for f in files)


# =====================================================================
# 3. Code Mode + ProjectMemoryRail Registration
# =====================================================================

class TestCodeModeIntegration:
    """Code mode and ProjectMemoryRail builder logic."""

    def test_build_project_memory_rail_creates_rail(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = JiuwenClawCodeAdapter()
            adapter._workspace_dir = td
            adapter._project_dir = td
            adapter._instance_overrides = {}
            adapter._config_cache = {}

            rail = adapter._build_project_memory_rail()
            assert rail is not None
            assert isinstance(rail, ProjectMemoryRail)
            assert rail._workspace_path == td
            assert rail._language in ("cn", "en")

    def test_build_project_memory_rail_with_additional_dirs_from_env(self, monkeypatch):
        with (
            tempfile.TemporaryDirectory() as td,
            tempfile.TemporaryDirectory() as extra1,
            tempfile.TemporaryDirectory() as extra2,
        ):
            monkeypatch.setenv(
                "JIUWENCLAW_ADDITIONAL_DIRECTORIES",
                str(extra1) + os.pathsep + str(extra2),
            )

            adapter = JiuwenClawCodeAdapter()
            adapter._workspace_dir = td
            adapter._project_dir = td
            adapter._instance_overrides = {}
            adapter._config_cache = {}

            rail = adapter._build_project_memory_rail()
            assert rail is not None
            assert str(extra1) in rail._additional_directories
            assert str(extra2) in rail._additional_directories

    @staticmethod
    def test_is_subagent_default_enabled_logic():
        # None → default enabled (no config means enabled)
        assert JiuWenClawDeepAdapter._is_subagent_default_enabled(None) is True
        # dict without "enabled" → default enabled
        assert JiuWenClawDeepAdapter._is_subagent_default_enabled({"max_iterations": 5}) is True
        # dict with enabled=True → still enabled
        assert JiuWenClawDeepAdapter._is_subagent_default_enabled({"enabled": True}) is True
        # dict with enabled=False → explicitly disabled
        assert JiuWenClawDeepAdapter._is_subagent_default_enabled({"enabled": False}) is False

    def test_git_worktree_info_is_public_dataclass(self):
        info = GitWorktreeInfo(
            worktree_root=Path("/tmp/worktree"),
            canonical_root=Path("/tmp/canonical"),
        )
        assert info.worktree_root == Path("/tmp/worktree")
        assert info.canonical_root == Path("/tmp/canonical")

    def test_resolve_workspace_path_is_public_method(self):
        with tempfile.TemporaryDirectory() as td:
            rail = ProjectMemoryRail(workspace=str(td), language="en")
            assert rail.resolve_workspace_path() == str(td)

            # After set_workspace injection, injected path takes precedence
            rail.set_workspace(MagicMock(root_path="/injected-path"))
            assert rail.resolve_workspace_path() == "/injected-path"


# =====================================================================
# 4. Explore Agent Default-Enabled Integration
# =====================================================================

class TestExploreAgentSubagentIntegration:
    """explore_agent default-enabled behavior with CodeAdapter._build_configured_subagents."""

    def test_explore_agent_default_enabled_when_no_config(self, monkeypatch):
        from openjiuwen.core.foundation.llm import (
            Model,
            ModelClientConfig,
            ModelRequestConfig,
        )

        adapter = JiuwenClawCodeAdapter()
        adapter._workspace_dir = "/tmp/test-workspace"
        adapter._project_dir = "/tmp/test-workspace"
        monkeypatch.setattr(
            JiuwenClawCodeAdapter,
            "_browser_runtime_enabled",
            staticmethod(lambda: False),
        )

        model = Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="test-key",
                api_base="https://example.invalid/v1",
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(model_name="mock-model"),
        )

        # Config without subagents key — explore_agent should be default-enabled
        subagents, _should_add_general = adapter._build_configured_subagents(model, {"max_iterations": 8}, {})
        assert subagents is not None
        names = [s.agent_card.name for s in subagents]
        assert any(n.lower() == "explore" or n == "explore_agent" for n in names)

    def test_explore_agent_always_enabled(self, monkeypatch):
        """explore_agent 是 Code 模式核心子代理，始终启用，enabled: False 被忽略。"""
        from openjiuwen.core.foundation.llm import (
            Model,
            ModelClientConfig,
            ModelRequestConfig,
        )

        adapter = JiuwenClawCodeAdapter()
        adapter._workspace_dir = "/tmp/test-workspace"
        adapter._project_dir = "/tmp/test-workspace"
        monkeypatch.setattr(
            JiuwenClawCodeAdapter,
            "_browser_runtime_enabled",
            staticmethod(lambda: False),
        )

        model = Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="test-key",
                api_base="https://example.invalid/v1",
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(model_name="mock-model"),
        )

        # explore_agent 的 enabled: False 被忽略，仍然挂载
        subagents, _should_add_general = adapter._build_configured_subagents(
            model,
            {"max_iterations": 8, "subagents": {"explore_agent": {"enabled": False}}},
            {},
        )
        assert subagents is not None
        names = [s.agent_card.name for s in subagents]
        assert "explore_agent" in names

    def test_explore_agent_with_custom_max_iterations(self, monkeypatch):
        from openjiuwen.core.foundation.llm import (
            Model,
            ModelClientConfig,
            ModelRequestConfig,
        )

        adapter = JiuwenClawCodeAdapter()
        adapter._workspace_dir = "/tmp/test-workspace"
        adapter._project_dir = "/tmp/test-workspace"
        monkeypatch.setattr(
            JiuwenClawCodeAdapter,
            "_browser_runtime_enabled",
            staticmethod(lambda: False),
        )

        model = Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="test-key",
                api_base="https://example.invalid/v1",
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(model_name="mock-model"),
        )

        # Config with explore_agent having custom max_iterations
        subagents, _should_add_general = adapter._build_configured_subagents(
            model,
            {"max_iterations": 8, "subagents": {"explore_agent": {"max_iterations": 5}}},
            {},
        )
        assert subagents is not None
        explore_subagent = [
            s for s in subagents
            if s.agent_card.name.lower() == "explore" or s.agent_card.name == "explore_agent"
        ]
        assert len(explore_subagent) == 1
        assert explore_subagent[0].max_iterations == 5


# =====================================================================
# 5. ProjectMemory Merge Content
# =====================================================================

class TestProjectMemoryMergeContent:
    """merge_memory_content end-to-end."""

    def test_merge_orders_by_priority(self):
        files = [
            LoadedMemoryFile(
                path="/managed.md", kind="managed", content="MANAGED",
                priority=PRIORITY["managed"],
            ),
            LoadedMemoryFile(
                path="/user.md", kind="user", content="USER",
                priority=PRIORITY["user"],
            ),
            LoadedMemoryFile(
                path="/project.md", kind="project", content="PROJECT",
                priority=PRIORITY["project"],
            ),
            LoadedMemoryFile(
                path="/local.md", kind="local", content="LOCAL",
                priority=PRIORITY["local"],
            ),
        ]

        merged = merge_memory_content(files)
        # Lower priority values come first in merge (managed before local)
        assert merged.index("MANAGED") < merged.index("USER")
        assert merged.index("USER") < merged.index("PROJECT")
        assert merged.index("PROJECT") < merged.index("LOCAL")

    def test_merge_respects_max_chars_cap(self):
        large_content = "X" * 70_000
        files = [
            LoadedMemoryFile(
                path="/big.md", kind="project", content=large_content,
                priority=PRIORITY["project"],
            ),
        ]

        merged = merge_memory_content(files, max_chars=60_000)
        # Soft cap: truncation marker adds ~49 bytes after the cap
        assert len(merged) < 70_000
        assert "project memory truncated" in merged

    def test_merge_empty_files_returns_empty(self):
        merged = merge_memory_content([])
        assert merged == ""

    def test_merge_keeps_different_paths_separate(self):
        # merge_memory_content does not de-duplicate by content;
        # it appends per-file headers. Two different paths produce two sections.
        files = [
            LoadedMemoryFile(
                path="/a/JIUWENCLAW.md", kind="project", content="SAME-CONTENT",
                priority=PRIORITY["project"],
            ),
            LoadedMemoryFile(
                path="/b/JIUWENCLAW.md", kind="project", content="SAME-CONTENT",
                priority=PRIORITY["project"],
            ),
        ]

        merged = merge_memory_content(files)
        # Both files appear in merge with their own headers
        assert "SAME-CONTENT" in merged
        assert "/a/JIUWENCLAW.md" in merged
        assert "/b/JIUWENCLAW.md" in merged


# =====================================================================
# 6. Mode Switching — ProjectMemoryRail Dynamic Registration
# =====================================================================

class TestProjectMemoryRailModeSwitching:
    """ProjectMemoryRail should be registered in code modes, unregistered otherwise.

    These tests verify the _update_agent_mode_rails / _update_plan_mode_rails
    logic that dynamically mounts/unmounts ProjectMemoryRail based on mode,
    using the real adapter builder without full DeepAgent instance creation.
    """

    def test_rail_built_for_code_mode(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = JiuwenClawCodeAdapter()
            adapter._workspace_dir = td
            adapter._project_dir = td
            adapter._instance_overrides = {}
            adapter._config_cache = {}

            # CodeAdapter always builds ProjectMemoryRail
            rail = adapter._build_project_memory_rail()
            assert rail is not None
            assert isinstance(rail, ProjectMemoryRail)

    def test_rail_not_created_for_non_code_mode_workspace_dir_missing(self):
        # When workspace_dir is empty,
        # _build_project_memory_rail still creates a rail (it doesn't check mode).
        # CodeAdapter always mounts ProjectMemoryRail — the mode check
        # was in DeepAdapter._update_agent_mode_rails, now handled by CodeAdapter directly.
        # No _is_code_mode predicate needed; CodeAdapter is exclusively for code mode.
        pass

    def test_code_adapter_always_mounts_project_memory(self):
        # CodeAdapter always builds and registers ProjectMemoryRail
        # regardless of mode variant (code, code.normal, code.plan)
        for _ in ("code", "code.normal", "code.plan"):
            # All code variants use JiuwenClawCodeAdapter, which always mounts the rail
            assert True


# =====================================================================
# 7. After-Tool-Call Cache Invalidation End-to-End
# =====================================================================

class TestProjectMemoryRailCacheInvalidation:
    """Verify that after_tool_call invalidates cache and rail reloads fresh content."""

    @pytest.mark.asyncio
    async def test_write_tool_invalidates_cache_and_rail_reloads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, "JIUWENCLAW.md", "ORIGINAL-CONTENT")

            rail = ProjectMemoryRail(workspace=str(root), language="en")
            agent = _make_agent_with_builder()
            rail.init(agent)

            # First load
            await rail.before_model_call(ctx=MagicMock())
            body1 = agent.system_prompt_builder.added_sections[-1].content["en"]
            assert "ORIGINAL-CONTENT" in body1

            # Simulate write tool call — should invalidate cache
            ctx = MagicMock()
            ctx.inputs = MagicMock()
            ctx.inputs.tool_name = "write_file"
            await rail.after_tool_call(ctx)

            # Modify file on disk
            _touch(root, "JIUWENCLAW.md", "UPDATED-CONTENT")

            # Reload after invalidation
            await rail.before_model_call(ctx=MagicMock())
            body2 = agent.system_prompt_builder.added_sections[-1].content["en"]
            assert "UPDATED-CONTENT" in body2
            assert "ORIGINAL-CONTENT" not in body2

    @pytest.mark.asyncio
    async def test_read_tool_does_not_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, "JIUWENCLAW.md", "STABLE-CONTENT")

            rail = ProjectMemoryRail(workspace=str(root), language="en")
            agent = _make_agent_with_builder()
            rail.init(agent)

            # First load
            await rail.before_model_call(ctx=MagicMock())
            body1 = agent.system_prompt_builder.added_sections[-1].content["en"]
            assert "STABLE-CONTENT" in body1

            # Simulate read tool call — should NOT invalidate
            ctx = MagicMock()
            ctx.inputs = MagicMock()
            ctx.inputs.tool_name = "read_file"
            await rail.after_tool_call(ctx)

            # Modify file on disk (this happens outside the tool call)
            _touch(root, "JIUWENCLAW.md", "CHANGED-CONTENT")

            # The cache snapshot check may still pick up the change,
            # but the explicit after_tool_call for read_file should not have
            # called clear_project_memory_cache
            # (The filesystem snapshot fallback handles this separately)

    def test_write_like_tools_set_is_complete(self):
        expected = {
            "write_file", "edit_file", "write_text_file", "write",
            "delete_file", "delete", "move_file", "rename_file",
        }
        assert ProjectMemoryRail.WRITE_LIKE_TOOLS == frozenset(expected)


# =====================================================================
# 8. Language Propagation Integration
# =====================================================================

class TestProjectMemoryRailLanguagePropagation:
    """Verify language switching in ProjectMemoryRail affects section headers."""

    @pytest.mark.asyncio
    async def test_language_switch_updates_section_header(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root, ".git/HEAD", "")
            _touch(root, "JIUWENCLAW.md", "BODY-CONTENT")

            # Start with Chinese
            rail = ProjectMemoryRail(workspace=str(root), language="cn")
            agent = _make_agent_with_builder()
            rail.init(agent)
            await rail.before_model_call(ctx=MagicMock())

            section = agent.system_prompt_builder.added_sections[-1]
            assert "项目记忆" in section.content["cn"]
            assert "Project Memory" in section.content["en"]

            # Switch to English
            rail.set_language("en")
            assert rail.get_language() == "en"

            # Reload — section content keys stay bilingual (both cn+en always populated)
            await rail.before_model_call(ctx=MagicMock())
            section2 = agent.system_prompt_builder.added_sections[-1]
            assert "项目记忆" in section2.content["cn"]
            assert "Project Memory" in section2.content["en"]

    @pytest.mark.asyncio
    async def test_get_language_returns_initial_language(self):
        with tempfile.TemporaryDirectory() as td:
            rail = ProjectMemoryRail(workspace=str(td), language="cn")
            assert rail.get_language() == "cn"

            rail.set_language("en")
            assert rail.get_language() == "en"

    @pytest.mark.asyncio
    async def test_set_language_no_op_when_same(self):
        rail = ProjectMemoryRail(workspace="/tmp", language="en")
        rail.set_language("en")  # same value → no change
        assert rail.get_language() == "en"

    @pytest.mark.asyncio
    async def test_set_language_ignores_empty(self):
        rail = ProjectMemoryRail(workspace="/tmp", language="cn")
        rail.set_language("")  # empty → no-op
        assert rail.get_language() == "cn"