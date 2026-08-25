# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest

import jiuwenclaw.runtime.platform as platform_module
from jiuwenclaw.runtime.platform import is_ohos_runtime, runtime_platform, sandbox_supported


def test_explicit_ohos_platform_is_fail_closed(monkeypatch):
    monkeypatch.setenv("JIUWENCLAW_RUNTIME_PLATFORM", "ohos")

    assert runtime_platform() == "ohos"
    assert is_ohos_runtime()
    assert not sandbox_supported()


def test_linux_remains_supported_without_ohos_marker(monkeypatch):
    monkeypatch.delenv("JIUWENCLAW_RUNTIME_PLATFORM", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform_module.os.path, "isdir", lambda _path: False)

    assert runtime_platform() == "linux"
    assert not is_ohos_runtime()
    assert sandbox_supported()


def test_sandbox_rpc_is_fail_closed_on_ohos(monkeypatch):
    monkeypatch.setenv("JIUWENCLAW_RUNTIME_PLATFORM", "ohos")

    from jiuwenclaw.agentserver.sandbox_config_rpc import dispatch_sandbox_config_request
    from jiuwenclaw.schema.agent import AgentRequest
    from jiuwenclaw.schema.message import ReqMethod

    response = dispatch_sandbox_config_request(
        AgentRequest(
            request_id="ohos-sandbox",
            channel_id="web",
            req_method=ReqMethod.SANDBOX_ENABLED_GET,
        )
    )

    assert not response.ok
    assert response.payload == {
        "error": "JiuwenBox sandbox is not supported on HarmonyOS",
        "code": "UNSUPPORTED_PLATFORM",
    }


@pytest.mark.asyncio
async def test_enabling_external_sandbox_does_not_start_internal_box_server(monkeypatch):
    from jiuwenclaw.agentserver import sandbox_config_rpc
    from jiuwenclaw.agentserver import sandbox_lifecycle

    start_internal = AsyncMock()
    monkeypatch.setattr(sandbox_lifecycle, "start_box_server_internal", start_internal)
    monkeypatch.setattr(
        "jiuwenclaw.config.get_sandbox_runtime", lambda: {"enabled": True}
    )
    monkeypatch.setattr(
        "jiuwenclaw.config.get_sandbox_startup_mode", lambda: "external"
    )

    await sandbox_config_rpc._apply_sandbox_change("enabled")

    start_internal.assert_not_awaited()
