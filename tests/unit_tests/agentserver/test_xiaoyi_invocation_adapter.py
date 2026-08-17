from __future__ import annotations

from jiuwenswarm.common.invocation_context import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.server.xiaoyi_invocation import (
    build_xiaoyi_device_command_context,
    build_xiaoyi_invocation_extension,
    get_xiaoyi_invocation_extension,
)


def test_xiaoyi_extension_keeps_device_routing_out_of_public_context() -> None:
    request = AgentRequest(
        request_id="request-1",
        channel_id="xiaoyi",
        session_id="jiuwen-1",
        chat_id="root-1",
        params={"session_id": "params-1", "task_id": "task-1"},
        metadata={
            "xiaoyi_root_session_id": "root-override",
            "xiaoyi_params_session_id": "params-override",
            "xiaoyi_task_id": "task-override",
            "xiaoyi_rpc_id": "message-1",
            "xiaoyi_device_id": "device-1",
            "scheduled_device": {"required_intents": ["CreateNote"]},
            "cron": {"job_id": "job-1"},
            "app_id": "app-1",
            "binding_id": "binding-1",
        },
    )
    invocation = InvocationContext(
        version=INVOCATION_CONTEXT_VERSION,
        invocation_id="invocation-1",
        request_id=request.request_id,
        session_id=request.session_id,
        channel_id=request.channel_id,
        chat_id=request.chat_id,
        metadata=build_xiaoyi_invocation_extension(request),
    )

    xiaoyi = get_xiaoyi_invocation_extension(invocation)
    device = build_xiaoyi_device_command_context(invocation)

    assert xiaoyi is not None
    assert xiaoyi.task_id == "task-override"
    assert device.xiaoyi_root_session_id == "root-override"
    assert device.xiaoyi_task_id == "task-override"
    assert device.xiaoyi_rpc_id == "message-1"
    assert device.metadata == {
        "invocation_id": "invocation-1",
        "app_id": "app-1",
        "binding_id": "binding-1",
        "scheduled_device": {"required_intents": ["CreateNote"]},
        "cron": {"job_id": "job-1"},
    }
