# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WebChannel 命名管道形态单测（桌面集成：cron 通道的 pipe server 皮）。

docs/named-pipe-migration-design.md §5.4（claw_desktop 仓）：
- 桌面形态（密钥包含 pipes.cron）除既有 WS server 外并行起命名管道 server，
  协议为长度前缀 JSON 帧（np_transport）；
- 每条管道连接首帧必须是 ``{"type":"auth","token":<e2aToken>}``，校验失败即关管；
- 通过后进入与 WS 形态相同的消息内核：connection.ack 首事件、type=req/res 帧、
  cron.job.* 分发、chat.final 广播同时到达 WS 与管道两路；
- Gateway 主 WS server（/acp、/tui 路由，桌面注入 18591）桌面形态不监听。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from typing import Any

import pytest

import jiuwenswarm.common.secrets_bootstrap as secrets_bootstrap_mod
from jiuwenswarm.gateway.app_gateway import (
    GatewayServer,
    GatewayServerConfig,
    is_desktop_runtime,
)
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.web.web_connect import (
    WebChannel,
    WebChannelConfig,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="命名管道仅 Windows")

_TEST_TOKEN = "e2a-test-token"


@pytest.fixture(autouse=True)
def _reset_secrets(monkeypatch: pytest.MonkeyPatch):
    """每用例重置密钥包 vault（默认非桌面形态，用例按需注入）。"""
    monkeypatch.setattr(secrets_bootstrap_mod, "_SECRETS", {})
    monkeypatch.setattr(secrets_bootstrap_mod, "_LOADED", False)
    yield


def _feed_secrets(
    monkeypatch: pytest.MonkeyPatch,
    pipe_path: str | None,
    *,
    token: str = _TEST_TOKEN,
    desktop_exe: str | None = None,
) -> None:
    """注入密钥包（模拟桌面主进程 stdin 下发的结果，直接写内存 vault）。"""
    secrets: dict[str, Any] = {"e2aToken": token}
    if pipe_path is not None:
        secrets["pipes"] = {"cron": pipe_path}
    if desktop_exe:
        secrets["desktopExe"] = desktop_exe
    monkeypatch.setattr(secrets_bootstrap_mod, "_SECRETS", secrets)
    monkeypatch.setattr(secrets_bootstrap_mod, "_LOADED", True)


def _pipe_path(name: str) -> str:
    return rf"\\.\pipe\claw-test-webpipe-{os.getpid()}-{name}"


def _make_channel() -> WebChannel:
    """构造带最小协议面的 WebChannel：connection.ack 连接钩子 + cron.job.list mock。"""
    channel = WebChannel(WebChannelConfig(enabled=True), RobotMessageRouter())

    async def _ack_hook(ws: Any) -> None:
        await channel.send_event(
            ws,
            "connection.ack",
            {
                "session_id": getattr(ws, "_jiuwen_initial_sid", ""),
                "protocol_version": "1.0",
                "transport": "web",
            },
        )

    channel.on_connect(_ack_hook)

    async def _cron_job_list(ws: Any, req_id: str, params: dict, session_id: str) -> None:
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={"jobs": [{"job_id": "job-1", "name": "demo"}]},
        )

    channel.register_method("cron.job.list", _cron_job_list)
    channel.on_message(lambda msg: None)
    return channel


class _DummyBus:
    @staticmethod
    async def publish_user_messages(msg: Any) -> None:
        return None


class TestPipeServerGating:
    """管道 server 的桌面形态门控：非桌面零变化。"""

    @pytest.mark.asyncio
    async def test_not_started_without_secrets(self) -> None:
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        assert channel._pipe_server is None

    @pytest.mark.asyncio
    async def test_not_started_without_cron_pipe_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _feed_secrets(monkeypatch, None)  # 有密钥包但无 pipes.cron
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        assert channel._pipe_server is None

    @pytest.mark.asyncio
    async def test_started_with_cron_pipe_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _pipe_path("gating")
        _feed_secrets(monkeypatch, path)
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        try:
            assert channel._pipe_server is not None
        finally:
            await channel.stop()

    @pytest.mark.asyncio
    async def test_desktop_mode_skips_ws_listener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """桌面形态（密钥包）：start() 不起 WS TCP 监听，仅管道 server（零监听端口目标态）。"""
        path = _pipe_path("desktop-no-ws")
        _feed_secrets(monkeypatch, path)
        channel = _make_channel()
        start_task = asyncio.create_task(channel.start())
        try:
            for _ in range(200):
                if channel._pipe_server is not None:
                    break
                await asyncio.sleep(0.05)
            assert channel._pipe_server is not None
            assert channel._server is None  # 桌面形态不起 WS TCP 监听
        finally:
            await channel.stop()
            await asyncio.wait_for(start_task, timeout=10)


class TestPipeAuth:
    """管道连接首帧 auth 校验（失败即关管）。"""

    @pytest.mark.asyncio
    async def test_wrong_token_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from jiuwenswarm.common.np_transport import PipeClosedError, open_pipe

        path = _pipe_path("bad-token")
        _feed_secrets(monkeypatch, path)
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        try:
            client = await open_pipe(path, timeout=5)
            try:
                await client.send_frame({"type": "auth", "token": "wrong-token"})
            except PipeClosedError:
                pass  # 服务端已断开（写缓冲竞态），读侧断言关闭即可
            with pytest.raises(PipeClosedError):
                await client.recv_frame(timeout=3)
            await client.close()
        finally:
            await channel.stop()

    @pytest.mark.asyncio
    async def test_non_auth_first_frame_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from jiuwenswarm.common.np_transport import PipeClosedError, open_pipe

        path = _pipe_path("non-auth")
        _feed_secrets(monkeypatch, path)
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        try:
            client = await open_pipe(path, timeout=5)
            try:
                await client.send_frame(
                    {"type": "req", "id": "r0", "method": "cron.job.list", "params": {}}
                )
            except PipeClosedError:
                pass
            with pytest.raises(PipeClosedError):
                await client.recv_frame(timeout=3)
            await client.close()
        finally:
            await channel.stop()

    @pytest.mark.asyncio
    async def test_verify_client_rejects_non_desktop_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """密钥包携带 desktopExe 时启用 PID→镜像白名单：非桌面进程连入即被断。"""
        from jiuwenswarm.common.np_transport import PipeClosedError, open_pipe

        path = _pipe_path("verify-reject")
        _feed_secrets(monkeypatch, path, desktop_exe=r"C:\nonexistent\fake-desktop.exe")
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        assert channel._pipe_server is not None
        try:
            client = await open_pipe(path, timeout=5)
            # accept 阶段校验失败：auth 帧尚未被消费即断管，读侧报关闭
            try:
                await client.send_frame({"type": "auth", "token": _TEST_TOKEN})
            except PipeClosedError:
                pass
            with pytest.raises(PipeClosedError):
                await client.recv_frame(timeout=3)
            await client.close()
        finally:
            await channel.stop()

    @pytest.mark.asyncio
    async def test_verify_client_accepts_self_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """白名单含本进程镜像（模拟桌面主进程）时正常走完 auth + ack。"""
        import win32api
        import win32con
        import win32process

        from jiuwenswarm.common.np_transport import open_pipe

        # venv launcher ≠ 真实镜像路径，须用同一 Win32 API 取（对齐 np_transport 自测）
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            os.getpid(),
        )
        try:
            real_image = win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)

        path = _pipe_path("verify-accept")
        _feed_secrets(monkeypatch, path, desktop_exe=real_image)
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        assert channel._pipe_server is not None
        try:
            client = await open_pipe(path, timeout=5)
            await client.send_frame({"type": "auth", "token": _TEST_TOKEN})
            ack = await client.recv_frame(timeout=5)
            assert ack["type"] == "event"
            assert ack["event"] == "connection.ack"
            await client.close()
        finally:
            await channel.stop()


class TestPipeSession:
    """auth 通过后进入公共消息内核：connection.ack / cron.job.* RPC roundtrip。"""

    @pytest.mark.asyncio
    async def test_ack_and_cron_job_list_roundtrip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from jiuwenswarm.common.np_transport import open_pipe

        path = _pipe_path("roundtrip")
        _feed_secrets(monkeypatch, path)
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        assert channel._pipe_server is not None
        client = await open_pipe(path, timeout=5)
        try:
            # auth 首帧 → connection.ack（与 WS 形态一致的首事件）
            await client.send_frame({"type": "auth", "token": _TEST_TOKEN})
            ack = await client.recv_frame(timeout=5)
            assert ack["type"] == "event"
            assert ack["event"] == "connection.ack"
            assert ack["payload"]["protocol_version"] == "1.0"

            # cron.job.list RPC roundtrip（下游 cron 处理已被 register_method mock）
            await client.send_frame(
                {"type": "req", "id": "req-1", "method": "cron.job.list", "params": {}}
            )
            res = await client.recv_frame(timeout=5)
            assert res["type"] == "res"
            assert res["id"] == "req-1"
            assert res["ok"] is True
            assert res["payload"]["jobs"] == [{"job_id": "job-1", "name": "demo"}]
        finally:
            await client.close()
            await channel.stop()

    @pytest.mark.asyncio
    async def test_unknown_method_error_frame(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from jiuwenswarm.common.np_transport import open_pipe

        path = _pipe_path("unknown-method")
        _feed_secrets(monkeypatch, path)
        channel = _make_channel()
        await channel._maybe_start_pipe_server()
        assert channel._pipe_server is not None
        client = await open_pipe(path, timeout=5)
        try:
            await client.send_frame({"type": "auth", "token": _TEST_TOKEN})
            ack = await client.recv_frame(timeout=5)
            assert ack["event"] == "connection.ack"

            await client.send_frame(
                {"type": "req", "id": "req-x", "method": "no.such.method", "params": {}}
            )
            res = await client.recv_frame(timeout=5)
            assert res["type"] == "res"
            assert res["id"] == "req-x"
            assert res["ok"] is False
            assert res["code"] == "METHOD_NOT_FOUND"
        finally:
            await client.close()
            await channel.stop()


class TestBroadcastBothTransports:
    """chat.final 等下行广播到达全部已鉴权客户端。

    桌面形态（密钥包）下 WebChannel 仅有命名管道载体（WS 18590 不监听），
    广播覆盖用两个管道客户端验证。
    """

    @pytest.mark.asyncio
    async def test_chat_final_reaches_all_pipe_clients(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from jiuwenswarm.common.np_transport import open_pipe

        path = _pipe_path("broadcast")
        _feed_secrets(monkeypatch, path)
        channel = _make_channel()
        start_task = asyncio.create_task(channel.start(), name="web-channel-test")
        try:
            for _ in range(200):
                if channel._pipe_server is not None:
                    break
                await asyncio.sleep(0.05)
            assert channel._pipe_server is not None, "管道 server 未启动"
            assert channel._server is None, "桌面形态不应起 WS 监听"

            async def _authed_client():
                client = await open_pipe(path, timeout=5)
                await client.send_frame({"type": "auth", "token": _TEST_TOKEN})
                ack = await client.recv_frame(timeout=5)
                assert ack["event"] == "connection.ack"
                return client

            client_a = await _authed_client()
            client_b = await _authed_client()
            try:
                await channel.broadcast_event(
                    "chat.final",
                    {
                        "session_id": "sess-broadcast",
                        "content": "cron 执行结果",
                        "cron": {"job_id": "job-1", "run_id": "run-1"},
                    },
                )
                for client in (client_a, client_b):
                    frame = await client.recv_frame(timeout=5)
                    assert frame["type"] == "event"
                    assert frame["event"] == "chat.final"
                    assert frame["payload"]["content"] == "cron 执行结果"
                    assert frame["payload"]["cron"]["job_id"] == "job-1"
            finally:
                await client_a.close()
                await client_b.close()
        finally:
            await channel.stop()
            await asyncio.wait_for(start_task, timeout=10)


class TestGatewayWsDesktopGate:
    """Gateway 主 WS server（/acp、/tui，桌面注入 18591）桌面形态不监听。"""

    def test_is_desktop_runtime_false_without_secrets(self) -> None:
        assert is_desktop_runtime() is False

    def test_is_desktop_runtime_true_with_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _feed_secrets(monkeypatch, None)
        assert is_desktop_runtime() is True

    @staticmethod
    def _free_port() -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    @pytest.mark.asyncio
    async def test_disabled_gateway_server_does_not_listen(self) -> None:
        """桌面形态 enabled=False：start() 不起监听，端口拒绝连接。"""
        port = self._free_port()
        config = GatewayServerConfig(enabled=False, host="127.0.0.1", port=port, routes={})
        server = GatewayServer(config, _DummyBus())
        await server.start()
        try:
            assert server._server is None
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=1)
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_enabled_gateway_server_still_listens(self) -> None:
        """非桌面对照：enabled=True 时监听行为不变（防误伤）。"""
        config = GatewayServerConfig(enabled=True, host="127.0.0.1", port=0, routes={})
        server = GatewayServer(config, _DummyBus())
        await server.start()
        try:
            assert server._server is not None
            port = server._server.sockets[0].getsockname()[1]
            conn = socket.create_connection(("127.0.0.1", port), timeout=1)
            conn.close()
            # 等服务端 accept/断连回调落定再停：proactor 上 accept 完成包与
            # server.close() 竞态会触发 _attach 断言并把 socket 泄漏给 GC
            # （ResourceWarning 会被 pytest unraisable 归到后续测试头上）
            await asyncio.sleep(0.3)
        finally:
            await server.stop()
            await asyncio.sleep(0.1)
