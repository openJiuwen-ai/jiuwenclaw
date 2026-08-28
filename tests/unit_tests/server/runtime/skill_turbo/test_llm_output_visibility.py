# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo LLM 输出可见性测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.server.runtime.skill_turbo.executor import (
    SkillTurboExecutor,
    _session_var,
)
from jiuwenswarm.server.runtime.skill_turbo.plan_node import (
    LLMOutputVisibility,
    PlanNode,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
    PPTGenRootNode,
)


class _Node(PlanNode):
    def __init__(
        self,
        plan_name: str,
        *,
        sub_plans: list[PlanNode] | None = None,
    ) -> None:
        super().__init__(plan_name, plan_name, sub_plans=sub_plans)

    async def _execute(self, inputs: dict[str, Any]) -> Any:
        return inputs


class _InternalNode(_Node):
    llm_output_visibility: LLMOutputVisibility = "internal"


class _PublicNode(_Node):
    llm_output_visibility: LLMOutputVisibility = "public"


class _InternalProgressNode(_InternalNode):
    async def _execute_stream(self, inputs: dict[str, Any]):
        yield {"content": "stage progress"}


class _ModelClient:
    async def invoke(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        return SimpleNamespace(
            content="model output",
            reasoning_content="model reasoning",
            usage_metadata=None,
        )

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any):
        yield SimpleNamespace(
            content="model output",
            reasoning_content="model reasoning",
            usage_metadata=None,
        )


def _make_executor() -> SkillTurboExecutor:
    env = MagicMock()
    env.config = {}
    env.model_client = _ModelClient()
    env.skill_code_import_prefixes = (
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes",
    )
    executor = SkillTurboExecutor(environment=env)
    executor._rails = []
    return executor


def _written_event_types(write_stream: AsyncMock) -> list[str]:
    return [call.args[0].type for call in write_stream.await_args_list]


class TestPlanNodeLLMOutputVisibility:
    @pytest.mark.asyncio
    async def test_default_visibility_is_public(self):
        child = _Node("child")
        root = _Node("root", sub_plans=[child])
        call_llm = AsyncMock(return_value="result")

        root.set_runtime_callbacks(call_llm=call_llm)
        assert await child.call_llm("prompt") == "result"

        assert call_llm.await_args.kwargs["output_visibility"] == "public"

    @pytest.mark.asyncio
    async def test_internal_visibility_is_inherited_and_can_be_overridden(self):
        inherited_child = _Node("inherited")
        public_child = _PublicNode("public")
        root = _InternalNode("root", sub_plans=[inherited_child, public_child])
        call_llm = AsyncMock(return_value="result")

        root.set_runtime_callbacks(call_llm=call_llm)
        await inherited_child.call_llm("internal prompt")
        await public_child.call_llm("public prompt")

        visibilities = [
            call.kwargs["output_visibility"]
            for call in call_llm.await_args_list
        ]
        assert visibilities == ["internal", "public"]

    def test_ppt_flow_explicitly_marks_its_llm_output_internal(self):
        assert PPTGenRootNode.llm_output_visibility == "internal"

    @pytest.mark.asyncio
    async def test_internal_visibility_does_not_hide_explicit_node_output(self):
        node = _InternalProgressNode("root")
        node.set_runtime_callbacks()

        chunks = [chunk async for chunk in node.run_stream({})]

        assert chunks == [{"content": "stage progress"}]


class TestExecutorLLMOutputVisibility:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("visibility", "expected_event_types"),
        [
            ("public", ["llm_reasoning", "llm_output"]),
            ("internal", []),
        ],
    )
    async def test_call_llm_only_emits_public_model_text(
        self,
        visibility: LLMOutputVisibility,
        expected_event_types: list[str],
    ):
        executor = _make_executor()
        session = SimpleNamespace(write_stream=AsyncMock())
        token = _session_var.set(session)
        try:
            result = await executor.call_llm(
                "prompt",
                output_visibility=visibility,
            )
        finally:
            _session_var.reset(token)

        assert result == "model output"
        assert _written_event_types(session.write_stream) == expected_event_types

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("visibility", "expected_event_types"),
        [
            ("public", ["llm_reasoning", "llm_output"]),
            ("internal", []),
        ],
    )
    async def test_stream_llm_only_emits_public_model_text(
        self,
        visibility: LLMOutputVisibility,
        expected_event_types: list[str],
    ):
        executor = _make_executor()
        session = SimpleNamespace(write_stream=AsyncMock())
        token = _session_var.set(session)
        try:
            chunks = [
                chunk
                async for chunk in executor.stream_llm(
                    "prompt",
                    output_visibility=visibility,
                )
            ]
        finally:
            _session_var.reset(token)

        assert chunks == ["model output"]
        assert _written_event_types(session.write_stream) == expected_event_types
