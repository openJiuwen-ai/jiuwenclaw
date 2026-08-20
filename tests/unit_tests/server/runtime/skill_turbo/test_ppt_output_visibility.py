# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Root nodes control stream visibility without business logic in Executor."""

from __future__ import annotations

from typing import Any

import pytest

from jiuwenswarm.server.runtime.skill_turbo.executor import SkillTurboExecutor
from jiuwenswarm.server.runtime.skill_turbo.plan_node import PlanNode
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import (
    PPTGenRootNode,
)


class _DefaultRoot(PlanNode):
    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return inputs


class _ConfiguredRoot(_DefaultRoot):
    suppressed_stream_event_types = frozenset({"chat.delta", "chat.reasoning"})


@pytest.fixture
def configured_root() -> PlanNode:
    return _ConfiguredRoot(plan_name="configured_root", instruction="test")


@pytest.mark.parametrize("event_type", ["chat.delta", "chat.reasoning"])
def test_root_policy_suppresses_configured_stream_events(
    configured_root: PlanNode, event_type: str
) -> None:
    assert SkillTurboExecutor._should_suppress_stream_event(configured_root, event_type)


@pytest.mark.parametrize(
    "event_type",
    [
        "task.start",
        "task.update",
        "task.complete",
        "chat.tool_call",
        "chat.tool_result",
        "artifact.generated",
        "chat.file",
        "chat.ask_user_question",
        "chat.error",
    ],
)
def test_root_policy_preserves_unconfigured_stream_events(
    configured_root: PlanNode, event_type: str
) -> None:
    assert not SkillTurboExecutor._should_suppress_stream_event(
        configured_root, event_type
    )


@pytest.mark.parametrize("event_type", ["chat.delta", "chat.reasoning"])
def test_default_root_policy_keeps_existing_stream_behavior(event_type: str) -> None:
    default_root = _DefaultRoot(plan_name="default_root", instruction="test")

    assert not SkillTurboExecutor._should_suppress_stream_event(
        default_root, event_type
    )


def test_ppt_root_declares_internal_chat_events_hidden() -> None:
    assert PPTGenRootNode.suppressed_stream_event_types == frozenset(
        {"chat.delta", "chat.reasoning"}
    )
