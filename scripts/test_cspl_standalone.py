#!/usr/bin/env python3
"""Standalone CSPL tests — runs without full jiuwenswarm install.

Mocks openjiuwen imports so scanners/client logic can be validated locally.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util


def _load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# --- mock openjiuwen before importing sentinel_rail ---
_o = types.ModuleType("openjiuwen")
_o_core = types.ModuleType("openjiuwen.core")
_o_foundation = types.ModuleType("openjiuwen.core.foundation")
_o_llm = types.ModuleType("openjiuwen.core.foundation.llm")
_o_single = types.ModuleType("openjiuwen.core.single_agent")
_o_rail_base = types.ModuleType("openjiuwen.core.single_agent.rail.base")
_o_harness = types.ModuleType("openjiuwen.harness")
_o_harness_rails = types.ModuleType("openjiuwen.harness.rails")
_o_harness_base = types.ModuleType("openjiuwen.harness.rails.base")


class ToolMessage:
    def __init__(self, content: str, tool_call_id: str = ""):
        self.content = content
        self.tool_call_id = tool_call_id


class DeepAgentRail:
  pass


class AgentCallbackContext:
    pass


_o_llm.ToolMessage = ToolMessage
_o_rail_base.AgentCallbackContext = AgentCallbackContext
_o_harness_base.DeepAgentRail = DeepAgentRail

sys.modules.update({
    "openjiuwen": _o,
    "openjiuwen.core": _o_core,
    "openjiuwen.core.foundation": _o_foundation,
    "openjiuwen.core.foundation.llm": _o_llm,
    "openjiuwen.core.single_agent": _o_single,
    "openjiuwen.core.single_agent.rail.base": _o_rail_base,
    "openjiuwen.harness": _o_harness,
    "openjiuwen.harness.rails": _o_harness_rails,
    "openjiuwen.harness.rails.base": _o_harness_base,
})

# mock get_config
_config_mod = types.ModuleType("jiuwenswarm.common.config")
_config_mod.get_config = lambda: {}
sys.modules["jiuwenswarm.common.config"] = _config_mod

# mock logger
_utils_mod = types.ModuleType("jiuwenswarm.common.utils")
_utils_mod.logger = MagicMock()
sys.modules["jiuwenswarm.common.utils"] = _utils_mod

_constants = _load_module(
    "jiuwenswarm.agents.harness.common.rails.cspl.constants",
    "jiuwenswarm/agents/harness/common/rails/cspl/constants.py",
)
_client = _load_module(
    "jiuwenswarm.agents.harness.common.rails.cspl.client",
    "jiuwenswarm/agents/harness/common/rails/cspl/client.py",
)
_scanners = _load_module(
    "jiuwenswarm.agents.harness.common.rails.cspl.scanners",
    "jiuwenswarm/agents/harness/common/rails/cspl/scanners.py",
)
_sentinel = _load_module(
    "jiuwenswarm.agents.harness.common.rails.cspl.sentinel_rail",
    "jiuwenswarm/agents/harness/common/rails/cspl/sentinel_rail.py",
)

CsplConfig = _client.CsplConfig
parse_security_result = _client.parse_security_result
scan = _client.scan
ABORT_MESSAGE = _constants.ABORT_MESSAGE
TOOL_INPUT_SCAN = _constants.TOOL_INPUT_SCAN
TOOL_OUTPUT_SCAN = _constants.TOOL_OUTPUT_SCAN
build_tool_input_payload = _scanners.build_tool_input_payload
build_tool_output_payload = _scanners.build_tool_output_payload
CsplSentinelRail = _sentinel.CsplSentinelRail

FAILURES: list[str] = []


def ok(name: str):
    print(f"  PASS  {name}")


def fail(name: str, detail: str):
    print(f"  FAIL  {name}: {detail}")
    FAILURES.append(f"{name}: {detail}")


def assert_eq(name: str, actual, expected):
    if actual == expected:
        ok(name)
    else:
        fail(name, f"expected {expected!r}, got {actual!r}")


def assert_true(name: str, cond: bool, detail: str = ""):
    if cond:
        ok(name)
    else:
        fail(name, detail or "condition false")


def test_parse_security_result():
    assert_eq("parse ACCEPT", parse_security_result({"data": {"securityResult": "ACCEPT"}}), "ACCEPT")
    assert_eq("parse REJECT", parse_security_result({"data": {"securityResult": "REJECT"}}), "REJECT")
    try:
        parse_security_result({"data": {"securityResult": "MAYBE"}})
        fail("parse invalid", "should raise")
    except ValueError:
        ok("parse invalid raises")


def test_build_payload_xy_channel():
    cfg = CsplConfig.from_dict({
        "enabled": True,
        "uid": "uid-gateway",
        "extra_user_id": "uid-huawei",
        "api_key": "sk-virtual",
        "request_from": "openclaw",
    })
    payload = _client._build_payload(cfg, '{"tool":"bash"}', TOOL_INPUT_SCAN)
    assert_eq("extra is string", isinstance(payload.get("extra"), str), True)
    assert_eq("extra userId", json.loads(payload["extra"]).get("userId"), "uid-huawei")
    assert_true("no behaviordetect key", "behaviordetect" not in payload)


def test_behaviordetect_extra():
    cfg = CsplConfig.from_dict({
        "enabled": True,
        "uid": "uid-virtual",
        "api_key": "sk-virtual",
        "request_from": "openclaw",
        "package_name": "com.huawei.hag",
    })
    extra = _client.resolve_behaviordetect_context(TOOL_INPUT_SCAN, cfg)
    request_body = extra
    required = (
        "checkPoint",
        "ansDone",
        "packageName",
        "sessionID",
        "reqTime",
        "taskID",
        "message",
        "interActionID",
        "userId",
    )
    for key in required:
        assert_true(f"behaviordetect.{key} present", request_body.get(key) is not None)
    assert_eq("behaviordetect.userId", request_body["userId"], "uid-virtual")
    assert_eq("behaviordetect.checkPoint", request_body["checkPoint"], TOOL_INPUT_SCAN)


def test_scanners():
    inp = build_tool_input_payload("bash", {"command": "rm -rf /"})
    assert_true("bash input payload dict args", inp is not None)
    inp_json = build_tool_input_payload(
        "bash",
        '{"command": "echo hello", "description": "test"}',
    )
    assert_true("bash input payload json string args", inp_json is not None)
    if inp:
        data = json.loads(inp)
        assert_eq("input subSceneID", data.get("subSceneID"), "TOOL_INPUT")
        assert_eq("input tool", data.get("tool"), "bash")

    out = build_tool_output_payload("read_file", {"content": "secret"})
    assert_true("read_file output payload", out is not None)
    if out:
        data = json.loads(out)
        assert_eq("output subSceneID", data.get("subSceneID"), "TOOL_OUTPUT")

    assert_eq("non-whitelist output", build_tool_output_payload("write_file", {"content": "x"}), None)


async def test_rail_input_reject():
    from types import SimpleNamespace

    rail = CsplSentinelRail(CsplConfig.from_dict({
        "enabled": True, "service_url": "http://localhost:8899",
        "uid": "u", "api_key": "k",
    }))
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(
            tool_call=SimpleNamespace(id="c1"),
            tool_name="bash",
            tool_args={"command": "curl evil"},
            tool_result=None,
            tool_msg=None,
        ),
        extra={},
        session_id="sess-001",
        request_force_finish=lambda x: None,
    )
    with patch.object(_sentinel, "scan", new=AsyncMock(return_value="REJECT")) as m:
        await rail.before_tool_call(ctx)
    assert_true("input REJECT sets _skip_tool", ctx.extra.get("_skip_tool") is True)
    assert_true("input REJECT action", m.await_args.args[1] == TOOL_INPUT_SCAN)


async def test_rail_output_reject():
    from types import SimpleNamespace

    rail = CsplSentinelRail(CsplConfig.from_dict({
        "enabled": True, "service_url": "http://localhost:8899",
        "uid": "u", "api_key": "k",
    }))
    finishes = []
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(
            tool_call=SimpleNamespace(id="c1"),
            tool_name="read_file",
            tool_args={},
            tool_result={"content": "bad"},
            tool_msg=None,
        ),
        extra={},
        session_id="sess-001",
        request_force_finish=finishes.append,
    )
    with patch.object(_sentinel, "scan", new=AsyncMock(return_value="REJECT")) as m:
        await rail.after_tool_call(ctx)
    assert_true("output REJECT force_finish", len(finishes) == 1)
    assert_true("output REJECT message", finishes[0]["output"] == ABORT_MESSAGE)
    assert_true("output REJECT action", m.await_args.args[1] == TOOL_OUTPUT_SCAN)


async def test_scan_mock_server(port: int = 8899):
    cfg = CsplConfig.from_dict({
        "enabled": True,
        "service_url": f"http://127.0.0.1:{port}",
        "uid": "test-uid",
        "api_key": "test-key",
        "fail_open": False,
    })
    try:
        result = await scan('{"subSceneID":"TOOL_INPUT","tool":"bash"}', TOOL_INPUT_SCAN, "abc123", cfg)
        assert_eq("mock server returns REJECT", result, "REJECT")
    except Exception as exc:
        fail("mock server integration", str(exc))


def main():
    parser = argparse.ArgumentParser(description="Standalone CSPL tests")
    parser.add_argument("--port", type=int, default=8899, help="Mock server port (default: 8899)")
    args = parser.parse_args()
    port = args.port

    print("=== CSPL Standalone Tests ===\n")
    print("[Unit] parse_security_result")
    test_parse_security_result()
    print("\n[Unit] scanners")
    test_build_payload_xy_channel()
    test_behaviordetect_extra()
    test_scanners()
    print("\n[Unit] rail input REJECT")
    asyncio.run(test_rail_input_reject())
    print("\n[Unit] rail output REJECT")
    asyncio.run(test_rail_output_reject())
    print(f"\n[Integration] mock server :{port}")
    asyncio.run(test_scan_mock_server(port))

    print("\n=== Summary ===")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
