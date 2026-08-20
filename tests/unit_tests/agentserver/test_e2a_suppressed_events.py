# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""E2A_SUPPRESSED_EVENT_TYPES must have a single source of truth in schema.message."""

from jiuwenclaw.schema.message import E2A_SUPPRESSED_EVENT_TYPES, EventType
from jiuwenclaw.agentserver import interface


def test_suppressed_set_contains_tool_calls_delta() -> None:
    assert EventType.CHAT_TOOL_CALLS_DELTA.value in E2A_SUPPRESSED_EVENT_TYPES
    assert "chat.tool_calls.delta" in E2A_SUPPRESSED_EVENT_TYPES


def test_interface_uses_shared_constant() -> None:
    """interface.py keeps only an alias; the frozenset object itself is shared."""
    assert interface._E2A_SUPPRESSED_EVENT_TYPES is E2A_SUPPRESSED_EVENT_TYPES
