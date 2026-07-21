# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from unittest.mock import MagicMock, patch

import pytest

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.extensions.yuanrong_frontend_client import YuanrongFrontendAgentClient


class YuanrongFrontendAgentClientProbe(YuanrongFrontendAgentClient):
    """Subclass exposing protected helpers for unit tests (G.CLS.11)."""

    def invoke_headers(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        req_method: str | None = None,
        stream: bool = False,
    ) -> dict[str, str]:
        return self._invoke_headers(
            session_id,
            user_id=user_id,
            req_method=req_method,
            stream=stream,
        )


@pytest.fixture
def client() -> YuanrongFrontendAgentClientProbe:
    return YuanrongFrontendAgentClientProbe(
        frontend_endpoint="http://127.0.0.1:8080",
        function_version_urn="urn:test:function:1",
        concurrency=2,
    )


def test_invoke_headers_without_user_id(client: YuanrongFrontendAgentClientProbe):
    headers = client.invoke_headers("sess-1")

    assert "X-Session-Context" not in headers
    instance = json.loads(headers["X-Instance-Session"])
    assert instance == {"sessionID": "sess-1", "concurrency": 2}


def test_invoke_headers_with_user_id(client: YuanrongFrontendAgentClientProbe):
    headers = client.invoke_headers("sess-1", user_id="alice")

    assert json.loads(headers["X-Session-Context"]) == {"sessionCtx": "alice"}
    assert json.loads(headers["X-Instance-Session"]) == {"sessionID": "sess-1", "concurrency": 2}


def test_invoke_headers_stream_accepts_sse(client: YuanrongFrontendAgentClientProbe):
    headers = client.invoke_headers("sess-1", user_id="bob", stream=True)

    assert headers["Accept"] == "text/event-stream"
    assert json.loads(headers["X-Session-Context"]) == {"sessionCtx": "bob"}


@pytest.mark.asyncio
async def test_send_request_passes_user_id_in_session_context(client: YuanrongFrontendAgentClientProbe):
    await client.connect("http://127.0.0.1:8080")

    captured: dict[str, str] = {}

    def fake_urlopen(req, timeout=0):
        captured.update(dict(req.header_items()))
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"ok": true}'
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    envelope = E2AEnvelope(
        request_id="req-1",
        channel="tui",
        session_id="sess-1",
        method="chat.send",
        user_id="alice",
    )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        response = await client.send_request(envelope)

    assert response.ok is True
    assert json.loads(captured["X-session-context"]) == {"sessionCtx": "alice"}


@pytest.mark.asyncio
async def test_send_request_omits_session_context_without_user_id(client: YuanrongFrontendAgentClientProbe):
    await client.connect("http://127.0.0.1:8080")

    captured: dict[str, str] = {}

    def fake_urlopen(req, timeout=0):
        captured.update(dict(req.header_items()))
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{}'
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    envelope = E2AEnvelope(
        request_id="req-2",
        channel="tui",
        session_id="sess-2",
        method="chat.send",
    )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await client.send_request(envelope)

    assert "X-session-context" not in {k.lower() for k in captured}


@pytest.mark.asyncio
async def test_create_and_delete_sandbox_placeholders(client: YuanrongFrontendAgentClientProbe):
    await client.connect("http://127.0.0.1:8080")

    sandbox = await client.create_sandbox(
        user_id="alice",
        agent_type="swarm",
        agent_id="agent-1",
        image_name="jiuwenswarm:latest",
    )

    assert sandbox.sandbox_id.startswith("sbx_")
    assert sandbox.user_id == "alice"
    assert sandbox.agent_type == "swarm"
    assert sandbox.status == "ready"
    assert sandbox.metadata["provisioning"] == "yuanrong_create_sandbox_stub"

    await client.delete_sandbox(
        sandbox.sandbox_id,
        user_id="alice",
        agent_type="3rd",
    )


@pytest.mark.asyncio
async def test_create_sandbox_requires_connection(client: YuanrongFrontendAgentClientProbe):
    with pytest.raises(RuntimeError, match="client not connected"):
        await client.create_sandbox(user_id="alice", agent_type="swarm")
