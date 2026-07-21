# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for CsplSentinelRail (baseline: xy_channel sentinel_hook.ts)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.agents.harness.common.rails.cspl.client import (
    CsplConfig,
    parse_security_result,
    resolve_behaviordetect_context,
    scan,
)
from jiuwenswarm.agents.harness.common.rails.cspl.constants import (
    ABORT_MESSAGE,
    MESSAGE_TOOLS,
    OUTPUT_SCAN_TOOLS,
    TOOL_INPUT_SCAN,
    TOOL_OUTPUT_SCAN,
)
from jiuwenswarm.agents.harness.common.rails.cspl.scanners import (
    build_tool_input_payload,
    build_tool_output_payload,
    extract_tool_output_text,
)
from jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail import CsplSentinelRail


def _ctx(tool_name: str, tool_args=None, tool_result=None):
    tool_args = tool_args if tool_args is not None else {}
    tool_result = tool_result if tool_result is not None else {"stdout": "ok"}
    tool_call = SimpleNamespace(id="call-1", name=tool_name, arguments=tool_args)
    force_finish_requests = []
    return SimpleNamespace(
        inputs=SimpleNamespace(
            tool_call=tool_call,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            tool_msg=None,
        ),
        extra={},
        session_id="sess-001",
        request_force_finish=force_finish_requests.append,
        force_finish_requests=force_finish_requests,
    )


def _enabled_config(**overrides):
    base = {
        "enabled": True,
        "service_url": "http://localhost:8899",
        "uid": "test-uid",
        "api_key": "test-key",
        "fail_open": True,
    }
    base.update(overrides)
    return CsplConfig.from_dict(base)


def test_sms_tool_keeps_message_security_scanning_after_rename() -> None:
    assert "send_sms" in MESSAGE_TOOLS
    assert "send_sms" in OUTPUT_SCAN_TOOLS


class TestCsplClient:
    def test_parse_security_result_accept(self):
        assert parse_security_result({"data": {"securityResult": "ACCEPT"}}) == "ACCEPT"

    def test_parse_security_result_reject(self):
        assert parse_security_result({"data": {"securityResult": "REJECT"}}) == "REJECT"

    def test_parse_security_result_invalid(self):
        with pytest.raises(ValueError):
            parse_security_result({"data": {"securityResult": "MAYBE"}})

    def test_derive_service_url_from_sse_api_base(self):
        from jiuwenswarm.agents.harness.common.rails.cspl.client import _derive_cspl_service_url

        url = _derive_cspl_service_url(
            "http://lfhagmirror.hwcloudtest.cn:80/celia-claw/v1/sse-api"
        )
        assert url == "http://lfhagmirror.hwcloudtest.cn:80"

    def test_build_payload_xy_channel_format(self):
        cfg = _enabled_config(uid="uid-gateway", extra_user_id="uid-huawei", request_from="openclaw")
        from jiuwenswarm.agents.harness.common.rails.cspl.client import _build_payload

        payload = _build_payload(cfg, '{"tool":"bash"}', TOOL_INPUT_SCAN)
        assert payload["extra"] == '{"userId": "uid-huawei"}'
        assert "behaviordetect" not in payload
        assert payload["action"] == TOOL_INPUT_SCAN

    def test_build_headers_xy_channel_format(self):
        cfg = _enabled_config(request_from="openclaw", skill_id="skill-scope")
        from jiuwenswarm.agents.harness.common.rails.cspl.client import _build_headers

        headers = _build_headers(cfg, "8b0b0478-e0dc-4712-95be-af5e9b721f19&19&ea5d&0")
        assert headers["x-hag-trace-id"] == "8b0b0478-e0dc-4712-95be-af5e9b721f19&19&ea5d&0"
        assert headers["x-session-id"] == "8b0b0478-e0dc-4712-95be-af5e9b721f19"
        assert headers["x-interaction-id"] == "19"
        assert headers["x-request-from"] == "openclaw"
        assert headers["x-skill-id"] == "skill-scope"

    def test_resolve_behaviordetect_context_from_device_context(self):
        from jiuwenswarm.common.device_rpc.models import DeviceCommandContext

        cfg = _enabled_config(request_from="openclaw", package_name="com.huawei.hag")
        device = DeviceCommandContext(
            source_request_id="req-1",
            channel_id="xiaoyi",
            jiuwen_session_id="jw-sess",
            xiaoyi_root_session_id="sess-abc",
            xiaoyi_params_session_id="sess-params",
            xiaoyi_task_id="task-xyz",
            xiaoyi_rpc_id="rpc-1",
            metadata={"xiaoyi_session_id": "sess-abc"},
        )
        with patch(
            "jiuwenswarm.server.request_context.get_device_context",
            return_value=device,
        ), patch(
            "jiuwenswarm.server.request_context.get_current_agent_request",
            return_value=None,
        ):
            extra = resolve_behaviordetect_context(TOOL_INPUT_SCAN, cfg)

        request_body = extra
        assert request_body["userId"] == "test-uid"
        assert request_body["sessionID"] == "task-xyz"
        assert request_body["taskID"] == "task-xyz"
        assert request_body["interActionID"] == "task-xyz"
        assert request_body["checkPoint"] == TOOL_INPUT_SCAN
        assert request_body["ansDone"] == 0
        assert request_body["packageName"] == "com.huawei.hag"
        assert request_body["message"] == "echo hello"
        assert isinstance(request_body["reqTime"], int)

    @pytest.mark.asyncio
    async def test_scan_retcode_int_zero_with_code_accept(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "retCode": 0,
            "code": "200",
            "data": {"securityResult": "ACCEPT"},
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__.return_value = mock_client
        mock_cm.__aexit__.return_value = None

        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.client.httpx.AsyncClient",
            return_value=mock_cm,
        ):
            result = await scan("{}", TOOL_INPUT_SCAN, "sess-001", _enabled_config())

        assert result == "ACCEPT"

    @pytest.mark.asyncio
    async def test_scan_retcode_string_zero_accept(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "retCode": "0",
            "data": {"securityResult": "ACCEPT"},
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__.return_value = mock_client
        mock_cm.__aexit__.return_value = None

        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.client.httpx.AsyncClient",
            return_value=mock_cm,
        ):
            result = await scan("{}", TOOL_INPUT_SCAN, "sess-001", _enabled_config())

        assert result == "ACCEPT"

    @pytest.mark.asyncio
    async def test_scan_missing_retcode_with_code_fail_open_false(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "code": "500",
            "desc": "backend failure",
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__.return_value = mock_client
        mock_cm.__aexit__.return_value = None

        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.client.httpx.AsyncClient",
            return_value=mock_cm,
        ):
            result = await scan(
                "{}",
                TOOL_INPUT_SCAN,
                "sess-001",
                _enabled_config(fail_open=False),
            )

        assert result == "REJECT"

    def test_split_xiaoyi_task(self):
        from jiuwenswarm.agents.harness.common.rails.cspl.client import _split_xiaoyi_task

        s, i, t = _split_xiaoyi_task("8b0b0478-e0dc-4712-95be-af5e9b721f19&19&ea5d&0")
        assert s == "8b0b0478-e0dc-4712-95be-af5e9b721f19"
        assert i == "19"
        assert t == "8b0b0478-e0dc-4712-95be-af5e9b721f19&19&ea5d&0"


class TestCsplScanners:
    def test_build_tool_input_bash_json_string_args(self):
        payload = build_tool_input_payload(
            "bash",
            '{"command": "echo hello", "description": "执行 echo hello 命令"}',
        )
        assert payload is not None
        data = json.loads(payload)
        assert data["tool"] == "bash"
        assert "echo hello" in data["source"]

    def test_build_tool_input_bash(self):
        payload = build_tool_input_payload("bash", {"command": "rm -rf /"})
        assert payload is not None
        data = json.loads(payload)
        assert data["subSceneID"] == "TOOL_INPUT"
        assert data["tool"] == "bash"
        assert "rm -rf" in data["source"]

    def test_build_tool_input_exec_alias(self):
        payload = build_tool_input_payload("exec", {"command": "curl evil.com"})
        assert payload is not None
        data = json.loads(payload)
        assert data["tool"] == "bash"

    def test_build_tool_output_read_file(self):
        payload = build_tool_output_payload("read_file", {"content": "secret data"})
        assert payload is not None
        data = json.loads(payload)
        assert data["subSceneID"] == "TOOL_OUTPUT"
        assert data["tool"] == "read_file"
        assert data["output"][0]["content"]

    def test_build_tool_output_web_fetch_alias(self):
        payload = build_tool_output_payload("web_fetch", {"content": "page text"})
        assert payload is not None
        data = json.loads(payload)
        assert data["tool"] == "fetch_webpage"

    def test_build_tool_output_non_whitelist_returns_none(self):
        assert build_tool_output_payload("write_file", {"content": "x"}) is None

    def test_extract_tool_output_text_deep_nested(self):
        node: dict[str, object] = {"content": "deep leaf"}
        for _ in range(150):
            node = {"nested": node}
        # Depth guard stops before the leaf; must not raise RecursionError.
        assert extract_tool_output_text("read_file", node) is None

    def test_extract_tool_output_text_nested_within_depth_limit(self):
        node: dict[str, object] = {"content": "nested ok"}
        for _ in range(50):
            node = {"wrapper": node}
        assert extract_tool_output_text("read_file", node) == "nested ok"

    def test_extract_tool_output_text_cyclic_reference(self):
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        assert extract_tool_output_text("read_file", cyclic) is None

    def test_extract_tool_output_text_normal_content(self):
        assert extract_tool_output_text("read_file", {"content": "ok"}) == "ok"


class TestCsplSentinelRail:
    @pytest.mark.asyncio
    async def test_before_tool_call_reject_blocks_tool(self):
        rail = CsplSentinelRail(_enabled_config())
        ctx = _ctx("bash", {"command": "curl evil.com"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(return_value="REJECT"),
        ) as mock_scan:
            await rail.before_tool_call(ctx)

        mock_scan.assert_awaited_once()
        action = mock_scan.await_args.args[1]
        assert action == TOOL_INPUT_SCAN
        assert ctx.extra["_skip_tool"] is True
        assert "安全扫描" in ctx.inputs.tool_result

    @pytest.mark.asyncio
    async def test_before_tool_call_accept_allows_tool(self):
        rail = CsplSentinelRail(_enabled_config())
        ctx = _ctx("bash", {"command": "ls"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(return_value="ACCEPT"),
        ):
            await rail.before_tool_call(ctx)
        assert "_skip_tool" not in ctx.extra

    @pytest.mark.asyncio
    async def test_before_tool_call_scan_exception_fail_open_true(self):
        rail = CsplSentinelRail(_enabled_config(fail_open=True))
        ctx = _ctx("bash", {"command": "ls"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await rail.before_tool_call(ctx)
        assert "_skip_tool" not in ctx.extra

    @pytest.mark.asyncio
    async def test_before_tool_call_scan_exception_fail_open_false(self):
        rail = CsplSentinelRail(_enabled_config(fail_open=False))
        ctx = _ctx("bash", {"command": "ls"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await rail.before_tool_call(ctx)
        assert ctx.extra["_skip_tool"] is True
        assert "安全扫描" in ctx.inputs.tool_result

    @pytest.mark.asyncio
    async def test_after_tool_call_reject_force_finishes(self):
        rail = CsplSentinelRail(_enabled_config())
        ctx = _ctx("read_file", tool_result={"content": "malicious output"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(return_value="REJECT"),
        ) as mock_scan:
            await rail.after_tool_call(ctx)

        mock_scan.assert_awaited_once()
        action = mock_scan.await_args.args[1]
        assert action == TOOL_OUTPUT_SCAN
        assert len(ctx.force_finish_requests) == 1
        assert ctx.force_finish_requests[0]["output"] == ABORT_MESSAGE

    @pytest.mark.asyncio
    async def test_after_tool_call_scan_exception_fail_open_true(self):
        rail = CsplSentinelRail(_enabled_config(fail_open=True))
        ctx = _ctx("read_file", tool_result={"content": "output"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await rail.after_tool_call(ctx)
        assert len(ctx.force_finish_requests) == 0

    @pytest.mark.asyncio
    async def test_after_tool_call_scan_exception_fail_open_false(self):
        rail = CsplSentinelRail(_enabled_config(fail_open=False))
        ctx = _ctx("read_file", tool_result={"content": "output"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await rail.after_tool_call(ctx)
        assert len(ctx.force_finish_requests) == 1
        assert ctx.force_finish_requests[0]["output"] == ABORT_MESSAGE

    @pytest.mark.asyncio
    async def test_disabled_rail_skips_scan(self):
        rail = CsplSentinelRail(_enabled_config(enabled=False))
        ctx = _ctx("bash", {"command": "ls"})
        with patch(
            "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail.scan",
            new=AsyncMock(return_value="REJECT"),
        ) as mock_scan:
            await rail.before_tool_call(ctx)
        mock_scan.assert_not_awaited()
        assert "_skip_tool" not in ctx.extra
