# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""xiaoyi 渠道本地中转 np:// 命名管道形态单测（WS → 命名管道迁移的 jiuwen 侧）。

协议契约（与 claw_desktop cloud-ws-relay 管道 server 同一规范）：
长度前缀帧（np_transport FrameCodec）；首帧必须是鉴权帧
{"type":"auth","agentId":...,"ak":...,"ts":...,"sign":...}
（WS 握手头 x-agent-id/x-access-key/x-ts/x-sign 的下沉形态，
sign = base64(HMAC-SHA256(sk, ts))，ts 为毫秒时间戳字符串）；
之后每帧 = 一条原 WS 文本消息（JSON 对象），下行同样逐帧下发。

覆盖：
  - np:// 连接成功：首帧 auth 字段与签名正确；clawd_bot_init/heartbeat 由应用层
    （桌面客户端 cloud-ws-relay）统一发送，channel 层连接后不再构造任何协议帧
  - 业务帧 roundtrip：_safe_ws_send 发 dict 帧 + 下行帧进 _handle_raw_message
  - 错误签名被服务端断管 → 客户端观测断连、重连循环存活（5s 退避语义平移）
  - ak/sk/agentId 来源：密钥包 vault 优先，回退渠道配置（兼容旧桌面）
  - ws:// 形态回归：仍走 websockets.connect + 握手头鉴权，无管道首帧、无 init/heartbeat
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time

import pytest

import jiuwenswarm.common.secrets_bootstrap as secrets_bootstrap
import jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect as xiaoyi_connect
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    XiaoyiChannel,
    XiaoyiChannelConfig,
    _generate_pipe_auth_frame,
)

TEST_AK = "ak-live"
TEST_SK = "sk-live"
TEST_AGENT_ID = "agent-1"


def _sign(sk: str, ts: str) -> str:
    return base64.b64encode(
        hmac.new(sk.encode("utf-8"), ts.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")


async def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _make_channel(**config_overrides) -> XiaoyiChannel:
    config_kwargs = dict(
        enabled=True,
        channel_id="xiaoyi",
        ak=TEST_AK,
        sk=TEST_SK,
        agent_id=TEST_AGENT_ID,
    )
    config_kwargs.update(config_overrides)
    channel = XiaoyiChannel(XiaoyiChannelConfig(**config_kwargs), None)
    channel._running = True
    return channel


async def _shutdown(channel: XiaoyiChannel, task: "asyncio.Task | None") -> None:
    channel._running = False
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class _FakeRelay:
    """serve_pipe 上的假桌面中转：首帧验签（与桌面 verifyLocalAuthFields 同口径）。

    验签失败直接断管（桌面 socket.destroy 语义）；验签通过后可按需
    立即下发一帧（push_on_auth）并/或 echo 业务帧。
    """

    def __init__(self, *, echo: bool = False, push_on_auth: dict | None = None) -> None:
        self.echo = echo
        self.push_on_auth = push_on_auth
        self.path = rf"\\.\pipe\claw-test-relay-{os.getpid()}-{id(self) & 0xFFFFF:x}"
        self.connections = 0
        self.auth_frames: list = []
        self.frames: list = []  # 验签通过后收到的业务帧
        self.server = None

    @property
    def url(self) -> str:
        return "np://" + self.path.rsplit("\\", 1)[-1]

    async def __aenter__(self) -> "_FakeRelay":
        from jiuwenswarm.common.np_transport import serve_pipe

        self.server = await serve_pipe(self.path, self._handle)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.server.stop()

    def _verify(self, frame) -> bool:
        if not isinstance(frame, dict) or frame.get("type") != "auth":
            return False
        if frame.get("agentId") != TEST_AGENT_ID or frame.get("ak") != TEST_AK:
            return False
        ts = frame.get("ts")
        sign = frame.get("sign")
        if not isinstance(ts, str) or not ts.isdigit():
            return False
        if abs(int(time.time() * 1000) - int(ts)) > 5 * 60 * 1000:
            return False
        return hmac.compare_digest(_sign(TEST_SK, ts), str(sign))

    async def _handle(self, stream) -> None:
        self.connections += 1
        try:
            first = await stream.recv_frame()
        except Exception:
            return
        self.auth_frames.append(first)
        if not self._verify(first):
            return  # 验签失败：直接断管
        if self.push_on_auth is not None:
            await stream.send_frame(self.push_on_auth)
        while True:
            try:
                frame = await stream.recv_frame()
            except Exception:
                return
            self.frames.append(frame)
            if self.echo:
                await stream.send_frame(frame)


pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="命名管道仅 Windows"),
    # 本文件的用例全部不走 TCP；pytest unraisable 会把「别的测试（如 web 管道广播用例的
    # WS server accept/close 竞态）遗留对象在 GC 时的 ResourceWarning」归到本文件下一个
    # 运行的测试头上——与本文件被测逻辑无关，免疫之。
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
]


@pytest.mark.skipif(sys.platform != "win32", reason="命名管道仅 Windows")
class TestXiaoyiRelayPipe:
    @pytest.mark.asyncio
    async def test_connect_sends_auth_frame_only(self) -> None:
        async with _FakeRelay() as relay:
            channel = _make_channel(ws_url1=relay.url)
            task = asyncio.create_task(channel._connect("ws_url1", relay.url))
            try:
                assert await _wait_until(lambda: len(relay.auth_frames) >= 1)
                # 首帧 = 鉴权帧（WS 握手头下沉形态）
                auth = relay.auth_frames[0]
                assert auth["type"] == "auth"
                assert auth["agentId"] == TEST_AGENT_ID
                assert auth["ak"] == TEST_AK
                assert isinstance(auth["ts"], str) and auth["ts"].isdigit()
                assert abs(int(time.time() * 1000) - int(auth["ts"])) < 60_000
                assert auth["sign"] == _sign(TEST_SK, auth["ts"])
                assert channel.is_ready
                # 连接稳定后 channel 层不发任何协议帧：clawd_bot_init/heartbeat
                # 统一由应用层（桌面客户端 cloud-ws-relay）构造发送
                await asyncio.sleep(0.5)
                assert relay.frames == []
            finally:
                await _shutdown(channel, task)

    @pytest.mark.asyncio
    async def test_business_frames_roundtrip(self) -> None:
        push = {"msgType": "server_push", "sessionId": "s-1", "text": "下行帧"}
        async with _FakeRelay(echo=True, push_on_auth=push) as relay:
            channel = _make_channel(ws_url1=relay.url)
            received: list = []

            async def _capture(raw, url_key=None):
                received.append(raw)

            channel._handle_raw_message = _capture
            task = asyncio.create_task(channel._connect("ws_url1", relay.url))
            try:
                # 连接就绪（channel 层不再发 init/heartbeat，无连接即发的业务帧）
                assert await _wait_until(lambda: channel.is_ready)
                # 上行：_safe_ws_send 直接发 dict 帧，服务端收到同形 JSON 对象
                payload = {"msgType": "custom_biz", "text": "你好管道", "n": 7}
                await channel._safe_ws_send("ws_url1", payload)
                assert await _wait_until(lambda: payload in relay.frames)
                # 下行 echo：帧负载还原为 JSON 文本进 _handle_raw_message
                assert await _wait_until(lambda: any(
                    isinstance(r, str) and json.loads(r) == payload for r in received
                ))
                # 服务端主动下行帧同样送达
                assert any(
                    isinstance(r, str) and json.loads(r) == push for r in received
                )
            finally:
                await _shutdown(channel, task)

    @pytest.mark.asyncio
    async def test_bad_signature_disconnects_and_reconnect_survives(self) -> None:
        # 客户端持有错误 sk：中转侧验签失败 → 断管
        async with _FakeRelay() as relay:
            channel = _make_channel(ws_url1=relay.url, sk="sk-WRONG")
            task = asyncio.create_task(channel._reconnect_loop("ws_url1", relay.url))
            try:
                # 首帧已送达（连接必然已建立并注册过）
                assert await _wait_until(lambda: len(relay.auth_frames) >= 1)
                # 服务端验签失败断管 → 客户端观测到断连（连接槽位清空）
                assert await _wait_until(lambda: channel._ws_connections.get("ws_url1") is None)
                # 重连循环不抛死，仍在 5s 退避中等待下一轮
                assert not task.done()
                # 验签失败前连接未被中转当作已鉴权连接处理业务帧
                assert relay.frames == []
                bad = relay.auth_frames[0]
                assert bad["type"] == "auth"
                assert bad["sign"] == _sign("sk-WRONG", bad["ts"])
            finally:
                await _shutdown(channel, task)


class TestPipeAuthFrameCredentials:
    """np:// 形态 ak/sk/agentId 来源：密钥包 vault 优先，配置兜底。"""

    def test_prefers_vault_secrets(self, monkeypatch) -> None:
        monkeypatch.setattr(
            secrets_bootstrap,
            "_SECRETS",
            {"localAuth": {"ak": "v-ak", "sk": "v-sk", "agentId": "v-agent"}},
        )
        config = XiaoyiChannelConfig(ak="c-ak", sk="c-sk", agent_id="c-agent")
        frame = _generate_pipe_auth_frame(config)
        assert frame["type"] == "auth"
        assert frame["ak"] == "v-ak"
        assert frame["agentId"] == "v-agent"
        assert frame["ts"].isdigit()
        assert frame["sign"] == _sign("v-sk", frame["ts"])

    def test_falls_back_to_config(self, monkeypatch) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {})
        config = XiaoyiChannelConfig(ak="c-ak", sk="c-sk", agent_id="c-agent")
        frame = _generate_pipe_auth_frame(config)
        assert frame["ak"] == "c-ak"
        assert frame["agentId"] == "c-agent"
        assert frame["sign"] == _sign("c-sk", frame["ts"])

    def test_partial_vault_falls_back_per_field(self, monkeypatch) -> None:
        monkeypatch.setattr(
            secrets_bootstrap, "_SECRETS", {"localAuth": {"sk": "v-sk"}}
        )
        config = XiaoyiChannelConfig(ak="c-ak", sk="c-sk", agent_id="c-agent")
        frame = _generate_pipe_auth_frame(config)
        assert frame["ak"] == "c-ak"
        assert frame["agentId"] == "c-agent"
        assert frame["sign"] == _sign("v-sk", frame["ts"])


class TestWsFormUnchanged:
    """ws:// 形态回归：握手头鉴权 + 无管道首帧，行为不变。"""

    @pytest.mark.asyncio
    async def test_ws_connect_uses_headers_and_no_auth_frame(self, monkeypatch) -> None:
        import websockets

        sent: list[str] = []

        class _FakeWsConn:
            async def send(self, data):
                sent.append(data)

            async def close(self):
                pass

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                return
                yield ""  # pragma: no cover

        captured: dict = {}

        def _fake_connect(url, additional_headers=None, **kwargs):
            captured["url"] = url
            captured["headers"] = additional_headers
            conn = _FakeWsConn()

            class _Ctx:
                async def __aenter__(self):
                    return conn

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

        monkeypatch.setattr(websockets, "connect", _fake_connect)

        def _forbidden_open_pipe(*args, **kwargs):  # pragma: no cover - 命中即失败
            raise AssertionError("ws:// 形态不得走命名管道")

        monkeypatch.setattr(xiaoyi_connect, "open_pipe", _forbidden_open_pipe)

        ws_url = "ws://127.0.0.1:19690"
        channel = _make_channel(ws_url1=ws_url)
        try:
            await channel._connect("ws_url1", ws_url)

            assert captured["url"] == ws_url
            headers = captured["headers"]
            assert headers["x-access-key"] == TEST_AK
            assert headers["x-agent-id"] == TEST_AGENT_ID
            assert headers["x-ts"].isdigit()
            assert headers["x-sign"] == _sign(TEST_SK, headers["x-ts"])
            # channel 层不再发送 init/heartbeat（由应用层 cloud-ws-relay 统一构造）：
            # 建链后无任何上行消息
            assert sent == []
            # 连接走完后清理连接槽位
            assert channel._ws_connections.get("ws_url1") is None
        finally:
            await _shutdown(channel, None)
