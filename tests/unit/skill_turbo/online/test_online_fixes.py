# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""在线执行优化修复（F1–F7/F11）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.skill_turbo.online import (
    context_store,
    flow_scheduler,
    param_validator,
)
from jiuwenclaw.agentserver.skill_turbo.online.skill_turbo_tool import (
    _build_base_accumulator,
    _build_product_summary,
    _is_large_field,
)


@pytest.fixture
def minimal_schema() -> dict[str, Any]:
    return {
        "execution_flow": [
            {"step": 1, "plan_name": "p0_pipeline_init"},
            {
                "step": 3,
                "plan_name": "p3_document_parse",
                "when": "has_documents == true",
                "skip_defaults": {
                    "doc_parse_ok": False,
                    "topic": "",
                    "doc_raw_path": "",
                    "doc_parse_error": "",
                    "topic_inferred": False,
                },
            },
            {"step": 4, "plan_name": "p2_requirement_collect"},
        ],
        "group_children": {
            "p0_pipeline_init": ["p0_1_env_deps", "p0_2_workspace_init"],
            "p2_requirement_collect": ["p2_1_slot_extract"],
        },
        "plan_tasks": [
            {
                "plan_name": "p3_document_parse",
                "inputs": ["has_documents", "doc_paths", "output_dir"],
                "optional_inputs": ["topic", "query"],
                "outputs": ["doc_parse_ok", "topic"],
            },
            {
                "plan_name": "p1_intent_classify",
                "inputs": [],
                "optional_inputs": ["query"],
                "outputs": ["has_documents"],
            },
        ],
    }


class TestAssembleNodeInputsF1:
    @pytest.mark.unit
    def test_group_gets_full_accumulator(self, minimal_schema: dict) -> None:
        acc = {"query": "做PPT", "skill_root": "/skills/pptx-craft", "skill_name": "pptx-craft"}
        node_inputs, missing = param_validator.assemble_node_inputs(
            "p0_pipeline_init", minimal_schema, acc,
        )
        assert missing == []
        assert node_inputs["query"] == "做PPT"
        assert node_inputs["skill_root"] == "/skills/pptx-craft"
        assert node_inputs is not acc  # 副本

    @pytest.mark.unit
    def test_leaf_full_accumulator_reports_missing(self, minimal_schema: dict) -> None:
        acc = {"query": "做PPT", "output_dir": "/tmp/out"}
        node_inputs, missing = param_validator.assemble_node_inputs(
            "p3_document_parse", minimal_schema, acc,
        )
        assert "query" in node_inputs
        assert "output_dir" in node_inputs
        assert set(missing) == {"has_documents", "doc_paths"}

    @pytest.mark.unit
    def test_increment_only_declared_keys_on_leaf(self, minimal_schema: dict) -> None:
        acc = {
            "has_documents": True,
            "doc_paths": ["/a.docx"],
            "output_dir": "/tmp",
            "query": "q",
        }
        node_inputs, missing = param_validator.assemble_node_inputs(
            "p3_document_parse",
            minimal_schema,
            acc,
            increment={"topic": "T", "secret": "nope"},
        )
        assert missing == []
        assert node_inputs["topic"] == "T"
        # secret 未声明：不应写入（叶节点）
        assert "secret" not in node_inputs
        assert node_inputs.get("secret") != "nope"


class TestActivateAccumulatorF2:
    @pytest.mark.unit
    def test_build_base_uses_resolved_skill_root(self) -> None:
        env = SimpleNamespace(
            skill_root="/wrong/parent",
            skill_name="wrong",
            skill_checksum="abc",
            skill_checksum_ok=True,
        )
        acc = _build_base_accumulator(
            query="生成PPT",
            skill_name="pptx-craft",
            skill_root="/relay/office-claw-skills/pptx-craft",
            env=env,
            parent_session=None,
            request_metadata=None,
        )
        assert acc["query"] == "生成PPT"
        assert acc["skill_name"] == "pptx-craft"
        assert acc["skill_root"] == "/relay/office-claw-skills/pptx-craft"
        assert acc["skill_checksum"] == "abc"
        assert acc["skill_checksum_ok"] is True


class TestClearOnlineContextF3:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_clear_skips_post_run_by_default_f1a(self) -> None:
        """F1a：活跃父会话默认 skip_post_run，避免 close_stream。"""
        session = MagicMock()
        session.get_session_id.return_value = "sid-1"
        session.pre_run = AsyncMock()
        session.post_run = AsyncMock()
        await context_store.clear_online_context(session)
        session.update_state.assert_called()
        session.pre_run.assert_awaited()
        session.post_run.assert_not_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_clear_can_post_run_when_explicit(self) -> None:
        session = MagicMock()
        session.get_session_id.return_value = "sid-1"
        session.pre_run = AsyncMock()
        session.post_run = AsyncMock()
        await context_store.clear_online_context(session, skip_post_run=False)
        session.post_run.assert_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_clear_persist_false_skips_post_run(self) -> None:
        session = MagicMock()
        session.get_session_id.return_value = "sid-1"
        session.pre_run = AsyncMock()
        session.post_run = AsyncMock()
        await context_store.clear_online_context(session, persist=False)
        session.update_state.assert_called()
        session.post_run.assert_not_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_save_skips_post_run_by_default(self) -> None:
        session = MagicMock()
        session.get_session_id.return_value = "sid-1"
        session.pre_run = AsyncMock()
        session.post_run = AsyncMock()
        ctx = context_store.TurboContext(
            task_id="t1",
            skill_name="pptx-craft",
            scenario="create_ppt",
            turbo_dir="/tmp/turbo",
            accumulator={},
            completed=set(),
            retry_count={},
            fallback_count=0,
            fallback_nodes=[],
            status="running",
        )
        await context_store.save_online_context(session, ctx)
        session.update_state.assert_called()
        session.post_run.assert_not_awaited()


class TestSkipDefaultsF5:
    @pytest.mark.unit
    def test_when_false_injects_skip_defaults(self, minimal_schema: dict) -> None:
        ctx = context_store.TurboContext(
            task_id="t1",
            skill_name="pptx-craft",
            scenario="create_ppt",
            turbo_dir="/tmp/turbo",
            accumulator={
                "query": "q",
                "has_documents": False,
                # p0 已完成，前沿到 p3
            },
            completed={"p0_pipeline_init"},
            retry_count={},
            fallback_count=0,
            fallback_nodes=[],
            status="running",
        )
        candidates = flow_scheduler.next_candidates(minimal_schema, ctx)
        assert "p3_document_parse" in ctx.completed
        assert ctx.accumulator.get("doc_parse_ok") is False
        assert ctx.accumulator.get("topic") == ""
        assert "p2_requirement_collect" in candidates


class TestProductSummaryF7:
    @pytest.mark.unit
    def test_keeps_outline_path(self) -> None:
        assert not _is_large_field("outline_path")
        products = _build_product_summary({
            "outline_path": "/tmp/outline.md",
            "outline_text": "x" * 500,
            "content_page_count": 3,
            "status": "ok",
        })
        assert products["outline_path"] == "/tmp/outline.md"
        assert products["content_page_count"] == 3
        assert "outline_text" not in products
