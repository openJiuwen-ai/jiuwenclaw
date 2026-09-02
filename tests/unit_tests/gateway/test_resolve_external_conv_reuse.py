"""MessageHandler should reuse desktop session for phone conv_* continue."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from collections.abc import AsyncIterator

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.gateway.message_handler.external_conv_session import to_local_conv_id
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler


class _FakeAgentClient:
    sent_requests: list[object] = []

    @staticmethod
    async def send_request(env: object) -> SimpleNamespace:
        _FakeAgentClient.sent_requests.append(env)
        raise AssertionError("session.create must not run when conv maps to desktop")

    @staticmethod
    async def send_request_stream(env: object) -> AsyncIterator[object]:
        if False:  # pragma: no cover
            yield env
        return


class _TestMessageHandler(MessageHandler):
    @classmethod
    def create(cls) -> "_TestMessageHandler":
        setattr(MessageHandler, "_instance", None)
        setattr(cls, "_instance", None)
        _FakeAgentClient.sent_requests = []
        return cls(_FakeAgentClient())


@pytest.mark.asyncio
async def test_resolve_external_reuses_desktop_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = "desktop_1a061727c1f_65690918081e"
    conv = to_local_conv_id(desktop)
    desk_dir = tmp_path / desktop
    desk_dir.mkdir()
    (desk_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": desktop,
                "title": "今天天气真好",
                "last_message_at": 100.0,
                "channel_id": "desktop",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_agent_sessions_dir",
        lambda: tmp_path,
    )

    handler = _TestMessageHandler.create()
    msg = Message(
        id="req-1",
        type="req",
        channel_id="xiaoyi",
        session_id=conv,
        params={"query": "你真的在办公室吗"},
        timestamp=1.0,
        ok=True,
        metadata={"xiaoyi_session_id": conv},
    )
    await handler._resolve_external_channel_session(msg)
    assert msg.session_id == desktop
    assert msg.metadata["external_session_id"] == conv
    assert _FakeAgentClient.sent_requests == []
