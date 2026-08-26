# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manager ConfigReceiver runtime_notify：写库后触发 agent-runtime cleanup。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

_RECEIVER_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "jiuwenclaw-ee"
    / "gateway"
    / "extensions"
    / "manager_config_receiver"
    / "routers"
)
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

from runtime_notify import (  # noqa: E402
    _request_agentserver_cleanup,
    trigger_runtime_config_update,
)


@pytest.mark.asyncio
async def test_trigger_runtime_config_update_skips_without_runtime_url(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GATEWAY_RUNTIME_MANAGER_URL", raising=False)
    trigger_runtime_config_update()


@pytest.mark.asyncio
async def test_request_agentserver_cleanup_posts_cleanup_payload(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GATEWAY_RUNTIME_MANAGER_URL", "http://runtime-manager:8091")
    monkeypatch.setenv("NAMESPACE", "prod")
    monkeypatch.setenv(
        "GATEWAY_RUNTIME_AGENTSERVER_LABEL",
        "jiuwenclaw-component=agentserver",
    )
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs) -> None:
            self._kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url: str, *, json: dict | None = None):
            captured["url"] = url
            captured["body"] = json

            class _Resp:
                status_code = 200
                text = ""

                @staticmethod
                def json() -> dict:
                    return {"rawdata": {"cleaned": 2}}

            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    await _request_agentserver_cleanup()

    assert captured["url"] == "http://runtime-manager:8091/api/session/cleanup"
    assert captured["body"]["type"] == "cleanup"
    assert captured["body"]["rawdata"] == {
        "namespace": "prod",
        "label_selector": "jiuwenclaw-component=agentserver",
    }
