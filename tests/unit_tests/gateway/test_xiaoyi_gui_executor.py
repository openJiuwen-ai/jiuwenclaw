from __future__ import annotations

import asyncio
import time

import pytest

from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_REQUEST_MESSAGE_TYPE,
    GuiRpcRequest,
)
from jiuwenswarm.gateway.gui_rpc import executor as executor_module
from jiuwenswarm.gateway.gui_rpc.executor import (
    GuiExecutionError,
    XiaoyiGuiExecutor,
)


def _request(
    timeout: float = 1.0,
    *,
    rpc_id: str = "gui-rpc-1",
    task_id: str = "xiaoyi-task-1",
) -> GuiRpcRequest:
    return GuiRpcRequest(
        message_type=GUI_RPC_REQUEST_MESSAGE_TYPE,
        rpc_id=rpc_id,
        query="open settings",
        source_request_id="request-1",
        jiuwen_session_id="jiuwen-1",
        xiaoyi_session_id="xiaoyi-session-1",
        xiaoyi_task_id=task_id,
        xiaoyi_message_id=f"message-{task_id}",
        device_id=None,
        deadline=time.time() + timeout,
    )


class FakeChannel:
    def __init__(self, frames: list[dict] | None = None) -> None:
        self.gui_tool_lock = asyncio.Lock()
        self.is_ready = True
        self.handlers: list = []
        self.frames = frames or []
        self.sent: list[dict] = []

    def register_gui_agent_handler(self, handler) -> None:
        self.handlers.append(handler)

    def unregister_gui_agent_handler(self, handler) -> None:
        self.handlers.remove(handler)

    async def send_xiaoyi_phone_tools_command(self, **kwargs) -> bool:
        self.sent.append(kwargs)
        for frame in self.frames:
            for handler in list(self.handlers):
                handler(frame)
        return True


def _frame(*, content: str, final: bool, interaction_id: str = "xiaoyi-task-1") -> dict:
    return {
        "_xiaoyi_session_id": "xiaoyi-session-1",
        "payload": {
            "interactionId": interaction_id,
            "isFinal": final,
            "streamInfo": {"streamContent": content},
        },
    }


@pytest.mark.asyncio
async def test_executor_uses_latest_non_empty_content(monkeypatch) -> None:
    channel = FakeChannel(
        [
            _frame(content="working", final=False),
            _frame(content="", final=True),
        ]
    )
    monkeypatch.setattr(executor_module, "get_xiaoyi_channel", lambda: channel)

    result = await XiaoyiGuiExecutor().execute(_request())

    assert result == "working"
    assert channel.handlers == []
    assert channel.sent[0]["task_id"] == "xiaoyi-task-1"
    command = channel.sent[0]["command"]
    assert command["header"]["name"] == "InvokeJarvisGUIAgentRequest"
    assert command["payload"]["interactionId"] == "xiaoyi-task-1"


@pytest.mark.asyncio
async def test_executor_ignores_wrong_interaction(monkeypatch) -> None:
    channel = FakeChannel(
        [
            _frame(content="wrong", final=True, interaction_id="other-task"),
            _frame(content="right", final=True),
        ]
    )
    monkeypatch.setattr(executor_module, "get_xiaoyi_channel", lambda: channel)

    assert await XiaoyiGuiExecutor().execute(_request()) == "right"
    assert channel.handlers == []


@pytest.mark.asyncio
async def test_executor_rejects_final_response_without_content(monkeypatch) -> None:
    channel = FakeChannel([_frame(content="", final=True)])
    monkeypatch.setattr(executor_module, "get_xiaoyi_channel", lambda: channel)

    with pytest.raises(GuiExecutionError) as exc_info:
        await XiaoyiGuiExecutor().execute(_request())

    assert exc_info.value.error_code == "INVALID_RESPONSE"
    assert channel.handlers == []


@pytest.mark.asyncio
async def test_executor_cancellation_cleans_handler(monkeypatch) -> None:
    channel = FakeChannel()
    monkeypatch.setattr(executor_module, "get_xiaoyi_channel", lambda: channel)
    task = asyncio.create_task(XiaoyiGuiExecutor().execute(_request(timeout=5.0)))
    while not channel.handlers:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(GuiExecutionError) as exc_info:
        await task

    assert exc_info.value.error_code == "CANCELLED"
    assert channel.handlers == []


@pytest.mark.asyncio
async def test_executor_serializes_same_channel_requests(monkeypatch) -> None:
    class SerialChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__()
            self.active_sends = 0
            self.max_active_sends = 0

        async def send_xiaoyi_phone_tools_command(self, **kwargs) -> bool:
            self.active_sends += 1
            self.max_active_sends = max(
                self.max_active_sends,
                self.active_sends,
            )
            await asyncio.sleep(0.01)
            task_id = kwargs["task_id"]
            frame = _frame(
                content=f"done:{task_id}",
                final=True,
                interaction_id=task_id,
            )
            for handler in list(self.handlers):
                handler(frame)
            self.active_sends -= 1
            return True

    channel = SerialChannel()
    monkeypatch.setattr(executor_module, "get_xiaoyi_channel", lambda: channel)

    first, second = await asyncio.gather(
        XiaoyiGuiExecutor().execute(
            _request(rpc_id="gui-rpc-1", task_id="task-1")
        ),
        XiaoyiGuiExecutor().execute(
            _request(rpc_id="gui-rpc-2", task_id="task-2")
        ),
    )

    assert (first, second) == ("done:task-1", "done:task-2")
    assert channel.max_active_sends == 1
    assert channel.handlers == []


@pytest.mark.asyncio
async def test_executor_detects_device_disconnect(monkeypatch) -> None:
    channel = FakeChannel()
    monkeypatch.setattr(executor_module, "get_xiaoyi_channel", lambda: channel)

    async def disconnect() -> None:
        await asyncio.sleep(0.02)
        channel.is_ready = False

    disconnect_task = asyncio.create_task(disconnect())
    with pytest.raises(GuiExecutionError) as exc_info:
        await XiaoyiGuiExecutor().execute(_request(timeout=1.0))
    await disconnect_task

    assert exc_info.value.error_code == "DEVICE_DISCONNECTED"
    assert channel.handlers == []
