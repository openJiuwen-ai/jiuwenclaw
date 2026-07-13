# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tests for workflow-related enum additions in message schema."""

from jiuwenswarm.common.schema.message import EventType, ReqMethod


def test_req_method_command_workflows():
    """Verify COMMAND_WORKFLOWS enum value exists with correct string."""
    assert ReqMethod.COMMAND_WORKFLOWS.value == "command.workflows"


def test_event_type_workflow_updated():
    """Verify WORKFLOW_UPDATED enum value exists with correct string."""
    assert EventType.WORKFLOW_UPDATED.value == "workflow.updated"