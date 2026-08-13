"""Embedded PCS Host lifecycle contracts for AgentWebSocketServer."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jiuwenswarm.server import agent_ws_server as server_module
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


class _FakePCSHost:
    instances: list["_FakePCSHost"] = []

    def __init__(self, *, home: str | Path) -> None:
        self.home = Path(home)
        self.events: list[tuple[str, object]] = []
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.events.append(("start", None))

    async def stop(self, *, timeout_seconds: float = 30.0) -> None:
        self.events.append(("stop", timeout_seconds))


class _FakeWebSocketServer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    def close(self) -> None:
        self.events.append("ws-close")
        self.closed = True

    async def wait_closed(self) -> None:
        self.events.append("ws-wait-closed")


class _FailingWebSocketServer:
    def close(self) -> None:
        raise RuntimeError("close failed")

    async def wait_closed(self) -> None:
        raise AssertionError("wait_closed should not run after close failure")


@pytest.fixture(autouse=True)
def _reset_fake_host() -> None:
    _FakePCSHost.instances.clear()


def _server(monkeypatch: pytest.MonkeyPatch) -> AgentWebSocketServer:
    monkeypatch.setattr(server_module, "PCSHostAPI", _FakePCSHost)
    return AgentWebSocketServer(host="127.0.0.1", port=0)


@pytest.mark.asyncio
async def test_agentserver_constructs_one_host_at_fixed_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("C:/users/tester")))
    server = _server(monkeypatch)

    assert len(_FakePCSHost.instances) == 1
    assert _FakePCSHost.instances[0].home == Path("C:/users/tester/.jiuwenswarm/.pcs")
    assert server._pcs_host is _FakePCSHost.instances[0]  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_start_opens_websocket_without_waiting_for_pcs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)
    host = _FakePCSHost.instances[0]
    pcs_started = asyncio.Event()
    release_pcs = asyncio.Event()
    events: list[str] = []

    async def _delayed_start() -> None:
        host.events.append(("start", None))
        pcs_started.set()
        await release_pcs.wait()

    async def _serve(*_args: object, **_kwargs: object) -> _FakeWebSocketServer:
        events.append("ws-serve")
        return _FakeWebSocketServer(events)

    host.start = _delayed_start  # type: ignore[method-assign]
    monkeypatch.setattr(server_module, "reset_harness_packages_state", lambda: None)
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep

    async def _checkpointer() -> None:
        return None

    monkeypatch.setattr(interface_deep, "ensure_persistent_checkpointer", _checkpointer)
    monkeypatch.setattr("websockets.legacy.server.serve", _serve)
    monkeypatch.setattr(server, "_bootstrap_internal_jiuwenbox", _noop_async)

    await server.start()
    await pcs_started.wait()

    assert events == ["ws-serve"]
    assert server._pcs_start_task is not None  # pylint: disable=protected-access
    release_pcs.set()
    await server._pcs_start_task  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_stop_calls_host_even_when_websocket_never_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)

    await server.stop()

    assert _FakePCSHost.instances[0].events == [("stop", 30.0)]


@pytest.mark.asyncio
async def test_pcs_start_failure_does_not_fail_agentserver_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)
    host = _FakePCSHost.instances[0]
    events: list[str] = []

    async def _failed_start() -> None:
        host.events.append(("start", None))
        raise RuntimeError("bad pcs config")

    async def _serve(*_args: object, **_kwargs: object) -> _FakeWebSocketServer:
        events.append("ws-serve")
        return _FakeWebSocketServer(events)

    host.start = _failed_start  # type: ignore[method-assign]
    monkeypatch.setattr(server_module, "reset_harness_packages_state", lambda: None)
    monkeypatch.setattr("websockets.legacy.server.serve", _serve)
    monkeypatch.setattr(server, "_bootstrap_internal_jiuwenbox", _noop_async)

    await server.start()
    assert server._pcs_start_task is not None  # pylint: disable=protected-access
    await server._pcs_start_task  # pylint: disable=protected-access

    assert server._server is not None  # pylint: disable=protected-access
    assert events == ["ws-serve"]


@pytest.mark.asyncio
async def test_pcs_stop_failure_does_not_change_normal_stop_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)
    host = _FakePCSHost.instances[0]
    events: list[str] = []

    async def _failed_stop(*, timeout_seconds: float = 30.0) -> None:
        del timeout_seconds
        raise RuntimeError("PCS stop failed")

    host.stop = _failed_stop  # type: ignore[method-assign]
    server._server = _FakeWebSocketServer(events)  # pylint: disable=protected-access
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_product_hooks.cancel_pending_tasks",
        _noop_async,
    )
    monkeypatch.setattr(server._jiuwenbox_runner, "stop", _noop_async)

    await server.stop()

    assert events == ["ws-close", "ws-wait-closed"]


@pytest.mark.asyncio
async def test_stop_finishes_main_services_before_pcs_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)
    host = _FakePCSHost.instances[0]
    events: list[str] = []

    async def _record_pcs_stop(*, timeout_seconds: float = 30.0) -> None:
        del timeout_seconds
        events.append("pcs-stop")

    host.stop = _record_pcs_stop  # type: ignore[method-assign]
    server._server = _FakeWebSocketServer(events)  # pylint: disable=protected-access
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_product_hooks.cancel_pending_tasks",
        _noop_async,
    )
    monkeypatch.setattr(server._jiuwenbox_runner, "stop", _noop_async)

    await server.stop()

    assert events == ["ws-close", "ws-wait-closed", "pcs-stop"]


@pytest.mark.asyncio
async def test_stop_calls_host_when_websocket_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)
    host = _FakePCSHost.instances[0]
    server._server = _FailingWebSocketServer()  # pylint: disable=protected-access

    with pytest.raises(RuntimeError, match="close failed"):
        await server.stop()

    assert host.events == [("stop", 30.0)]


@pytest.mark.asyncio
async def test_stop_does_not_swallow_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)
    host = _FakePCSHost.instances[0]

    async def _cancelled_stop(*, timeout_seconds: float = 30.0) -> None:
        del timeout_seconds
        raise asyncio.CancelledError

    host.stop = _cancelled_stop  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await server.stop()


async def _noop_async() -> None:
    return None
