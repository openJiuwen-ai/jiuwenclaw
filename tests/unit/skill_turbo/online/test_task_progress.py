# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""S1 schema / normalize + online task_progress 单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jiuwenclaw.agentserver.skill_turbo.online import context_store, task_progress
from jiuwenclaw.agentserver.skill_turbo.online.skill_turbo_tool import (
    normalize_plan_name,
    skill_turbo_tool,
)


@pytest.fixture
def flow_schema() -> dict[str, Any]:
    return {
        "execution_flow": [
            {"step": 1, "plan_name": "p0_pipeline_init"},
            {
                "step": 3,
                "plan_name": "p3_document_parse",
                "when": "has_documents == true",
                "skip_defaults": {"doc_parse_ok": False, "topic": ""},
            },
            {"step": 4, "plan_name": "p2_requirement_collect", "description": "需求收集"},
        ],
        "group_children": {
            "p0_pipeline_init": ["p0_1_env_deps"],
            "p2_requirement_collect": ["p2_1_slot_extract"],
        },
        "plan_tasks": [
            {
                "plan_name": "p2_requirement_collect",
                "title": "Stage 4: 需求收集",
                "inputs": [],
                "outputs": [],
            },
        ],
    }


def _make_ctx(schema_turbo_dir: str = "/tmp/turbo") -> context_store.TurboContext:
    return context_store.TurboContext(
        task_id="t1",
        skill_name="pptx-craft",
        scenario="create_ppt",
        turbo_dir=schema_turbo_dir,
        accumulator={"query": "做PPT", "has_documents": False},
        completed=set(),
        retry_count={},
        fallback_count=0,
        fallback_nodes=[],
        status="running",
    )


class TestPlanNameSchemaS1:
    @pytest.mark.unit
    def test_schema_plan_name_is_string_nullable(self) -> None:
        props = skill_turbo_tool.card.input_params["properties"]["plan_name"]
        assert props.get("type") == "string"
        assert props.get("nullable") is True or props.get("default") is None

    @pytest.mark.unit
    def test_normalize_string_and_dict(self) -> None:
        assert normalize_plan_name(None) is None
        assert normalize_plan_name("p0_pipeline_init") == "p0_pipeline_init"
        assert normalize_plan_name({"p0_pipeline_init": True}) == "p0_pipeline_init"
        assert normalize_plan_name({"node": "p1_intent_classify"}) == "p1_intent_classify"
        assert normalize_plan_name({"a": True, "b": True}) == "a"
        assert normalize_plan_name(["p0_pipeline_init", "p1"]) == "p0_pipeline_init"


class TestTaskProgress:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_init_and_mark_flow(self, flow_schema: dict) -> None:
        ctx = _make_ctx()
        session = SimpleNamespace(write_stream=AsyncMock())

        created = await task_progress.init_progress(ctx, flow_schema, session, request_id="r1")
        assert created is True
        assert len(ctx.task_progress) == 3
        assert all(s["status"] == "pending" for s in ctx.task_progress.values())
        assert ctx.task_progress["p0_pipeline_init"]["task_id"] == "task_0000_p0_pipeline_init"

        # resume 不重建
        assert await task_progress.init_progress(ctx, flow_schema, session) is False

        await task_progress.mark_started(ctx, "p0_pipeline_init", session, request_id="r1")
        assert ctx.task_progress["p0_pipeline_init"]["status"] == "in_progress"

        await task_progress.mark_completed(ctx, "p0_pipeline_init", session, request_id="r1")
        assert ctx.task_progress["p0_pipeline_init"]["status"] == "completed"

        # when-skip sync
        ctx.completed.add("p3_document_parse")
        changed = await task_progress.sync_from_completed(
            ctx, flow_schema, session, request_id="r1",
        )
        assert changed is True
        assert ctx.task_progress["p3_document_parse"]["status"] == "completed"

        # write_stream 至少发过 update
        assert session.write_stream.await_count >= 1
        types = [c.args[0].type for c in session.write_stream.await_args_list]
        assert "task.update" in types
        # payload 含 event_type + completed 统计
        update_payloads = [
            c.args[0].payload
            for c in session.write_stream.await_args_list
            if c.args[0].type == "task.update"
        ]
        assert update_payloads
        assert update_payloads[-1]["event_type"] == "task.update"
        assert update_payloads[-1]["completed_tasks"] >= 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_finalize_and_serialize(self, flow_schema: dict) -> None:
        ctx = _make_ctx()
        session = SimpleNamespace(write_stream=AsyncMock())
        await task_progress.init_progress(ctx, flow_schema, session)
        await task_progress.mark_started(ctx, "p0_pipeline_init", session)
        await task_progress.finalize_progress(ctx, session)
        assert ctx.task_progress["p0_pipeline_init"]["status"] == "failed"
        assert ctx.task_progress["p2_requirement_collect"]["status"] == "failed"

        data = ctx.to_dict()
        assert "task_progress" in data
        restored = context_store.TurboContext.from_dict(data)
        assert restored.task_progress["p0_pipeline_init"]["task_id"] == (
            "task_0000_p0_pipeline_init"
        )

    @pytest.mark.unit
    def test_prepare_resume_resets_in_progress(self, flow_schema: dict) -> None:
        ctx = _make_ctx()
        ctx.task_progress = {
            "p0_pipeline_init": {
                "task_id": "task_0000_p0_pipeline_init",
                "status": "in_progress",
                "started_at": 1.0,
            },
        }
        task_progress.prepare_resume_progress(ctx)
        assert ctx.task_progress["p0_pipeline_init"]["status"] == "pending"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_write_stream_failure_does_not_raise(self, flow_schema: dict) -> None:
        ctx = _make_ctx()
        session = SimpleNamespace(write_stream=AsyncMock(side_effect=RuntimeError("boom")))
        # 不应抛出
        await task_progress.init_progress(ctx, flow_schema, session)
        assert len(ctx.task_progress) == 3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_parent_none_warns_and_tracks(self, flow_schema: dict) -> None:
        from unittest.mock import patch

        ctx = _make_ctx()
        with task_progress.progress_emit_scope(mode="activate") as tracker:
            with patch.object(task_progress.logger, "warning") as warn_mock:
                await task_progress.init_progress(ctx, flow_schema, None, request_id="r1")
            assert tracker.ok_count == 0
            assert tracker.warning_summary
            assert "parent_session is None" in tracker.warning_summary
            assert warn_mock.called
            assert any(
                "parent_session is None" in str(c)
                for c in warn_mock.call_args_list
            )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compensate_only_when_emit_failed(self, flow_schema: dict) -> None:
        ctx = _make_ctx()
        session = SimpleNamespace(write_stream=AsyncMock())
        with task_progress.progress_emit_scope(mode="execute") as tracker:
            await task_progress.init_progress(ctx, flow_schema, session)
            assert tracker.ok_count >= 1
            before = session.write_stream.await_count
            # 已有成功 emit → 不补偿
            await task_progress.compensate_task_update_if_needed(
                ctx, session, had_state_change=True, request_id="r1",
            )
            assert session.write_stream.await_count == before

        # 无 tracker / ok_count=0 场景：强制补偿
        session2 = SimpleNamespace(write_stream=AsyncMock())
        ctx2 = _make_ctx()
        await task_progress.init_progress(ctx2, flow_schema, session2)
        # 清空 tracker 后用新 scope 模拟中途全失败
        with task_progress.progress_emit_scope(mode="execute"):
            # 人为不发任何成功 emit，直接补偿
            await task_progress.compensate_task_update_if_needed(
                ctx2, session2, had_state_change=True, request_id="r1",
            )
            assert session2.write_stream.await_count >= 1

    @pytest.mark.unit
    def test_display_name_from_schema_title(self, flow_schema: dict) -> None:
        name = task_progress.resolve_display_name(
            "p2_requirement_collect", flow_schema, turbo_dir=None,
        )
        assert "需求收集" in name


class TestOnlineSessionBind:
    @pytest.mark.unit
    def test_bind_and_reset_session_var(self) -> None:
        from jiuwenclaw.agentserver.skill_turbo import executor as ex_mod

        session = SimpleNamespace(get_session_id=lambda: "s1")
        tokens = ex_mod.bind_online_parent_session(
            session, request_id="r1", channel_id="c1",
        )
        try:
            assert ex_mod._session_var.get() is session
            assert ex_mod._request_id_var.get() == "r1"
            assert ex_mod._channel_id_var.get() == "c1"
        finally:
            ex_mod.reset_online_parent_session(tokens)
        assert ex_mod._session_var.get() is None


class TestSkillTurboEagerMutex:
    @pytest.mark.unit
    def test_online_mode_replaces_batch_in_eager(self) -> None:
        from jiuwenclaw.agentserver.deep_agent.interface_deep import (
            _apply_skill_turbo_eager_mutex,
        )

        tools = ["tools_search", "invoke_tool", "skill_acceleration_exec", "bash"]
        out = _apply_skill_turbo_eager_mutex(
            tools,
            {"skill_turbo": {"enabled": True, "skill_turbo_execution_mode": "online"}},
        )
        assert "skill_turbo_tool" in out
        assert "skill_acceleration_exec" not in out

    @pytest.mark.unit
    def test_batch_mode_keeps_batch_tool(self) -> None:
        from jiuwenclaw.agentserver.deep_agent.interface_deep import (
            _apply_skill_turbo_eager_mutex,
        )

        tools = ["tools_search", "invoke_tool", "skill_turbo_tool"]
        out = _apply_skill_turbo_eager_mutex(
            tools,
            {"skill_turbo": {"enabled": True, "skill_turbo_execution_mode": "batch"}},
        )
        assert "skill_acceleration_exec" in out
        assert "skill_turbo_tool" not in out

    @pytest.mark.unit
    def test_disabled_strips_both(self) -> None:
        from jiuwenclaw.agentserver.deep_agent.interface_deep import (
            _apply_skill_turbo_eager_mutex,
        )

        tools = ["skill_turbo_tool", "skill_acceleration_exec", "bash"]
        out = _apply_skill_turbo_eager_mutex(
            tools, {"skill_turbo": {"enabled": False}},
        )
        assert "skill_turbo_tool" not in out
        assert "skill_acceleration_exec" not in out
        assert "bash" in out


class TestEgressF1F2F3:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_emitter_closed_not_counted_as_ok(self, flow_schema: dict) -> None:
        """F3：emitter 已关闭时不得报 queued ok。"""
        ctx = _make_ctx()
        session = SimpleNamespace(
            write_stream=AsyncMock(),
            get_session_id=lambda: "s1",
            _inner=SimpleNamespace(
                stream_writer_manager=lambda: SimpleNamespace(
                    stream_emitter=lambda: SimpleNamespace(is_closed=lambda: True),
                ),
            ),
        )
        with task_progress.progress_emit_scope(mode="execute") as tracker:
            await task_progress.init_progress(ctx, flow_schema, session)
            assert tracker.ok_count == 0
            assert tracker.warning_summary
            assert "emitter_closed" in (tracker.warning_summary or "")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_flush_task_update_helper(self, flow_schema: dict) -> None:
        ctx = _make_ctx()
        session = SimpleNamespace(
            write_stream=AsyncMock(),
            get_session_id=lambda: "s1",
            _inner=SimpleNamespace(
                stream_writer_manager=lambda: SimpleNamespace(
                    stream_emitter=lambda: SimpleNamespace(is_closed=lambda: False),
                ),
            ),
        )
        await task_progress.init_progress(ctx, flow_schema, session)
        session.write_stream.reset_mock()
        ok = await task_progress.flush_task_update_to_session(session, ctx, request_id="r1")
        assert ok is True
        assert session.write_stream.await_count == 1
        assert session.write_stream.await_args.args[0].type == "task.update"

    @pytest.mark.unit
    def test_pending_clear_mark_consume(self) -> None:
        assert context_store.consume_pending_clear_online_context(session=None) is False
        context_store.mark_pending_clear_online_context(session=None)
        assert context_store.consume_pending_clear_online_context(session=None) is True
        assert context_store.consume_pending_clear_online_context(session=None) is False

        class _S:
            def get_session_id(self):
                return "sid-a"

        a, b = _S(), _S()
        # 同 sid 的两个对象共享标记
        context_store.mark_pending_clear_online_context(a)
        assert context_store.consume_pending_clear_online_context(b) is True
        context_store.mark_pending_clear_online_context(a)

        class _S2:
            def get_session_id(self):
                return "sid-b"

        other = _S2()
        assert context_store.consume_pending_clear_online_context(other) is False
        assert context_store.consume_pending_clear_online_context(a) is True
