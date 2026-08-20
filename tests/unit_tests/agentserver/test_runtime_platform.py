# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import sys

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
