# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Creation-time /persist routing and safe failure notices on beta2."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from jiuwenswarm.common.schema.message import Message
from jiuwenswarm.gateway.message_handler.message_handler import (
    ChannelControlState,
    ChannelMode,
    MessageHandler,
)


class _FakeAgentClient:
    @staticmethod
    async def send_request(env: object) -> SimpleNamespace:
        return SimpleNamespace(ok=True, payload={})

    @staticmethod
    async def send_request_stream(env: object):
        if False:  # pragma: no cover
            yield env


class _TestMessageHandler(MessageHandler):
    @classmethod
    def create(cls) -> "_TestMessageHandler":
        # 清掉单例 initialized flag，让 __init__ 跑完整初始化（_get_config_raw 等
        # 实例属性在 set_outbound_pipeline 才赋值，这里直接 stub 上去）。
        setattr(MessageHandler, "_instance", None)
        setattr(cls, "_instance", None)
        if "_singleton_initialized" in MessageHandler.__dict__:
            del MessageHandler._singleton_initialized
        if "_singleton_initialized" in cls.__dict__:
            del cls._singleton_initialized
        handler = cls(_FakeAgentClient())
        handler.published = []
        # _get_config_raw 由 set_outbound_pipeline 赋值；测试不调 pipeline，
        # 这里直接给个返回空配置的 stub 供 _get_channel_default_state 调用。
        handler._get_config_raw = lambda: {}  # type: ignore[assignment]
        return handler

    async def publish_robot_messages(self, msg: object) -> None:
        self.published.append(msg)


def _control_message(query: str, channel_id: str = "feishu") -> Message:
    return Message(
        id="switch-test",
        type="req",
        channel_id=channel_id,
        session_id=None,
        params={"query": query},
        timestamp=0.0,
        ok=True,
        provider=channel_id,
    )


@pytest.mark.asyncio
async def test_persist_creates_locked_session_and_forwards_first_task(monkeypatch) -> None:
    handler = _TestMessageHandler.create()
    state = ChannelControlState(
        session_id="old-session",
        mode=ChannelMode.AGENT,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(handler, "get_or_create_channel_state", lambda _msg: state)
    monkeypatch.setattr(handler._join_exit, "sender_has_joined", lambda _msg: False)

    async def _allocate(_msg, target_state, *, persist_session=False):
        captured["persist_session"] = persist_session
        target_state.session_id = "persist-session"
        return "persist-session"

    async def _cancel_and_notice(params, _msg):
        captured["old_sid"] = params.old_sid
        captured["new_sid"] = params.new_sid

    monkeypatch.setattr(handler, "_allocate_channel_session", _allocate)
    monkeypatch.setattr(handler, "_new_session_cancel_and_notice", _cancel_and_notice)
    handler._gateway_hook_handler = None

    msg = _control_message("/persist 跟进发布\n重点关注回滚方案")
    msg.params["content"] = msg.params["query"]
    processed = await handler._handle_channel_control(msg)
    await asyncio.sleep(0)

    assert processed is False
    assert captured == {
        "persist_session": True,
        "old_sid": "old-session",
        "new_sid": "persist-session",
    }
    assert state.session_id == "persist-session"
    assert msg.session_id == "persist-session"
    assert msg.params["query"] == "跟进发布\n重点关注回滚方案"
    assert msg.params["content"] == msg.params["query"]
    assert msg.metadata["persist_session_first_task"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_error"),
    [
        ("/new_session", "创建新会话失败，请稍后重试"),
        ("/persist 跟进发布", "创建永续会话失败，请稍后重试"),
    ],
)
async def test_session_creation_failure_hides_internal_error(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    command: str,
    expected_error: str,
) -> None:
    handler = _TestMessageHandler.create()
    state = ChannelControlState(
        session_id="old-session",
        mode=ChannelMode.AGENT,
    )
    notices: list[object] = []

    monkeypatch.setattr(handler, "get_or_create_channel_state", lambda _msg: state)
    monkeypatch.setattr(handler._join_exit, "sender_has_joined", lambda _msg: False)

    async def _allocate(*_args, **_kwargs):
        raise RuntimeError("secret-database-path")

    async def _notice(_user_infos, _channel, _session_id, content):
        notices.append(content)

    monkeypatch.setattr(handler, "_allocate_channel_session", _allocate)
    monkeypatch.setattr(handler, "send_channel_notice", _notice)
    target_logger = logging.getLogger(
        "jiuwenswarm.gateway.message_handler.message_handler"
    )
    target_logger.addHandler(caplog.handler)
    caplog.set_level(logging.ERROR, logger=target_logger.name)
    try:
        processed = await handler._handle_channel_control(_control_message(command))
        await asyncio.sleep(0)
    finally:
        target_logger.removeHandler(caplog.handler)

    assert processed is True
    assert state.session_id == "old-session"
    assert notices == [{"error": expected_error}]
    assert "secret-database-path" not in str(notices)
    assert "secret-database-path" in caplog.text
