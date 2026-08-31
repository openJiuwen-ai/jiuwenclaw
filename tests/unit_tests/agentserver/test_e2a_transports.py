# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""E2A 通道传输抽象与桌面形态（stdio/命名管道）单测。

docs/named-pipe-migration-design.md §5.2（claw_desktop 仓）的 jiuwen 侧：
- MessageTransport 三实现（Ws/Pipe/Stdio）与连接内核的 roundtrip：
  auth 首帧错误被拒 / 正确后 connection.ack + echo 方法响应（最小假内核，
  不拉全量 AgentServer 依赖）；
- StdioMessageTransport：os.pipe() 喂 stdin 替身 + os.pipe() 捕获 os.write 帧输出；
- DesktopE2aChannels 编排：非桌面形态返回 None（回退 TCP）；桌面形态 stdio auth
  失败 → on_stdio_closed（进程退出编排）且不进入连接内核；
- gateway agent_client 管道形态：serve_pipe 假 AgentServer（auth 校验 + ack +
  E2A unary echo），验证连接/ack/断连重连；
- AgentWebSocketServer.run_connection 真实内核 + 内存假传输：ack 首事件 +
  消息分发 + EOF 断连清理。

pytest filterwarnings=error + unraisable 约束：测试内创建的 server/连接/管道
一律在本测试内完整关闭并 gc.collect() 收尾（参考 test_web_pipe_channel.py）。
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import sys
from typing import Any
from unittest.mock import AsyncMock

import pytest

import jiuwenswarm.common.secrets_bootstrap as secrets_bootstrap_mod
from jiuwenswarm.common.np_transport import (
    FrameCodecError,
    PipeClosedError,
    encode_frame,
    open_pipe,
    serve_pipe,
)
from jiuwenswarm.server.e2a_desktop import (
    DesktopE2aChannels,
    start_desktop_e2a_channels,
)
from jiuwenswarm.server.e2a_transports import (
    StdioMessageTransport,
    WsMessageTransport,
    build_connection_ack_frame,
    encode_text_frame,
    verify_auth_frame,
)
from jiuwenswarm.server.ws_send import send_wire_payload

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="命名管道仅 Windows"),
    # 各用例收尾的 gc.collect() 会把「前序测试遗留资源（他案泄漏的 socket/loop）」
    # 的 GC 警告收拢到本文件用例头上（pytest unraisable 归因机制）——这些警告与
    # 本文件行为断言无关，按 test_web_pipe_channel.py 广播用例的先例豁免。
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
]

_TEST_TOKEN = "e2a-test-token"


@pytest.fixture(autouse=True)
def _reset_secrets(monkeypatch: pytest.MonkeyPatch):
    """每用例重置密钥包 vault（默认非桌面形态，用例按需注入）。"""
    monkeypatch.setattr(secrets_bootstrap_mod, "_SECRETS", {})
    monkeypatch.setattr(secrets_bootstrap_mod, "_LOADED", False)
    yield


@pytest.fixture(autouse=True)
def _preserve_stdout():
    """DesktopE2aChannels.start() 会 redirect_stdout_to_stderr（全局）——
    测试后恢复 sys.stdout，防 pytest capture 被永久换绑。"""
    original = sys.stdout
    yield
    sys.stdout = original


def _feed_secrets(
    monkeypatch: pytest.MonkeyPatch,
    pipe_path: str | None,
    *,
    token: str = _TEST_TOKEN,
    e2a_transport: str = "stdio",
) -> None:
    """注入密钥包（模拟桌面主进程 stdin 下发的结果，直接写内存 vault）。

    e2a_transport 默认 'stdio'（E2A stdio 形态）；'ws' 模拟迁移期默认的
    WS 形态（密钥包存在但 E2A 仍走 TCP 18592）。
    """
    secrets: dict[str, Any] = {"e2aToken": token, "e2aTransport": e2a_transport}
    if pipe_path is not None:
        secrets["pipes"] = {"agentE2a": pipe_path}
    monkeypatch.setattr(secrets_bootstrap_mod, "_SECRETS", secrets)
    monkeypatch.setattr(secrets_bootstrap_mod, "_LOADED", True)


def _pipe_path(name: str) -> str:
    return rf"\\.\pipe\claw-test-e2a-{os.getpid()}-{name}"


def _real_process_image() -> str:
    """当前进程的真实镜像路径（venv launcher ≠ 镜像路径，须用 Win32 API 取）。"""
    import win32api
    import win32con
    import win32process

    handle = win32api.OpenProcess(
        win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
        False,
        os.getpid(),
    )
    try:
        return win32process.GetModuleFileNameEx(handle, 0)
    finally:
        win32api.CloseHandle(handle)


class _StdioPipePair:
    """os.pipe() 双管：desktop→agent（stdin 替身）+ agent→desktop（捕获 os.write 帧输出）。"""

    def __init__(self, *, create_transport: bool = True) -> None:
        self._r_in, self._w_in = os.pipe()
        self._r_out, self._w_out = os.pipe()
        self.stdin = os.fdopen(self._r_in, "rb")
        self.transport: StdioMessageTransport | None = (
            StdioMessageTransport(self.stdin, stdout_fd=self._w_out)
            if create_transport
            else None
        )

    def feed(self, payload: Any) -> None:
        """桌面侧写一帧进 agent stdin。"""
        os.write(self._w_in, encode_frame(payload))

    def feed_raw(self, data: bytes) -> None:
        os.write(self._w_in, data)

    def feed_eof(self) -> None:
        """桌面侧关闭 stdin 管道（EOF）。"""
        if self._w_in >= 0:
            os.close(self._w_in)
            self._w_in = -1

    async def read_frame(self, timeout: float = 5.0) -> Any:
        """读 agent 写出的一帧（os.read 阻塞走 to_thread + 超时兜底）。"""

        def _read_exact(size: int) -> bytes:
            parts: list[bytes] = []
            remaining = size
            while remaining > 0:
                chunk = os.read(self._r_out, remaining)
                if not chunk:
                    raise PipeClosedError("stdio 输出管道提前关闭")
                parts.append(chunk)
                remaining -= len(chunk)
            return b"".join(parts)

        async def _read() -> Any:
            header = await asyncio.to_thread(_read_exact, 4)
            length = int.from_bytes(header, "little")
            body = await asyncio.to_thread(_read_exact, length)
            return json.loads(body.decode("utf-8"))

        return await asyncio.wait_for(_read(), timeout)

    async def close(self) -> None:
        self.feed_eof()
        if self.transport is not None:
            await self.transport.close()
        try:
            self.stdin.close()
        except OSError:
            pass
        for fd in (self._r_out, self._w_out):
            try:
                os.close(fd)
            except OSError:
                pass


class _FakeKernel:
    """最小协议内核（run_connection 的替身）：connection.ack 首事件 + echo。"""

    def __init__(self) -> None:
        self.connections: list[Any] = []
        self.closed = asyncio.Event()

    async def __call__(self, transport: Any, *, remote: Any = None) -> None:
        self.connections.append(transport)
        await send_wire_payload(transport, build_connection_ack_frame())
        while True:
            raw = await transport.recv_text()
            if raw is None:
                break
            data = json.loads(raw)
            if data.get("type") == "shutdown":
                break
            await send_wire_payload(transport, {"echo": data})
        await transport.close()
        self.closed.set()


async def _make_channels(
    kernel: _FakeKernel,
    *,
    on_stdio_closed: Any = None,
) -> tuple[DesktopE2aChannels, _StdioPipePair]:
    """构造 stdio 管道注入的 DesktopE2aChannels（写端持有在 pair 上）。"""
    pair = _StdioPipePair(create_transport=False)  # transport 由 channels 内部创建
    channels = DesktopE2aChannels(
        kernel,
        on_stdio_closed=on_stdio_closed,
        stdin=pair.stdin,
        stdout_fd=pair._w_out,
    )
    return channels, pair


# ---------------------------------------------------------------------------
# verify_auth_frame / encode_text_frame 纯函数
# ---------------------------------------------------------------------------


class TestAuthFrame:
    def test_valid_token_accepted(self) -> None:
        assert verify_auth_frame({"type": "auth", "token": "t-1"}, "t-1") is True

    def test_wrong_token_rejected(self) -> None:
        assert verify_auth_frame({"type": "auth", "token": "bad"}, "t-1") is False

    def test_non_auth_frame_rejected(self) -> None:
        assert verify_auth_frame({"type": "req", "token": "t-1"}, "t-1") is False
        assert verify_auth_frame("not-a-dict", "t-1") is False
        assert verify_auth_frame(None, "t-1") is False

    def test_empty_expected_rejected(self) -> None:
        assert verify_auth_frame({"type": "auth", "token": "t-1"}, "") is False


class TestEncodeTextFrame:
    def test_roundtrip_with_frame_decoder(self) -> None:
        from jiuwenswarm.common.np_transport import FrameDecoder

        text = json.dumps({"a": "你"}, ensure_ascii=False)
        frames = FrameDecoder().feed(encode_text_frame(text))
        assert frames == [{"a": "你"}]

    def test_oversize_rejected(self) -> None:
        with pytest.raises(FrameCodecError):
            encode_text_frame("x" * (8 * 2**20 + 1))


# ---------------------------------------------------------------------------
# StdioMessageTransport
# ---------------------------------------------------------------------------


class TestStdioTransport:
    @pytest.mark.asyncio
    async def test_recv_and_send_roundtrip(self) -> None:
        pair = _StdioPipePair()
        try:
            pair.feed({"type": "auth", "token": "t"})
            raw = await pair.transport.recv_text()
            assert json.loads(raw) == {"type": "auth", "token": "t"}

            await pair.transport.send_text(json.dumps(build_connection_ack_frame()))
            frame = await pair.read_frame()
            assert frame["type"] == "event"
            assert frame["event"] == "connection.ack"
        finally:
            await pair.close()
        gc.collect()

    @pytest.mark.asyncio
    async def test_recv_batches_partial_and_multi_frames(self) -> None:
        """半包/粘包：分段写入与两帧连写都按序各收一条。"""
        pair = _StdioPipePair()
        try:
            # 半包：一帧分两段写
            frame1 = encode_frame({"n": 1})
            pair.feed_raw(frame1[:3])
            await asyncio.sleep(0.1)
            pair.feed_raw(frame1[3:])
            assert json.loads(await pair.transport.recv_text()) == {"n": 1}
            # 粘包：两帧一次写入
            pair.feed_raw(encode_frame({"n": 2}) + encode_frame({"n": 3}))
            assert json.loads(await pair.transport.recv_text()) == {"n": 2}
            assert json.loads(await pair.transport.recv_text()) == {"n": 3}
        finally:
            await pair.close()
        gc.collect()

    @pytest.mark.asyncio
    async def test_eof_returns_none(self) -> None:
        pair = _StdioPipePair()
        try:
            pair.feed({"n": 1})
            assert json.loads(await pair.transport.recv_text()) == {"n": 1}
            pair.feed_eof()
            assert await pair.transport.recv_text() is None
            assert pair.transport.closed is True
            # 关闭后 send 抛断连异常
            with pytest.raises(PipeClosedError):
                await pair.transport.send_text("{}")
        finally:
            await pair.close()
        gc.collect()

    @pytest.mark.asyncio
    async def test_bad_frame_length_raises_protocol_error(self) -> None:
        pair = _StdioPipePair()
        try:
            pair.feed_raw((0).to_bytes(4, "little"))  # 帧长度为 0：协议错误
            with pytest.raises(FrameCodecError):
                await pair.transport.recv_text()
        finally:
            await pair.close()
        gc.collect()


# ---------------------------------------------------------------------------
# WsMessageTransport（假 ws，不拉真实 websockets 连接）
# ---------------------------------------------------------------------------


class TestWsTransport:
    @pytest.mark.asyncio
    async def test_recv_iterates_until_clean_close(self) -> None:
        class FakeWs:
            remote_address = ("127.0.0.1", 1)

            def __init__(self) -> None:
                self.items = iter(["a", "b"])
                self.sent: list[str] = []
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.items)
                except StopIteration:
                    raise StopAsyncIteration from None

            async def send(self, data: str) -> None:
                self.sent.append(data)

            async def close(self) -> None:
                self.closed = True

        ws = FakeWs()
        transport = WsMessageTransport(ws)
        assert transport.remote_address == ("127.0.0.1", 1)  # 鸭子委托
        assert await transport.recv_text() == "a"
        assert await transport.recv_text() == "b"
        assert await transport.recv_text() is None  # StopAsyncIteration → None
        await transport.send_text("x")
        assert ws.sent == ["x"]
        await transport.close()
        assert ws.closed is True


# ---------------------------------------------------------------------------
# DesktopE2aChannels 编排（stdio auth + 命名管道）
# ---------------------------------------------------------------------------


class TestDesktopChannelsGating:
    """分流判定契约：按密钥包 e2aTransport 字段，而非密钥包存在与否。"""

    @pytest.mark.asyncio
    async def test_returns_none_without_secrets(self) -> None:
        kernel = _FakeKernel()
        assert await start_desktop_e2a_channels(kernel) is None

    @pytest.mark.asyncio
    async def test_returns_none_when_e2a_transport_is_ws(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """密钥包存在但 e2aTransport='ws'（迁移期桌面默认）：不启 stdio/管道，
        AgentServer 保持 TCP 18592 监听（由调用方回退）。"""
        from jiuwenswarm.server.e2a_transports import e2a_stdio_mode_enabled

        _feed_secrets(monkeypatch, _pipe_path("ws-mode"), e2a_transport="ws")
        assert e2a_stdio_mode_enabled() is False
        kernel = _FakeKernel()
        assert await start_desktop_e2a_channels(kernel) is None

        # gateway 侧同契约：ws 形态不解析管道（回退 AGENT_SERVER_URL）
        from jiuwenswarm.gateway.routing.agent_client import (
            resolve_agent_e2a_pipe_path,
        )

        assert resolve_agent_e2a_pipe_path() is None

    @pytest.mark.asyncio
    async def test_returns_none_when_e2a_transport_field_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """密钥包未携带 e2aTransport 字段（旧版桌面）：按 ws 形态处理。"""
        secrets: dict[str, Any] = {"e2aToken": _TEST_TOKEN}
        monkeypatch.setattr(secrets_bootstrap_mod, "_SECRETS", secrets)
        monkeypatch.setattr(secrets_bootstrap_mod, "_LOADED", True)
        kernel = _FakeKernel()
        assert await start_desktop_e2a_channels(kernel) is None


class TestDesktopChannelsStdio:
    @pytest.mark.asyncio
    async def test_auth_failure_skips_kernel_and_signals_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _feed_secrets(monkeypatch, None)
        kernel = _FakeKernel()
        closed = asyncio.Event()
        channels, pair = await _make_channels(kernel, on_stdio_closed=closed.set)
        await channels.start()
        try:
            pair.feed({"type": "auth", "token": "wrong-token"})
            # auth 失败：不进入连接内核，触发 on_stdio_closed（进程退出编排）
            assert await asyncio.wait_for(closed.wait(), timeout=5)
            assert kernel.connections == []
        finally:
            pair.feed_eof()
            await channels.stop()
            await pair.close()
        gc.collect()

    @pytest.mark.asyncio
    async def test_auth_success_then_ack_echo_and_eof_close(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _feed_secrets(monkeypatch, None)
        kernel = _FakeKernel()
        closed = asyncio.Event()
        channels, pair = await _make_channels(kernel, on_stdio_closed=closed.set)
        await channels.start()
        try:
            pair.feed({"type": "auth", "token": _TEST_TOKEN})
            # auth 通过 → connection.ack 首事件
            ack = await pair.read_frame()
            assert ack == build_connection_ack_frame()
            # echo roundtrip
            pair.feed({"request_id": "r1", "method": "ping"})
            echo = await pair.read_frame()
            assert echo == {"echo": {"request_id": "r1", "method": "ping"}}
            assert len(kernel.connections) == 1
            # 桌面关闭 stdin（EOF）→ 内核结束 → on_stdio_closed
            pair.feed_eof()
            assert await asyncio.wait_for(kernel.closed.wait(), timeout=5)
            assert await asyncio.wait_for(closed.wait(), timeout=5)
        finally:
            pair.feed_eof()
            await channels.stop()
            await pair.close()
        gc.collect()


class TestDesktopChannelsPipe:
    @pytest.mark.asyncio
    async def test_pipe_auth_wrong_token_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _pipe_path("bad-token")
        _feed_secrets(monkeypatch, path)
        monkeypatch.setattr(sys, "executable", _real_process_image())
        kernel = _FakeKernel()
        channels, pair = await _make_channels(kernel)
        await channels.start()
        assert channels.pipe_server is not None
        try:
            client = await open_pipe(path, timeout=5)
            try:
                await client.send_frame({"type": "auth", "token": "wrong"})
            except PipeClosedError:
                pass  # 服务端已断开（写缓冲竞态），读侧断言关闭即可
            with pytest.raises(PipeClosedError):
                await client.recv_frame(timeout=3)
            await client.close()
            assert kernel.connections == []
        finally:
            pair.feed_eof()
            await channels.stop()
            await pair.close()
        gc.collect()

    @pytest.mark.asyncio
    async def test_pipe_auth_and_echo_roundtrip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _pipe_path("roundtrip")
        _feed_secrets(monkeypatch, path)
        monkeypatch.setattr(sys, "executable", _real_process_image())
        kernel = _FakeKernel()
        channels, pair = await _make_channels(kernel)
        await channels.start()
        assert channels.pipe_server is not None
        client = await open_pipe(path, timeout=5)
        try:
            # auth 首帧 → connection.ack（与 WS 形态一致的首事件）
            await client.send_frame({"type": "auth", "token": _TEST_TOKEN})
            ack = await client.recv_frame(timeout=5)
            assert ack == build_connection_ack_frame()
            # echo roundtrip
            await client.send_frame({"request_id": "r2", "method": "ping"})
            echo = await client.recv_frame(timeout=5)
            assert echo == {"echo": {"request_id": "r2", "method": "ping"}}
        finally:
            await client.close()
            pair.feed_eof()
            await channels.stop()
            await pair.close()
        gc.collect()

    @pytest.mark.asyncio
    async def test_pipe_verify_client_rejects_foreign_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """verify_client=make_image_verifier({sys.executable, sys._base_executable})：
        白名单（两者）不含本进程镜像时（模拟外来仿冒进程），accept 阶段即断管。"""
        path = _pipe_path("verify-reject")
        _feed_secrets(monkeypatch, path)
        monkeypatch.setattr(sys, "executable", r"C:\nonexistent\fake-jiuwenswarm.exe")
        # uv trampoline 下进程实际镜像是 _base_executable，白名单两个都查，须一并遮蔽
        monkeypatch.setattr(sys, "_base_executable", r"C:\nonexistent\fake-jiuwenswarm.exe")
        kernel = _FakeKernel()
        channels, pair = await _make_channels(kernel)
        await channels.start()
        assert channels.pipe_server is not None
        try:
            client = await open_pipe(path, timeout=5)
            try:
                await client.send_frame({"type": "auth", "token": _TEST_TOKEN})
            except PipeClosedError:
                pass
            with pytest.raises(PipeClosedError):
                await client.recv_frame(timeout=3)
            await client.close()
            assert kernel.connections == []
        finally:
            pair.feed_eof()
            await channels.stop()
            await pair.close()
        gc.collect()


# ---------------------------------------------------------------------------
# gateway agent_client 管道形态（假 AgentServer：auth + ack + E2A unary echo）
# ---------------------------------------------------------------------------


class _FakeAgentPipeServer:
    """serve_pipe 上的假 AgentServer：auth 校验 → connection.ack → E2A unary echo。"""

    def __init__(self, name: str, *, token: str = _TEST_TOKEN) -> None:
        self.path = _pipe_path(name)
        self.token = token
        self.server: Any = None
        self.auth_frames: list[Any] = []
        self.connections = 0

    async def __aenter__(self) -> "_FakeAgentPipeServer":
        self.server = await serve_pipe(self.path, self._handle)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.server.stop()
        gc.collect()

    async def _handle(self, stream: Any) -> None:
        from jiuwenswarm.common.e2a.wire_codec import encode_agent_response_for_wire
        from jiuwenswarm.common.schema.agent import AgentResponse

        self.connections += 1
        try:
            first = await stream.recv_frame(timeout=5)
        except Exception:
            return
        self.auth_frames.append(first)
        if not verify_auth_frame(first, self.token):
            return
        await stream.send_frame(build_connection_ack_frame())
        while True:
            try:
                frame = await stream.recv_frame()
            except Exception:
                return
            request_id = str(frame.get("request_id") or "")
            resp = AgentResponse(
                request_id=request_id,
                channel_id=str(frame.get("channel") or ""),
                ok=True,
                payload={"content": "pong"},
            )
            await stream.send_frame(
                encode_agent_response_for_wire(resp, response_id=request_id)
            )


class TestAgentClientPipe:
    def _feed_gateway_secrets(self, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
        _feed_secrets(monkeypatch, path)

    @pytest.mark.asyncio
    async def test_connect_ack_and_unary_roundtrip_over_pipe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.gateway.routing.agent_client import (
            WebSocketAgentServerClient,
            resolve_agent_e2a_pipe_path,
        )

        async with _FakeAgentPipeServer("client-roundtrip") as server:
            self._feed_gateway_secrets(monkeypatch, server.path)
            assert resolve_agent_e2a_pipe_path() == server.path

            client = WebSocketAgentServerClient()
            try:
                # uri 在管道形态下被忽略（回退形态才消费）
                await client.connect("ws://127.0.0.1:1")
                assert client.server_ready is True
                assert server.auth_frames == [
                    {"type": "auth", "token": _TEST_TOKEN}
                ]

                env = e2a_from_agent_fields(
                    request_id="req-pipe-1",
                    channel_id="web",
                    session_id="sess-1",
                    params={"message": "hi"},
                )
                resp = await client.send_request(env)
                assert resp.ok is True
                assert resp.request_id == "req-pipe-1"
                assert resp.payload == {"content": "pong"}
            finally:
                await client.disconnect()
        gc.collect()
        await asyncio.sleep(0.1)
        gc.collect()

    @pytest.mark.asyncio
    async def test_reconnect_after_pipe_broken(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """断连（服务端关停）后 send_request 经 _ensure_connected_for_request 重连。"""
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.gateway.routing.agent_client import WebSocketAgentServerClient

        path = _pipe_path("client-reconnect")
        self._feed_gateway_secrets(monkeypatch, path)
        client = WebSocketAgentServerClient()
        server = _FakeAgentPipeServer("client-reconnect")
        server.path = path
        server.server = await serve_pipe(path, server._handle)
        try:
            await client.connect("ws://127.0.0.1:1")
            assert client.server_ready is True

            # 服务端关停 → 客户端接收循环感知断连
            await server.server.stop()
            server.server = None
            for _ in range(60):
                if client._ws is None:  # noqa: SLF001
                    break
                await asyncio.sleep(0.05)
            assert client._ws is None  # noqa: SLF001

            # 服务端重启（同管道名）→ send_request 触发按需重连
            server.server = await serve_pipe(path, server._handle)
            env = e2a_from_agent_fields(
                request_id="req-pipe-2",
                channel_id="web",
                session_id="sess-1",
                params={"message": "hi"},
            )
            resp = await client.send_request(env)
            assert resp.ok is True
            assert resp.payload == {"content": "pong"}
            assert client.server_ready is True
            assert server.auth_frames[-1] == {"type": "auth", "token": _TEST_TOKEN}
        finally:
            await client.disconnect()
            if server.server is not None:
                await server.server.stop()
        gc.collect()
        await asyncio.sleep(0.1)
        gc.collect()


# ---------------------------------------------------------------------------
# AgentWebSocketServer.run_connection 真实内核 + 内存假传输
# ---------------------------------------------------------------------------


class _MemoryTransport:
    def __init__(self, inbound: list[Any]) -> None:
        self._inbound: asyncio.Queue = asyncio.Queue()
        for item in inbound:
            self._inbound.put_nowait(item)
        self.sent: list[str] = []
        self.closed = False
        self.remote_address = "memory:test"

    async def recv_text(self) -> Any:
        return await self._inbound.get()

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class TestRunConnectionKernel:
    @pytest.mark.asyncio
    async def test_ack_dispatch_and_eof_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

        server = AgentWebSocketServer.__new__(AgentWebSocketServer)
        server._agent_manager = SimpleNamespace(
            cancel_all_inflight_work=AsyncMock(return_value=None)
        )
        server._session_stream_tasks = {}
        server._current_ws = None
        server._current_send_lock = None
        server._acp_client_capabilities_by_ws = {}
        server._scheduler_service = None
        server._scheduler_agent = None
        server._ping_interval = 30.0
        server._ping_timeout = 300.0
        monkeypatch.setattr(
            "jiuwenswarm.agents.harness.team.cancel_all_team_stream_tasks_across_managers",
            AsyncMock(return_value=None),
        )

        handled: list[str] = []

        async def fake_handle_message(transport: Any, raw: Any, send_lock: Any) -> None:
            handled.append(raw)
            async with send_lock:
                await send_wire_payload(transport, {"echo": json.loads(raw)})

        server._handle_message = fake_handle_message  # type: ignore[method-assign]

        transport = _MemoryTransport([json.dumps({"request_id": "r1"})])
        run_task = asyncio.create_task(server.run_connection(transport))
        try:
            # 消息是 fire-and-forget 分发（create_task）——EOF 会取消在途任务，
            # 先等 echo 落定再喂 EOF（与真实连接「消息先于断连到达」的时序一致）
            for _ in range(500):
                if len(transport.sent) >= 2:
                    break
                await asyncio.sleep(0.01)
            # 首帧 = connection.ack，随后是 echo 响应
            assert json.loads(transport.sent[0]) == build_connection_ack_frame()
            assert json.loads(transport.sent[1]) == {"echo": {"request_id": "r1"}}
            assert handled == [json.dumps({"request_id": "r1"})]
            transport._inbound.put_nowait(None)  # 对端 EOF
            await asyncio.wait_for(run_task, timeout=5)
        finally:
            if not run_task.done():
                run_task.cancel()
                await asyncio.gather(run_task, return_exceptions=True)
        # EOF 断连清理：传输关闭 + _current_ws 复位
        assert transport.closed is True
        assert server._current_ws is None
        server._agent_manager.cancel_all_inflight_work.assert_awaited_once()
        gc.collect()
