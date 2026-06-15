# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ShellOperation pip isolation patch and PipIsolationRail."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.runtime import shell_pip_patch


@pytest.mark.asyncio
async def test_pip_isolation_rail_syncs_ctx_inputs_tool_args():
    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "deep_agent"
        / "rails"
        / "pip_isolation_rail.py"
    )
    spec = importlib.util.spec_from_file_location("pip_isolation_rail_testmod", module_path)
    assert spec and spec.loader
    rail_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rail_module)

    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs

    rail = rail_module.PipIsolationRail()
    tool_call = SimpleNamespace(
        name="bash",
        arguments=json.dumps({"command": "pip install langchain"}),
    )
    inputs = ToolCallInputs(
        tool_call=tool_call,
        tool_name="bash",
        tool_args=tool_call.arguments,
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs)

    runtime_py = Path("C:/venv/Scripts/python.exe")
    with patch.object(
        rail_module,
        "rewrite_shell_command",
        return_value=f'"{runtime_py}" -m pip install langchain',
    ):
        await rail.before_tool_call(ctx)

    assert ctx.inputs.tool_args == tool_call.arguments
    parsed = json.loads(tool_call.arguments)
    assert runtime_py.name in parsed["command"]


@pytest.mark.asyncio
async def test_shell_pip_patch_rewrites_command_and_injects_env():
    captured: dict[str, object] = {}

    class FakeShellOperation:
        _jiuwenclaw_pip_isolation_patched = False

        async def execute_cmd(self, command, *, environment=None, **kwargs):
            captured["command"] = command
            captured["environment"] = environment
            return "ok"

    fake_cls = FakeShellOperation
    fake_cls.execute_cmd_stream = AsyncMock()
    fake_cls.execute_cmd_background = AsyncMock()

    runtime_py = Path("C:/venv/Scripts/python.exe")
    with patch.object(
        shell_pip_patch,
        "rewrite_shell_command",
        return_value=f'"{runtime_py}" -m pip install pkg',
    ), patch.object(
        shell_pip_patch,
        "runtime_subprocess_env",
        return_value={
            "PATH": "C:/venv/Scripts;C:/old",
            "VIRTUAL_ENV": "C:/venv",
            "PYTHONPATH": "C:/venv/Lib/site-packages",
        },
    ), patch.dict(
        "sys.modules",
        {
            "openjiuwen.core.sys_operation.local.shell_operation": SimpleNamespace(
                ShellOperation=fake_cls,
            ),
        },
    ):
        shell_pip_patch.apply_shell_pip_isolation_patch()
        op = fake_cls()
        await op.execute_cmd("pip install pkg", environment={"FOO": "bar"})

    assert runtime_py.name in str(captured["command"])
    env = captured["environment"]
    assert env["FOO"] == "bar"
    assert env["VIRTUAL_ENV"] == "C:/venv"
    assert env["PATH"].startswith("C:/venv/Scripts")
