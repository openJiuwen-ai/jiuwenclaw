# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HITL resume: skip real execution of completed depth-1 stages."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from jiuwenswarm.server.runtime.skill_turbo.executor import SkillTurboExecutor
from jiuwenswarm.server.runtime.skill_turbo.plan_node import PlanNode
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
    _merge_subplan_result,
)


class _LeafNode(PlanNode):
    def __init__(self, plan_name: str, depth: int = 1) -> None:
        super().__init__(plan_name=plan_name, instruction=plan_name, sub_plans=[], depth=depth)
        self.run_calls = 0
        self.run_stream_calls = 0

    async def _execute(self, inputs: dict[str, Any]) -> Any:
        self.run_calls += 1
        return {"node": self.plan_name, "status": "ok", "ran": True}

    async def _execute_stream(self, inputs: dict[str, Any]):
        self.run_stream_calls += 1
        yield {"node": self.plan_name, "status": "ok", "ran": True}


class TestPlanNodeResumeSkip:
    @pytest.mark.asyncio
    async def test_execute_subplan_skips_run_when_callback_true(self):
        parent = _LeafNode("root", depth=0)
        child = _LeafNode("p0_pipeline_init", depth=1)
        before_n = after_n = 0

        async def before(_sp: PlanNode, _inp: dict[str, Any]) -> None:
            nonlocal before_n
            before_n += 1

        async def after(_sp: PlanNode, _inp: dict[str, Any], result: Any) -> None:
            nonlocal after_n
            after_n += 1
            assert result.get("resume_skip") is True
            assert result.get("skipped") is True

        async def should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return True

        parent.set_runtime_callbacks(
            before_subplan_execute=before,
            after_subplan_execute=after,
            should_skip_subplan_execute=should_skip,
        )

        result = await parent.execute_subplan(child, {})
        assert child.run_calls == 0
        assert before_n == 1 and after_n == 1
        assert result["resume_skip"] is True
        assert result["message"] == "resume skip completed stage"

    @pytest.mark.asyncio
    async def test_execute_subplan_stream_skips_run_stream(self):
        parent = _LeafNode("root", depth=0)
        child = _LeafNode("p1_intent_classify", depth=1)
        after_n = 0

        async def after(_sp: PlanNode, _inp: dict[str, Any], result: Any) -> None:
            nonlocal after_n
            after_n += 1
            assert result.get("resume_skip") is True

        async def should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return True

        parent.set_runtime_callbacks(
            after_subplan_execute=after,
            should_skip_subplan_execute=should_skip,
        )

        chunks = [c async for c in parent.execute_subplan_stream(child, {})]
        assert chunks == []
        assert child.run_stream_calls == 0
        assert after_n == 1

    @pytest.mark.asyncio
    async def test_execute_subplan_runs_when_callback_false(self):
        parent = _LeafNode("root", depth=0)
        child = _LeafNode("p2_requirement_collect", depth=1)

        async def should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return False

        parent.set_runtime_callbacks(should_skip_subplan_execute=should_skip)
        result = await parent.execute_subplan(child, {})
        assert child.run_calls == 1
        assert result.get("ran") is True

    @pytest.mark.asyncio
    async def test_should_skip_subplan_delegates_to_callback(self):
        parent = _LeafNode("root", depth=0)
        child = _LeafNode("p0_pipeline_init", depth=1)

        async def should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return _sp.plan_name == "p0_pipeline_init"

        parent.set_runtime_callbacks(should_skip_subplan_execute=should_skip)
        assert await parent.should_skip_subplan(child, {}) is True
        assert await parent.should_skip_subplan(
            _LeafNode("p2_requirement_collect", depth=1), {}
        ) is False


def _make_executor() -> SkillTurboExecutor:
    env = MagicMock()
    env.config = {}
    env.skill_code_import_prefixes = (
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes",
    )
    return SkillTurboExecutor(environment=env)


class TestExecutorShouldSkipSubplanExecute:
    @pytest.mark.asyncio
    async def test_resume_skips_completed_depth1_only(self):
        ex = _make_executor()
        ex._resume_replay = True
        ex._task_states_holder = {
            "task_p0": {
                "task_id": "task_p0",
                "task_content": "p0_pipeline_init",
                "status": "completed",
            },
            "task_p1": {
                "task_id": "task_p1",
                "task_content": "p1_intent_classify",
                "status": "completed",
            },
            "task_p3": {
                "task_id": "task_p3",
                "task_content": "p3_document_parse",
                "status": "completed",
            },
            "task_p2": {
                "task_id": "task_p2",
                "task_content": "p2_requirement_collect",
                "status": "in_progress",
            },
        }

        p0 = _LeafNode("p0_pipeline_init", depth=1)
        p2 = _LeafNode("p2_requirement_collect", depth=1)
        deep = _LeafNode("p0_child", depth=2)

        assert await ex._should_skip_subplan_execute(p0, {}) is True
        assert await ex._should_skip_subplan_execute(p2, {}) is False
        assert await ex._should_skip_subplan_execute(deep, {}) is False

    @pytest.mark.asyncio
    async def test_non_resume_never_skips(self):
        ex = _make_executor()
        ex._resume_replay = False
        ex._task_states_holder = {
            "task_p0": {
                "task_id": "task_p0",
                "task_content": "p0_pipeline_init",
                "status": "completed",
            },
        }
        p0 = _LeafNode("p0_pipeline_init", depth=1)
        assert await ex._should_skip_subplan_execute(p0, {}) is False


class TestExecutorSuppressSubplanStartBanner:
    @pytest.mark.asyncio
    async def test_resume_suppresses_in_progress_only(self):
        ex = _make_executor()
        ex._resume_replay = True
        ex._task_states_holder = {
            "task_p2": {
                "task_id": "task_p2",
                "task_content": "p2_requirement_collect",
                "status": "in_progress",
            },
            "task_p5": {
                "task_id": "task_p5",
                "task_content": "p3_5_template_context",
                "status": "pending",
            },
        }
        p2 = _LeafNode("p2_requirement_collect", depth=1)
        p5 = _LeafNode("p3_5_template_context", depth=1)

        assert await ex._should_suppress_subplan_start_banner(p2, {}) is True
        assert await ex._should_suppress_subplan_start_banner(p5, {}) is False

    @pytest.mark.asyncio
    async def test_non_resume_never_suppresses(self):
        ex = _make_executor()
        ex._resume_replay = False
        ex._task_states_holder = {
            "task_p2": {
                "task_id": "task_p2",
                "task_content": "p2_requirement_collect",
                "status": "in_progress",
            },
        }
        p2 = _LeafNode("p2_requirement_collect", depth=1)
        assert await ex._should_suppress_subplan_start_banner(p2, {}) is False


class TestMergeSubplanResultIgnoresSkipFields:
    def test_ignores_skipped_and_resume_skip(self):
        inputs: dict[str, Any] = {"query": "keep"}
        _merge_subplan_result(
            inputs,
            {
                "node": "p0_pipeline_init",
                "status": "ok",
                "message": "resume skip completed stage",
                "skipped": True,
                "resume_skip": True,
                "useful": 1,
            },
        )
        assert inputs == {"query": "keep", "useful": 1}
        assert "skipped" not in inputs
        assert "resume_skip" not in inputs


class TestPPTGenRootResumeProgressSilence:
    @pytest.mark.asyncio
    async def test_run_subplan_stream_silent_when_resume_skip(self):
        from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
            PPTGenRootNode,
        )

        root = PPTGenRootNode()

        async def should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return True

        root.set_runtime_callbacks(should_skip_subplan_execute=should_skip)

        messages: list[str] = []
        results: list[dict[str, Any]] = []
        async for chunk in root._run_subplan_stream(
            root._p0, {}, results, index=1, total_steps=14
        ):
            msg = chunk.get("message")
            if isinstance(msg, str) and msg.strip():
                messages.append(msg)

        assert messages == []
        assert len(results) == 1
        assert results[0]["node"] == "p0_pipeline_init"

    @pytest.mark.asyncio
    async def test_skip_p3_subplan_stream_silent_when_resume_skip(self):
        from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
            PPTGenRootNode,
            _P3_SKIP_MESSAGE,
        )

        root = PPTGenRootNode()

        async def should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return _sp.plan_name == "p3_document_parse"

        root.set_runtime_callbacks(should_skip_subplan_execute=should_skip)

        messages: list[str] = []
        results: list[dict[str, Any]] = []
        async for chunk in root._skip_p3_subplan_stream(
            {}, results, index=3, total_steps=14
        ):
            msg = chunk.get("message")
            if isinstance(msg, str) and msg.strip():
                messages.append(msg)

        assert messages == []
        assert _P3_SKIP_MESSAGE not in messages
        assert len(results) == 1
        assert results[0]["node"] == "p3_document_parse"

    @pytest.mark.asyncio
    async def test_execute_stream_omits_root_banner_on_resume(self):
        from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
            PPTGenRootNode,
        )

        root = PPTGenRootNode()

        async def should_skip(sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return sp.plan_name == "p0_pipeline_init"

        root.set_runtime_callbacks(should_skip_subplan_execute=should_skip)

        # p0 静默跳过后，首条可见消息不应是根节点 banner（即便后续 stage 仍会正常广播）。
        collected: list[str] = []
        gen = root._execute_stream({"has_documents": False})
        try:
            async for chunk in gen:
                msg = chunk.get("message")
                if isinstance(msg, str) and msg.strip():
                    collected.append(msg)
                if collected:
                    break
        finally:
            await gen.aclose()

        assert "PPT生成任务流开始执行" not in collected

    @pytest.mark.asyncio
    async def test_run_subplan_stream_omits_start_banner_for_in_progress_resume(self):
        from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
            PPTGenRootNode,
        )

        root = PPTGenRootNode()
        child = _LeafNode("p2_requirement_collect", depth=1)

        async def should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return False

        async def suppress_start(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            return _sp.plan_name == "p2_requirement_collect"

        root.set_runtime_callbacks(
            should_skip_subplan_execute=should_skip,
            should_suppress_subplan_start_banner=suppress_start,
        )

        messages: list[str] = []
        results: list[dict[str, Any]] = []
        async for chunk in root._run_subplan_stream(
            child, {}, results, index=4, total_steps=14
        ):
            msg = chunk.get("message")
            if isinstance(msg, str) and msg.strip():
                messages.append(msg)

        assert not any(msg.startswith("开始执行") for msg in messages)
        assert child.run_stream_calls == 1
        assert any(msg.startswith("完成执行") for msg in messages)

    @pytest.mark.asyncio
    async def test_silent_resume_skip_falls_back_when_skip_disagrees(self):
        from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
            PPTGenRootNode,
        )

        root = PPTGenRootNode()
        child = _LeafNode("p0_pipeline_init", depth=1)
        calls = {"n": 0}

        async def flaky_should_skip(_sp: PlanNode, _inp: dict[str, Any]) -> bool:
            calls["n"] += 1
            return calls["n"] == 1

        root.set_runtime_callbacks(should_skip_subplan_execute=flaky_should_skip)

        results: list[dict[str, Any]] = []
        chunks = [
            c
            async for c in root._run_subplan_stream(
                child, {}, results, index=1, total_steps=14
            )
        ]

        assert calls["n"] == 2
        assert child.run_stream_calls == 1
        assert len(results) == 1
        assert results[0]["result"].get("ran") is True
        assert chunks == [{"node": "p0_pipeline_init", "status": "ok", "ran": True}]
