"""Tests for the execution-provider policy boundary."""

from jiuwenswarm.agents.harness.common.rails.permissions.execution_provider_contract import (
    ACP_IDE_EXECUTION_TOOLS,
    JIUWENBOX_SANDBOX_EXECUTION_TOOLS,
    UNKNOWN_PROVIDER_EXECUTION_TOOLS,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_capabilities import (
    _CODE_TOOLS,
    _SHELL_TOOLS,
)


def test_execution_provider_partition_is_closed_and_exhaustive() -> None:
    partitions = (
        JIUWENBOX_SANDBOX_EXECUTION_TOOLS,
        ACP_IDE_EXECUTION_TOOLS,
        UNKNOWN_PROVIDER_EXECUTION_TOOLS,
    )
    assert all(
        left.isdisjoint(right)
        for left in partitions
        for right in partitions
        if left is not right
    )
    assert set().union(*partitions) == _SHELL_TOOLS | _CODE_TOOLS
