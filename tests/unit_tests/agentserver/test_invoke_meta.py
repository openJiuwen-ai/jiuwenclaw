# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for invoke_meta (mcp/run OA path + local CloudWsRelay)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client import (
    CloudPluginClient,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.external_tool_registry import (
    ExternalToolSpec,
    load_external_tools,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.invoke_tool import InvokeTool
from jiuwenswarm.agents.harness.common.tools.invoke_meta.plugin_skill_catalog import (
    extract_seedance_query_state,
    extract_seedance_task_id,
    normalize_plugin_skill_args,
    want_seedance_wait,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.workspace_context import (
    set_effective_request_workspace_dir,
)


@pytest.fixture(autouse=True)
def _clear_mcp_run_env(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME_MCP_RUN", raising=False)


@pytest.fixture()
def tools_workspace(tmp_path: Path) -> Path:
    tools_dir = tmp_path / "skill" / "references" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "com.demo.plugin__echo_tool.json").write_text(
        json.dumps(
            {
                "pluginId": "com.demo.plugin",
                "toolName": "echo_tool",
                "description": "echo",
                "protocol": "REST",
                "pluginType": "Cloud",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tools_dir / "com.demo.device__device_tool.json").write_text(
        json.dumps(
            {
                "pluginId": "com.demo.device",
                "toolName": "device_tool",
                "pluginType": "Device",
                "parameters": {"type": "object", "properties": {}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_normalize_seedream_pro_size_and_drops_max_images():
    out, err = normalize_plugin_skill_args(
        "SeedreamPro4Skill",
        {"size": "1024x1024", "max_images": 4, "prompt": "logo"},
    )
    assert err is None
    assert out["size"] == "1K"
    assert "max_images" not in out


def test_normalize_seedream_lite_keeps_max_images():
    out, err = normalize_plugin_skill_args(
        "seedreamLite4Skill",
        {"size": "2048x2048", "max_images": "3", "prompt": "cats"},
    )
    assert err is None
    assert out["size"] == "2K"
    assert out["max_images"] == 3


@pytest.mark.asyncio
async def test_invoke_requires_function_name():
    tool = InvokeTool()
    result = await tool.invoke({"arguments": {}})
    assert result["success"] is False
    assert "functionName" in result["error"]


@pytest.mark.asyncio
async def test_invoke_agent_missing_agent_id():
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "agent_as_a_tool",
            "arguments": {"query": "hello"},
        }
    )
    assert result["success"] is False
    assert "agentId" in result["error"]


def test_load_external_tools(tools_workspace: Path):
    registry = load_external_tools(tools_workspace)
    assert ("com.demo.plugin", "echo_tool") in registry
    assert registry[("com.demo.plugin", "echo_tool")].plugin_type == "Cloud"
    assert ("com.demo.device", "device_tool") in registry


@pytest.mark.asyncio
async def test_invoke_device_plugin_rejected(tools_workspace: Path, monkeypatch):
    set_effective_request_workspace_dir(str(tools_workspace))
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "device_tool",
            "arguments": {"bundleName": "com.demo.device"},
        }
    )
    assert result.get("success") is False
    assert "Device" in result.get("error", "")


@pytest.mark.asyncio
async def test_invoke_plugin_routes_to_cloud_client(tools_workspace: Path, monkeypatch):
    set_effective_request_workspace_dir(str(tools_workspace))
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")

    mock_invoke = AsyncMock(
        return_value={
            "success": True,
            "content": "ok",
            "pluginId": "com.demo.plugin",
            "toolName": "echo_tool",
        }
    )

    class _FakeCloudClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        invoke = mock_invoke

    with patch(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client.CloudPluginClient",
        _FakeCloudClient,
    ):
        tool = InvokeTool()
        result = await tool.invoke(
            {
                "functionName": "echo_tool",
                "arguments": {"bundleName": "com.demo.plugin", "text": "hi"},
            }
        )

    assert result.get("success") is True
    assert result.get("content") == "ok"
    mock_invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_rejects_invented_bundle(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "PluginSkillExecTool",
            "arguments": {
                "functionName": "generate",
                "bundleName": "image-generation",
                "prompt": "a dog",
            },
        }
    )
    assert result.get("success") is False
    err = result.get("error", "")
    assert "不支持" in err or "seedreamLite4Skill" in err


@pytest.mark.asyncio
async def test_invoke_rejects_wrong_bundle_for_seedream(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "PluginSkillExecTool",
            "arguments": {
                "functionName": "seedreamLite4Skill",
                "bundleName": "xiaoyi",
                "prompt": "a dog",
            },
        }
    )
    assert result.get("success") is False
    assert "com.atomicservice.5765880207845681341" in result.get("error", "")


@pytest.mark.asyncio
async def test_invoke_rejects_seedream_without_prompt(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "PluginSkillExecTool",
            "arguments": {
                "functionName": "seedreamLite4Skill",
                "bundleName": "com.atomicservice.5765880207845681341",
            },
        }
    )
    assert result.get("success") is False
    assert "prompt" in result.get("error", "")


@pytest.mark.asyncio
async def test_invoke_nested_function_name_without_wrapper(monkeypatch):
    """Skill-doc inner args only: arguments.functionName present, top-level omitted."""
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    captured: dict[str, Any] = {}

    mock_invoke = AsyncMock(
        return_value={"success": True, "content": '{"items":["https://x"]}'}
    )

    class _FakeCloudClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def invoke(self, spec: ExternalToolSpec, arguments: dict, **kwargs: Any):
            captured["spec"] = spec
            captured["arguments"] = arguments
            return await mock_invoke(spec, arguments, **kwargs)

    with patch(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client.CloudPluginClient",
        _FakeCloudClient,
    ):
        tool = InvokeTool()
        result = await tool.invoke(
            {
                "arguments": {
                    "bundleName": "com.atomicservice.5765880207845681341",
                    "functionName": "SeedreamPro4Skill",
                    "max_images": 4,
                    "prompt": "Trendy logo design for Gen Z beauty brand 'LUMI'.",
                    "size": "1024x1024",
                }
            }
        )

    assert result.get("success") is True
    spec = captured["spec"]
    assert isinstance(spec, ExternalToolSpec)
    assert spec.plugin_id == "com.atomicservice.5765880207845681341"
    assert spec.tool_name == "SeedreamPro4Skill"
    assert captured["arguments"]["prompt"].startswith("Trendy logo")
    assert captured["arguments"]["functionName"] == "SeedreamPro4Skill"
    assert captured["arguments"]["size"] == "1K"
    assert "max_images" not in captured["arguments"]


@pytest.mark.asyncio
async def test_invoke_rejects_invalid_seedream_size(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "PluginSkillExecTool",
            "arguments": {
                "functionName": "SeedreamPro4Skill",
                "bundleName": "com.atomicservice.5765880207845681341",
                "prompt": "a dog",
                "size": "4K",
            },
        }
    )
    assert result.get("success") is False
    assert "1K" in result.get("error", "")


@pytest.mark.asyncio
async def test_invoke_lite_rejects_bad_max_images(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "PluginSkillExecTool",
            "arguments": {
                "functionName": "seedreamLite4Skill",
                "bundleName": "com.atomicservice.5765880207845681341",
                "prompt": "a dog",
                "max_images": 99,
            },
        }
    )
    assert result.get("success") is False
    assert "max_images" in result.get("error", "")


@pytest.mark.asyncio
async def test_invoke_plugin_skill_exec_tool_unwraps(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    captured: dict[str, Any] = {}

    mock_invoke = AsyncMock(
        return_value={"success": True, "content": '{"items":["https://x"]}'}
    )

    class _FakeCloudClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def invoke(self, spec: ExternalToolSpec, arguments: dict, **kwargs: Any):
            captured["spec"] = spec
            captured["arguments"] = arguments
            return await mock_invoke(spec, arguments, **kwargs)

    with patch(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client.CloudPluginClient",
        _FakeCloudClient,
    ):
        tool = InvokeTool()
        result = await tool.invoke(
            {
                "functionName": "PluginSkillExecTool",
                "arguments": {
                    "functionName": "seedreamLite4Skill",
                    "bundleName": "com.atomicservice.5765880207845681341",
                    "prompt": "a dog",
                },
            }
        )

    assert result.get("success") is True
    spec = captured["spec"]
    assert isinstance(spec, ExternalToolSpec)
    assert spec.plugin_id == "com.atomicservice.5765880207845681341"
    assert spec.tool_name == "seedreamLite4Skill"


@pytest.mark.asyncio
async def test_invoke_agent_missing_runtime_baseurl(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME_BASEURL", raising=False)
    tool = InvokeTool()
    result = await tool.invoke(
        {
            "functionName": "agent_as_a_tool",
            "arguments": {"agentId": "demo", "query": "hello"},
        }
    )
    assert result.get("success") is False
    assert "AGENT_RUNTIME_BASEURL" in result.get("error", "")


@pytest.mark.asyncio
async def test_invoke_agent_routes_to_runtime(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_BASEURL", "https://useraccess.example")

    async def _fake_run(inputs: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        assert inputs["agentId"] == "demo"
        return {"result": "agent-ok", "success": True}

    with patch(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.invoke_tool.invoke_remote_agent",
        _fake_run,
    ):
        tool = InvokeTool()
        result = await tool.invoke(
            {
                "functionName": "agent_as_a_tool",
                "arguments": {"agentId": "demo", "query": "hello"},
            }
        )

    assert result.get("success") is True
    assert result.get("result") == "agent-ok"


def test_build_request_body_aligns_skills_request(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_UID", "uid-1")
    monkeypatch.setenv("AGENT_RUNTIME_DEVICE_ID", "dev-1")
    monkeypatch.setenv("CLAW_DEVICE_HOSTNAME", "DESKTOP-PC")
    monkeypatch.setenv("CLAW_DEVICE_SANDBOX_SYSTEM", "windows")
    spec = ExternalToolSpec(
        plugin_id="com.atomicservice.5765880207845681341",
        tool_name="seedreamLite4Skill",
        description="",
        protocol="WS",
        plugin_type="Cloud",
    )
    body = CloudPluginClient._build_request_body(
        spec,
        {
            "bundleName": "com.atomicservice.5765880207845681341",
            "functionName": "seedreamLite4Skill",
            "prompt": "一只柯基",
        },
        context=None,
        session_id="sess-1",
    )
    assert body["bundleName"] == "com.atomicservice.5765880207845681341"
    assert body["functionName"] == "seedreamLite4Skill"
    assert body["skillName"] == ""
    assert body["turnContinue"] is False
    assert body["progressToken"] == ""
    assert body["arguments"]["prompt"] == "一只柯基"
    assert body["arguments"]["bundleName"] == body["bundleName"]
    assert "extraInfo" in body
    assert body["extraInfo"]["session"]["sessionId"] == "sess-1"
    assert body["extraInfo"]["context"]["userInfo"]["uid"] == "uid-1"
    assert body["extraInfo"]["context"]["deviceInfo"]["x-device-id"] == "dev-1"
    assert body["extraInfo"]["context"]["deviceInfo"]["deviceName"] == "DESKTOP-PC"
    assert body["extraInfo"]["context"]["deviceInfo"]["x-device-type"] == "windows"
    assert body["extraInfo"]["session"]["deviceId"] == "dev-1"


def test_is_final_frame_stream_type_final():
    frame = {
        "event": "text",
        "content": json.dumps(
            {
                "items": ["https://example.com/a.jpg"],
                "streamInfo": {"streamType": "final", "textType": "plainText"},
            }
        ),
    }
    assert CloudPluginClient._is_final_frame(frame) is True
    assert CloudPluginClient._is_final_frame({"event": "finish"}) is True
    assert CloudPluginClient._is_final_frame({"event": "text", "content": "{}"}) is False


def test_build_local_relay_headers_prefer_env(monkeypatch):
    monkeypatch.setenv("CLAW_XIAOYI_AK", "ak-env")
    monkeypatch.setenv("CLAW_XIAOYI_SK", "sk-env")
    monkeypatch.setenv("CLAW_XIAOYI_AGENT_ID", "ag-env")
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime._xiaoyi_channel",
        lambda: {},
    )
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        build_local_relay_headers,
    )

    headers = build_local_relay_headers()
    assert headers["x-relay-role"] == "plugin"
    assert headers["x-access-key"] == "ak-env"
    assert headers["x-agent-id"] == "ag-env"
    assert "x-sign" in headers
    assert "x-ts" in headers


def test_build_local_relay_headers_include_role(monkeypatch):
    monkeypatch.delenv("CLAW_XIAOYI_AK", raising=False)
    monkeypatch.delenv("CLAW_XIAOYI_SK", raising=False)
    monkeypatch.delenv("CLAW_XIAOYI_AGENT_ID", raising=False)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime._xiaoyi_channel",
        lambda: {"ak": "ak1", "sk": "sk1", "agent_id": "ag1"},
    )
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        build_local_relay_headers,
    )

    headers = build_local_relay_headers()
    assert headers["x-relay-role"] == "plugin"
    assert headers["x-access-key"] == "ak1"
    assert headers["x-agent-id"] == "ag1"
    assert "x-sign" in headers
    assert "x-ts" in headers


def test_mcp_run_url_preferred_over_relay(monkeypatch):
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        is_mcp_run_url,
        resolve_plugin_runtime_url,
    )

    mcp = "wss://lfhagmirror.hwcloudtest.cn:18449/agent-runtime-service-ws/v1/mcp/run"
    monkeypatch.setenv("AGENT_RUNTIME_MCP_RUN", mcp)
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://127.0.0.1:19690")
    resolved = resolve_plugin_runtime_url()
    assert resolved == mcp
    assert is_mcp_run_url(resolved)
    assert "/agent-runtime-service/v1/mcp/run" not in resolved
    assert "/agent-runtime-service-ws/v1/mcp/run" in resolved


def test_relay_url_when_mcp_run_unset(monkeypatch):
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        is_mcp_run_url,
        resolve_plugin_runtime_url,
    )

    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://127.0.0.1:19690")
    resolved = resolve_plugin_runtime_url()
    assert resolved == "ws://127.0.0.1:19690"
    assert not is_mcp_run_url(resolved)


def test_mcp_run_oa_headers(monkeypatch):
    monkeypatch.setenv(
        "AGENT_RUNTIME_MCP_RUN",
        "wss://host:18449/agent-runtime-service-ws/v1/mcp/run",
    )
    monkeypatch.setenv("AGENT_RUNTIME_UID", "30086000686785686")
    monkeypatch.setenv("OA_API_KEY", "test-key")
    monkeypatch.setenv("OA_REQUEST_FROM", "jiuwenclaw")
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        build_runtime_headers,
    )

    headers = build_runtime_headers(extra={"x-plugin-session-id": "pluginabc"})
    assert headers["x-api-key"] == "test-key"
    assert headers["x-uid"] == "30086000686785686"
    assert headers["x-plugin-session-id"] == "pluginabc"
    assert headers["x-request-from"] == "jiuwenclaw"
    assert "x-relay-role" not in headers
    assert "x-access-key" not in headers


def test_relay_headers_still_local_auth(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://127.0.0.1:19690")
    monkeypatch.setenv("CLAW_XIAOYI_AK", "ak-env")
    monkeypatch.setenv("CLAW_XIAOYI_SK", "sk-env")
    monkeypatch.setenv("CLAW_XIAOYI_AGENT_ID", "ag-env")
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime._xiaoyi_channel",
        lambda: {},
    )
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        build_runtime_headers,
    )

    headers = build_runtime_headers(url="ws://127.0.0.1:19690")
    assert headers["x-relay-role"] == "plugin"
    assert headers["x-access-key"] == "ak-env"
    assert "x-api-key" not in headers


def test_mcp_run_extra_info_uses_request_txt_device(monkeypatch):
    monkeypatch.setenv(
        "AGENT_RUNTIME_MCP_RUN",
        "wss://host:18449/agent-runtime-service-ws/v1/mcp/run",
    )
    monkeypatch.setenv("AGENT_RUNTIME_UID", "30086000686785686")
    monkeypatch.delenv("AGENT_RUNTIME_DEVICE_ID", raising=False)
    monkeypatch.delenv("X_DEVICE_ID", raising=False)
    monkeypatch.delenv("CLAW_DEVICE_HOSTNAME", raising=False)
    monkeypatch.delenv("CLAW_DEVICE_SANDBOX_SYSTEM", raising=False)
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        build_plugin_skill_extra_info,
    )

    extra = build_plugin_skill_extra_info(session_id="sess-mcp")
    device = extra["context"]["deviceInfo"]
    assert extra["context"]["userInfo"]["uid"] == "30086000686785686"
    assert extra["session"]["sessionId"] == "sess-mcp"
    assert device["deviceName"] == "HAD-W32"
    assert device["ohosApiVersion"] == 26
    assert device["x-device-type"] == "2in1"
    assert device["sysVersion"].startswith("OpenHarmony")


def test_needs_insecure_ssl_for_test_host_and_ip():
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client import (
        _needs_insecure_ssl,
    )

    assert _needs_insecure_ssl(
        "wss://lfhagmirror.hwcloudtest.cn:18449/agent-runtime-service-ws/v1/mcp/run"
    )
    assert _needs_insecure_ssl("wss://10.33.87.20:18449/agent-runtime-service-ws/v1/mcp/run")
    assert not _needs_insecure_ssl("wss://example.com/v1/mcp/run")
    assert not _needs_insecure_ssl("ws://127.0.0.1:19690")


def test_extract_seedance_task_id_from_json_content():
    assert extract_seedance_task_id({"content": '{"task_id":"cgt-1"}'}) == "cgt-1"
    assert extract_seedance_task_id({"content": {"id": "cgt-2"}}) == "cgt-2"


def test_extract_seedance_query_state():
    status, url = extract_seedance_query_state(
        {
            "content": json.dumps(
                {"status": "succeeded", "content": {"video_url": "https://cdn.example/a.mp4"}}
            )
        }
    )
    assert status == "succeeded"
    assert url == "https://cdn.example/a.mp4"


def test_want_seedance_wait_defaults_true():
    assert want_seedance_wait({}) is True
    assert want_seedance_wait({"wait": False}) is False
    assert want_seedance_wait({"wait": "false"}) is False


def _seedance_task_args(**extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "functionName": "seedanceMiniTask",
        "bundleName": "com.atomicservice.5765880207845681341",
        "content": [{"type": "text", "text": "一只在月光下奔跑的狐狸"}],
        "duration": 10,
    }
    args.update(extra)
    return args


@pytest.mark.asyncio
async def test_invoke_seedance_auto_polls_until_video_url(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    monkeypatch.setenv("SEEDANCE_POLL_INTERVAL", "0")
    monkeypatch.setenv("SEEDANCE_POLL_TIMEOUT", "30")
    names: list[str] = []

    class _FakeCloudClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def invoke(self, spec: ExternalToolSpec, arguments: dict, **kwargs: Any):
            names.append(spec.tool_name)
            if spec.tool_name == "seedanceMiniTask":
                return {"success": True, "content": json.dumps({"task_id": "cgt-1"})}
            query_count = names.count("seedanceMiniTaskQuery")
            if query_count == 1:
                return {"success": True, "content": json.dumps({"status": "running"})}
            return {
                "success": True,
                "content": json.dumps(
                    {
                        "status": "succeeded",
                        "content": {"video_url": "https://cdn.example/a.mp4"},
                    }
                ),
            }

    with patch(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client.CloudPluginClient",
        _FakeCloudClient,
    ):
        tool = InvokeTool()
        result = await tool.invoke(
            {"functionName": "PluginSkillExecTool", "arguments": _seedance_task_args()}
        )

    assert result.get("success") is True
    assert result.get("task_id") == "cgt-1"
    assert result.get("video_url") == "https://cdn.example/a.mp4"
    assert names[0] == "seedanceMiniTask"
    assert names.count("seedanceMiniTaskQuery") >= 2


@pytest.mark.asyncio
async def test_invoke_seedance_wait_false_skips_poll(monkeypatch):
    monkeypatch.setenv("XIAOYI_RELAY_WS_URL", "ws://example.test/relay")
    names: list[str] = []

    class _FakeCloudClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def invoke(self, spec: ExternalToolSpec, arguments: dict, **kwargs: Any):
            names.append(spec.tool_name)
            assert "wait" not in arguments
            return {"success": True, "content": json.dumps({"task_id": "cgt-9"})}

    with patch(
        "jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client.CloudPluginClient",
        _FakeCloudClient,
    ):
        tool = InvokeTool()
        result = await tool.invoke(
            {
                "functionName": "PluginSkillExecTool",
                "arguments": _seedance_task_args(wait=False),
            }
        )

    assert result.get("success") is True
    assert names == ["seedanceMiniTask"]
    assert "cgt-9" in str(result.get("content", ""))


def test_design_system_prompt_includes_video_workflow():
    from jiuwenswarm.agents.harness.design.prompt.design_prompt_builder import (
        build_design_system_prompt,
    )

    prompt = build_design_system_prompt()
    assert "seedance-video-gen" in prompt
    assert "分镜" in prompt
    assert "invoke" in prompt.lower() or "`invoke`" in prompt


def test_saas_video_case_prompt_asks_for_finished_clip():
    constants = (
        Path(__file__).resolve().parents[4]
        / "claw_desktop"
        / "src"
        / "renderer"
        / "src"
        / "pages"
        / "home"
        / "design"
        / "constants.ts"
    )
    if not constants.is_file():
        pytest.skip("claw_desktop constants.ts not in workspace")
    text = constants.read_text(encoding="utf-8")
    saas = '为某 SaaS 产品生成 10 秒产品演示视频'
    assert saas in text
    assert "必须调用视频生成能力产出成片" in text
    assert "60 秒短视频分镜脚本" not in text
    assert "要求交付：分镜表" not in text

