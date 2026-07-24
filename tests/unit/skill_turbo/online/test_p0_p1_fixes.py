# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""P0/P1 修复回归：skill_name 路径、assemble deepcopy、sys.path、clear 时序。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jiuwenclaw.agentserver.skill_turbo.online import (
    context_store,
    executor_single,
    flow_scheduler,
    param_validator,
)
from jiuwenclaw.agentserver.skill_turbo.online.skill_name_guard import (
    InvalidSkillNameError,
    safe_join_skill_dir,
    validate_skill_name,
)


class TestSkillNameGuard:
    @pytest.mark.unit
    def test_rejects_path_traversal(self) -> None:
        for bad in ("../x", "..", "a/b", "a\\b", "", ".hidden"):
            with pytest.raises(InvalidSkillNameError):
                validate_skill_name(bad)

    @pytest.mark.unit
    def test_accepts_normal_names(self) -> None:
        assert validate_skill_name("pptx-craft") == "pptx-craft"
        assert validate_skill_name("My_Skill.v1") == "My_Skill.v1"

    @pytest.mark.unit
    def test_safe_join_blocks_escape(self, tmp_path: Path) -> None:
        base = tmp_path / "skills"
        base.mkdir()
        (base / "pptx-craft").mkdir()
        assert safe_join_skill_dir(base, "pptx-craft") is not None
        assert safe_join_skill_dir(base, "../pptx-craft") is None
        assert safe_join_skill_dir(base, "..") is None


class TestAssembleDeepCopy:
    @pytest.mark.unit
    def test_nested_mutation_does_not_pollute_accumulator(self) -> None:
        schema = {
            "plan_tasks": [
                {
                    "plan_name": "p1",
                    "inputs": ["doc_paths"],
                    "optional_inputs": [],
                    "outputs": [],
                }
            ],
            "group_children": {},
        }
        acc: dict[str, Any] = {"doc_paths": ["a.md"], "query": "q"}
        node_inputs, missing = param_validator.assemble_node_inputs("p1", schema, acc)
        assert missing == []
        node_inputs["doc_paths"].append("b.md")
        assert acc["doc_paths"] == ["a.md"]


class TestSysPathCleanup:
    @pytest.mark.unit
    def test_turbo_dir_removed_after_context(self, tmp_path: Path) -> None:
        turbo = tmp_path / "turbo"
        turbo.mkdir()
        normalized = str(turbo.resolve())
        assert normalized not in sys.path
        with executor_single._turbo_dir_on_sys_path(normalized):
            assert normalized in sys.path
        assert normalized not in sys.path

    @pytest.mark.unit
    def test_refcount_keeps_path_until_last_exit(self, tmp_path: Path) -> None:
        turbo = tmp_path / "turbo2"
        turbo.mkdir()
        normalized = str(turbo.resolve())
        with executor_single._turbo_dir_on_sys_path(normalized):
            with executor_single._turbo_dir_on_sys_path(normalized):
                assert normalized in sys.path
            assert normalized in sys.path
        assert normalized not in sys.path


class TestAdvanceAndCandidatesAlias:
    @pytest.mark.unit
    def test_alias_matches_advance(self) -> None:
        schema = {
            "execution_flow": [
                {"step": 1, "plan_name": "p0"},
                {
                    "step": 2,
                    "plan_name": "p_skip",
                    "when": "flag == true",
                    "skip_defaults": {"skipped": True},
                },
                {"step": 3, "plan_name": "p_next"},
            ],
            "plan_tasks": [],
            "group_children": {},
        }
        ctx = context_store.TurboContext(
            task_id="t1",
            skill_name="s",
            scenario="c",
            turbo_dir="/tmp",
            accumulator={"flag": False},
            completed={"p0"},
            retry_count={},
            fallback_count=0,
            fallback_nodes=[],
            status="running",
        )
        out = flow_scheduler.advance_and_candidates(schema, ctx)
        assert "p_skip" in ctx.completed
        assert ctx.accumulator.get("skipped") is True
        assert "p_next" in out
        # alias 仍可用
        ctx2 = context_store.TurboContext(
            task_id="t2",
            skill_name="s",
            scenario="c",
            turbo_dir="/tmp",
            accumulator={"flag": False},
            completed={"p0"},
            retry_count={},
            fallback_count=0,
            fallback_nodes=[],
            status="running",
        )
        assert flow_scheduler.next_candidates(schema, ctx2) == out


class TestClearOnlineContextContinuesAfterPreRunFail:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_state_still_called(self) -> None:
        from unittest.mock import MagicMock

        session = SimpleNamespace(
            pre_run=AsyncMock(side_effect=RuntimeError("already closed")),
            post_run=AsyncMock(),
            update_state=MagicMock(),
            get_session_id=lambda: "sid-1",
        )
        await context_store.clear_online_context(session, persist=True, skip_post_run=True)
        session.update_state.assert_called()
        session.post_run.assert_not_awaited()
