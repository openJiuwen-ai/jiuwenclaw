"""Tests for request-local command execution ownership."""

import asyncio

import pytest

from jiuwenswarm.agents.harness.common.tools.command_execution_context import (
    CommandExecutionBinding,
    bind_command_execution,
    bind_no_command_execution,
    current_command_execution,
    reset_command_execution,
)


def test_command_binding_requires_an_exact_provider_contract() -> None:
    with pytest.raises(ValueError, match="sys_operation is required"):
        CommandExecutionBinding(None, sandboxed=True)
    with pytest.raises(TypeError, match="sandboxed flag must be bool"):
        CommandExecutionBinding(object(), sandboxed=1)  # type: ignore[arg-type]


def test_no_command_binding_shadows_and_restores_parent() -> None:
    provider = object()
    outer = bind_command_execution(provider, sandboxed=True)
    try:
        assert current_command_execution() == CommandExecutionBinding(provider, True)
        inner = bind_no_command_execution()
        try:
            assert current_command_execution() is None
        finally:
            reset_command_execution(inner)
        assert current_command_execution() == CommandExecutionBinding(provider, True)
    finally:
        reset_command_execution(outer)
    assert current_command_execution() is None


def test_command_binding_is_task_local() -> None:
    async def observe(sandboxed: bool) -> bool:
        token = bind_command_execution(object(), sandboxed=sandboxed)
        try:
            await asyncio.sleep(0)
            binding = current_command_execution()
            assert binding is not None
            return binding.sandboxed
        finally:
            reset_command_execution(token)

    async def run() -> list[bool]:
        return list(await asyncio.gather(observe(True), observe(False)))

    assert asyncio.run(run()) == [True, False]
