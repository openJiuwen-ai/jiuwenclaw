# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""E2A 通道（客户端 ↔ AgentServer）的消息传输抽象与桌面形态实现。

背景（claw_desktop 仓 docs/named-pipe-migration-design.md §5.2）：E2A 通道从
loopback TCP（18592）迁到：
- stdio 匿名管道（桌面主进程 ↔ AgentServer）：句柄继承，第三方进程无法连接/
  枚举，是最强本地通道形态；
- 命名管道（gateway 兄弟进程 ↔ AgentServer）：stdio 无法连兄弟进程，管道名经
  密钥包 ``pipes.agentE2a`` 下发（SDDL 仅本人+SYSTEM、PID→镜像校验、auth 首帧
  三重防护由 e2a_desktop/serve_pipe 装配）。

形态判定契约（双仓同步）：走不走 stdio 由密钥包 ``e2aTransport`` 字段决定
（桌面按 CLAW_E2A_TRANSPORT 实际生效值下发），**不是**「密钥包存在与否」——
迁移期桌面默认仍是 WS，但密钥包在两种形态下都会下发（模型/上传/relay/cron
等通道的 np:// 分流各自按自己的配置 scheme 判定）；仅凭密钥包存在就停开
18592 会让默认 WS 形态的桌面客户端连不上。非桌面形态（无密钥包）与
``e2aTransport='ws'`` 形态保持 websockets 现状，零行为变化。

传输抽象（MessageTransport）：连接内核（agent_ws_server.run_connection）只依赖
``recv_text``/``send_text``/``close`` 三个方法；线格式与 WS 文本帧一致——
一条消息 = 一个 UTF-8 JSON 文本（管道/stdio 形态由长度前缀帧承载，帧规范见
common/np_transport.py，与桌面侧 src/core/net/length-prefix.ts 同一契约）。

本模块刻意保持轻依赖（np_transport / secrets_bootstrap / websockets），供
server（agent_ws_server）与 gateway（routing/agent_client）双侧复用——
不要在这里 import agent_ws_server（420KB+ 的重模块，会拖进 gateway 进程）。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import threading
from typing import IO, Any, Protocol, runtime_checkable

from websockets.exceptions import ConnectionClosed as WebSocketConnectionClosed

from jiuwenswarm.common.np_transport import (
    FRAME_MAX_BYTES,
    FrameCodecError,
    PipeClosedError,
    PipeError,
    PipeStream,
)
from jiuwenswarm.common.secrets_bootstrap import stdin_binary_stream

logger = logging.getLogger(__name__)

# 干净断连类异常（WS 形态 ConnectionClosed；管道/stdio 形态 PipeClosedError）。
# 连接内核统一按「对端关闭」处理（记录诊断后进断连清理）。
TRANSPORT_CLOSED_ERRORS = (WebSocketConnectionClosed, PipeClosedError)
# 帧协议/传输错误（帧超长/非 UTF-8/管道 I/O 错误）。注意 PipeClosedError ⊂ PipeError，
# 捕获顺序必须先 TRANSPORT_CLOSED_ERRORS、后本组。
TRANSPORT_PROTOCOL_ERRORS = (FrameCodecError, PipeError)


@runtime_checkable
class MessageTransport(Protocol):
    """一条 E2A 连接的消息传输接口（WS / 命名管道 / stdio 三形态共用）。"""

    async def recv_text(self) -> str | None:
        """收一条消息（JSON 文本）；对端干净关闭返回 None。"""
        ...

    async def send_text(self, data: str) -> None:
        """发一条消息（JSON 文本）；对端已关闭抛 TRANSPORT_CLOSED_ERRORS 系异常。"""
        ...

    async def close(self) -> None:
        """关闭传输（幂等）。"""
        ...


def encode_text_frame(data: str) -> bytes:
    """JSON 文本 → 长度前缀帧字节（与 np_transport.encode_frame 同帧规范，
    免去「先 json.loads 再 send_frame 重新 dumps」的往返——发送侧拿到的本来就是
    序列化好的文本）。"""
    body = data.encode("utf-8")
    if not body:
        raise FrameCodecError("帧负载为空")
    if len(body) > FRAME_MAX_BYTES:
        raise FrameCodecError(f"帧负载超长: {len(body)} > {FRAME_MAX_BYTES}")
    return len(body).to_bytes(4, "little") + body


def build_connection_ack_frame() -> dict[str, Any]:
    """connection.ack 首事件帧（三形态共用同一形状）。"""
    return {"type": "event", "event": "connection.ack", "payload": {"status": "ready"}}


def e2a_stdio_mode_enabled() -> bool:
    """E2A 是否走 stdio/管道形态：密钥包 ``e2aTransport == 'stdio'``。

    判定字段而非「密钥包存在」：桌面迁移期默认仍是 WS（CLAW_E2A_TRANSPORT
    控制），密钥包在两种形态下都会下发（其他通道的 np:// 分流各自按自己的
    配置判定）——仅凭密钥包存在就分流会让默认 WS 形态的桌面客户端连不上
    18592。判定失败按 False（WS 现状）处理。
    """
    try:
        from jiuwenswarm.common.secrets_bootstrap import get_secret, secrets_loaded

        return bool(secrets_loaded()) and get_secret("e2aTransport") == "stdio"
    except Exception:  # noqa: BLE001 - 防御：模块不可用时按 WS 形态
        return False


def verify_auth_frame(frame: Any, expected_token: str) -> bool:
    """首帧 auth 校验：``{"type":"auth","token":...}``，常量时间比对。

    expected_token 为空（密钥包未携带 e2aToken）时一律拒绝——桌面形态下
    e2aToken 由桌面主进程生成并随密钥包下发，缺失即形态异常。
    """
    token = (
        frame.get("token")
        if isinstance(frame, dict) and frame.get("type") == "auth"
        else None
    )
    if not expected_token or not isinstance(token, str):
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8"))


class WsMessageTransport:
    """websockets 连接的 MessageTransport 皮（非桌面形态，行为与改造前完全一致）。

    收：复用 ws 的异步迭代协议（``async for raw in ws`` 的等价拆解——干净关闭
    StopAsyncIteration → None；异常关闭 ConnectionClosed 原样上抛，保留
    describe_ws_exception 诊断语义）。
    发：``ws.send(str)`` 原样透传。
    其余属性（remote_address/local_address/state/closed 等诊断字段）鸭子委托
    底层 ws，describe_ws_peer 等 getattr 访问零适配。
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._iter: Any = None

    def __getattr__(self, name: str) -> Any:
        ws = self.__dict__.get("_ws")
        if ws is None:
            raise AttributeError(name)
        return getattr(ws, name)

    async def recv_text(self) -> str | None:
        if self._iter is None:
            self._iter = self._ws.__aiter__()
        try:
            return await self._iter.__anext__()
        except StopAsyncIteration:
            return None

    async def send_text(self, data: str) -> None:
        await self._ws.send(data)

    async def close(self) -> None:
        await self._ws.close()


class PipeMessageTransport:
    """命名管道 PipeStream 的 MessageTransport 皮（gateway 兄弟侧 E2A 通道）。

    帧负载即 JSON 文本：recv_frame 的解析结果 re-serialize 交付（np_transport 的
    API 固定返回解析后对象，与 web_connect._PipeClientAdapter 同形态）；send_text
    直写长度前缀帧字节（发送侧拿到的是已序列化文本，不再 parse+dump 往返）。

    管道可靠有序、断管即断连，无 ping/pong（保活由应用层心跳承担）。
    PipeStream 为 FILE_FLAG_OVERLAPPED 全双工句柄——驻留读与并发写不互斥
    （非 overlapped 句柄上挂起 ReadFile 会串行化阻塞同句柄 WriteFile，已修过的坑）。
    """

    def __init__(self, stream: PipeStream, *, remote: str = "named-pipe:agent-e2a") -> None:
        self._stream = stream
        # 日志/诊断的对端标识（管道无 remote_address 概念）
        self.remote_address = remote

    @property
    def closed(self) -> bool:
        return self._stream.closed

    async def recv_text(self) -> str | None:
        try:
            frame = await self._stream.recv_frame()
        except PipeClosedError:
            return None
        return json.dumps(frame, ensure_ascii=False)

    async def send_text(self, data: str) -> None:
        await self._stream.write(encode_text_frame(data))

    async def close(self) -> None:
        await self._stream.close()


def _read_exact_blocking(stream: IO[bytes], size: int) -> bytes | None:
    """同步读满 size 字节；任何 EOF（含半帧）返回 None。"""
    parts: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


class StdioMessageTransport:
    """桌面主进程 ↔ AgentServer 的 stdio 长度前缀帧传输。

    读：守护读线程复用 ``secrets_bootstrap.stdin_binary_stream()``——进程内唯一
    stdin 二进制 reader（密钥包已消费首帧；BufferedReader 预读字节只进该 reader
    的缓冲区，另开 reader 必丢字节）。帧文本经 call_soon_threadsafe 投进 asyncio
    队列。刻意用 daemon 线程而非 asyncio.to_thread：to_thread 的 worker 非
    daemon，interpreter exit 时 ThreadPoolExecutor 会 join 它们——stdin 阻塞读
    永远不返回会让进程退不掉。
    写：``os.write(stdout_fd)`` 直写 fd 1，绕过 sys.stdout 的任何缓冲/重绑定
    （stdout 只承载帧；日志/print 已由 redirect_stdout_to_stderr 改道 stderr）。
    写阻塞走 to_thread（pipe 缓冲满时不堵事件循环）。
    """

    def __init__(self, stdin: IO[bytes] | None = None, *, stdout_fd: int = 1) -> None:
        self._stdin = stdin if stdin is not None else stdin_binary_stream()
        self._stdout_fd = stdout_fd
        self._closed = False
        self._reader_started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        # 日志/诊断的对端标识（匿名管道无 remote_address 概念）
        self.remote_address = "stdio:desktop"

    @property
    def closed(self) -> bool:
        return self._closed

    def _publish(self, item: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except RuntimeError:
            pass  # 事件循环关闭竞态（进程退出中）

    def _reader_main(self) -> None:
        try:
            while not self._closed:
                header = _read_exact_blocking(self._stdin, 4)
                if header is None:
                    self._publish(None)  # EOF：桌面关闭了管道（进程退出信号）
                    return
                length = int.from_bytes(header, "little")
                if length == 0 or length > FRAME_MAX_BYTES:
                    self._publish(FrameCodecError(f"stdio 帧长度非法: {length}"))
                    return
                body = _read_exact_blocking(self._stdin, length)
                if body is None:
                    self._publish(None)  # 半帧 EOF：桌面进程已消失
                    return
                try:
                    self._publish(body.decode("utf-8"))
                except UnicodeDecodeError as exc:
                    self._publish(FrameCodecError(f"stdio 帧负载非 UTF-8: {exc}"))
                    return
        except Exception as exc:  # noqa: BLE001 - 读线程兜底：异常上抛给 recv_text
            self._publish(exc)

    def _ensure_reader(self) -> None:
        if self._reader_started:
            return
        self._reader_started = True
        self._loop = asyncio.get_running_loop()
        threading.Thread(
            target=self._reader_main, name="e2a-stdio-read", daemon=True
        ).start()

    async def recv_text(self) -> str | None:
        if self._closed:
            return None
        self._ensure_reader()
        item = await self._queue.get()
        if item is None:
            self._closed = True
            return None
        if isinstance(item, BaseException):
            self._closed = True
            raise item
        return item

    async def send_text(self, data: str) -> None:
        if self._closed:
            raise PipeClosedError("stdio 传输已关闭，无法发送")
        frame = encode_text_frame(data)
        fd = self._stdout_fd

        def _write_all() -> None:
            view = memoryview(frame)
            while len(view) > 0:
                view = view[os.write(fd, view) :]

        try:
            await asyncio.to_thread(_write_all)
        except OSError as exc:  # BrokenPipeError 等：桌面已关闭 stdout 管道
            self._closed = True
            raise PipeClosedError(f"stdio 写入失败（桌面管道已关闭）: {exc}") from exc

    async def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------------------
# stdio 形态的前置：stdout 改道 stderr
# ---------------------------------------------------------------------------

# exe_entry._ensure_stdio() 在桌面 spawn 下会把 sys.stdout 绑成 fd 1 的
# TextIOWrapper（open(1, "w")）——换绑时必须保留其引用，否则 GC 连带 close fd 1，
# 帧通道即毁。
_HELD_STDOUT: Any = None


def redirect_stdout_to_stderr() -> None:
    """stdio 帧形态下把 sys.stdout 改道 sys.stderr（fd 1 只承载协议帧）。幂等。

    覆盖的污染面：
    - ``print()`` / ``sys.stdout.write``（默认走 sys.stdout）；
    - openjiuwen 日志的 console 输出——default 后端
      ``logging.StreamHandler(stream=sys.stdout)``、loguru 后端 console sink
      （target=stdout）都在 logger/sink 创建时捕获 sys.stdout 对象，故本函数
      必须尽早调用（exe_entry 在分发 app_agentserver 之前已调用；e2a_desktop
      启动通道时幂等再调一次兜底）。
    - 桌面侧 spawnRuntime 把子进程 stderr 转发进应用日志，排障能力不损失。
    """
    global _HELD_STDOUT
    target = sys.stderr
    if target is None or getattr(target, "closed", False):
        return  # stderr 也不可用的极端形态：不动（避免引入新的崩溃面）
    if sys.stdout is target:
        return
    _HELD_STDOUT = sys.stdout  # 保留引用：防 fd 1 包装器被 GC 连带 close
    sys.stdout = target
