from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from jiuwenclaw.channel.base import BaseChannel, RobotMessageRouter
from jiuwenclaw.schema.message import Message, ReqMethod


@dataclass
class AcpChannelConfig:
    enabled: bool = True
    channel_id: str = "acp"
    default_session_id: str = "acp_cli_session"
    metadata: dict[str, Any] = field(default_factory=dict)


class AcpChannel(BaseChannel):
    """ACP CLI 通道（第一阶段：仅本地拦截，不转发到 gateway）。"""

    name = "acp"

    def __init__(self, config: AcpChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: AcpChannelConfig = config
        self._gateway_callback: Callable[[Message], None] | None = None

    @property
    def channel_id(self) -> str:
        return str(self.config.channel_id or self.name).strip() or self.name

    def on_message(self, callback: Callable[[Message], None]) -> None:
        self._gateway_callback = callback

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: Message) -> None:
        # 第一阶段无出站目标：CLI 自己负责输出。
        _ = msg

    def build_message(self, argv: list[str]) -> Message:
        content = " ".join(argv).strip()
        params: dict[str, Any] = {
            "content": content,
            "query": content,
            "argv": list(argv),
            "source": "cli",
        }
        rid = f"acp_{uuid.uuid4().hex[:12]}"
        return Message(
            id=rid,
            type="req",
            channel_id=self.channel_id,
            session_id=self.config.default_session_id,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            metadata={"acp": dict(self.config.metadata or {})},
        )

    def intercept_cli_output(self, argv: list[str]) -> str:
        msg = self.build_message(argv)
        payload = {
            "intercepted": True,
            "channel_id": msg.channel_id,
            "request_id": msg.id,
            "session_id": msg.session_id,
            "argv": argv,
            "note": "acp channel 命令已在 CLI 本地拦截，暂未发送到 gateway。",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
