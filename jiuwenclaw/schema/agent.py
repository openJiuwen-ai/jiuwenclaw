# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agent 请求与响应模型."""

from dataclasses import dataclass, field
from typing import Any

from jiuwenclaw.schema.message import ReqMethod


@dataclass
class AgentRequest:
    """Agent 请求（Gateway → AgentServer）."""

    request_id: str
    channel_id: str = ""
    session_id: str | None = None
    req_method: ReqMethod | None = None
    params: dict = field(default_factory=dict)
    is_stream: bool = False
    timestamp: float = 0.0
    metadata: dict[str, Any] | None = None


@dataclass
class AgentResponse:
    """Agent 响应（AgentServer → Gateway，非流式完整响应）."""

    request_id: str
    channel_id: str
    ok: bool = True
    payload: dict | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class AgentResponseChunk:
    """Agent 响应片段（AgentServer → Gateway，流式）."""

    request_id: str
    channel_id: str
    payload: dict | None = None
    is_complete: bool = False
