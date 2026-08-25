# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Windows 命名管道传输（桌面集成形态的本地通道，替代 loopback TCP）。

帧规范（与 claw_desktop 侧 src/core/net/length-prefix.ts 同一契约，改动须双仓同步）：
    4 字节小端无符号长度前缀 + UTF-8 JSON 负载；单帧上限 FRAME_MAX_BYTES = 8 MiB
    （对齐 common/ws_limits.py 的 AGENT_WS_MAX_MESSAGE_BYTES 语义）。

能力：
  - FrameCodec：长度前缀帧编解码（半包/粘包/超限处理），stdio 与管道通道共用
  - open_pipe()：客户端连接（WaitNamedPipe 重试 + CreateFile）
  - serve_pipe()：服务端（CreateNamedPipe 多实例；SDDL 收紧到仅本人+SYSTEM；
    可选 verify_client 对端进程身份校验——GetNamedPipeClientProcessId → 镜像路径白名单）
  - NamedPipeTransport / NamedPipeSyncTransport：httpx transport，
    把 HTTP/1.1 字节流原样过管道（模型代理/上传代理/专家仓库三条 HTTP 通道复用）

安全语义（docs/named-pipe-migration-design.md §2，claw_desktop 仓）：
  管道 ACL 挡跨用户；verify_client 挡同用户仿冒进程；应用层令牌（首帧 auth）
  为独立一层，密钥经 stdin 密钥包下发（secrets_bootstrap），不落 env/命令行。
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterator
from typing import Any

import httpx

if sys.platform == "win32":
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32file
    import win32pipe
    import win32process
    import win32security
    import winerror

# ---------------------------------------------------------------------------
# np:// URL 约定（桌面注入配置的统一形态：np://<管道名>/<http 路径前缀>）
# ---------------------------------------------------------------------------


def is_named_pipe_url(url: str | None) -> bool:
    """URL 是否为命名管道形态（np://claw-model/v1）。"""
    return bool(url) and url.lower().startswith("np://")


def pipe_path_from_url(url: str) -> str:
    """np://<管道名>/<...> → \\\\.\\pipe\\<管道名>（管道名为 URL authority 段）。"""
    if not is_named_pipe_url(url):
        raise ValueError(f"非 np:// URL: {url}")
    rest = url[5:]  # 去掉 np://
    name = rest.split("/", 1)[0].strip()
    if not name:
        raise ValueError(f"np:// URL 缺少管道名: {url}")
    return "\\\\.\\pipe\\" + name


def named_pipe_transport_for(base_url: str, **kwargs: Any) -> "NamedPipeTransport":
    """按 np:// base_url 构造异步 transport（httpx.AsyncClient(transport=...) 注入用）。

    URL 的 path 前缀由 httpx base_url 拼接语义保留在请求路径里（raw_path 原样过管道），
    transport 只关心 authority 段（管道名）。
    """
    return NamedPipeTransport(pipe_path_from_url(base_url), **kwargs)


def named_pipe_sync_transport_for(base_url: str, **kwargs: Any) -> "NamedPipeSyncTransport":
    """np:// base_url 的同步 transport（同步 OpenAI 客户端等旁路用）。"""
    return NamedPipeSyncTransport(pipe_path_from_url(base_url), **kwargs)


# ---------------------------------------------------------------------------
# 帧编解码（与 claw_desktop src/core/net/length-prefix.ts 同规范）
# ---------------------------------------------------------------------------

FRAME_MAX_BYTES = 8 * 2**20  # 8 MiB，对齐 AGENT_WS_MAX_MESSAGE_BYTES


class FrameCodecError(Exception):
    """帧协议错误（超长/零长度/非法 JSON）。"""


def encode_frame(payload: Any) -> bytes:
    """编码一帧：JSON 序列化 + 4 字节小端长度前缀。超长抛 FrameCodecError。"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not body:
        raise FrameCodecError("帧负载为空（JSON 序列化异常）")
    if len(body) > FRAME_MAX_BYTES:
        raise FrameCodecError(f"帧负载超长: {len(body)} > {FRAME_MAX_BYTES}")
    return len(body).to_bytes(4, "little") + body


class FrameDecoder:
    """增量解码器：feed 任意字节流切片，返回本轮完整解出的帧（0~N 个）。"""

    def __init__(self, max_bytes: int = FRAME_MAX_BYTES) -> None:
        self._buf = bytearray()
        self._max = max_bytes

    def feed(self, chunk: bytes) -> list[Any]:
        self._buf.extend(chunk)
        out: list[Any] = []
        while True:
            if len(self._buf) < 4:
                break
            length = int.from_bytes(self._buf[:4], "little")
            if length == 0:
                raise FrameCodecError("帧长度为 0（协议错误）")
            if length > self._max:
                raise FrameCodecError(f"帧负载超长: {length} > {self._max}")
            if len(self._buf) < 4 + length:
                break  # 半包：等更多数据
            body = bytes(self._buf[4 : 4 + length])
            del self._buf[: 4 + length]
            try:
                out.append(json.loads(body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FrameCodecError(f"帧负载非合法 JSON（len={length}）: {exc}") from exc
        return out

    @property
    def pending_bytes(self) -> int:
        """尚未成帧的残留字节数（诊断用）。"""
        return len(self._buf)


# ---------------------------------------------------------------------------
# 管道字节流（客户端/服务端共用）
# ---------------------------------------------------------------------------


class PipeError(Exception):
    """管道传输错误基类。"""


class PipeClosedError(PipeError):
    """对端已关闭（ERROR_BROKEN_PIPE / ERROR_NO_DATA 等）。"""


class PipeTimeoutError(PipeError):
    """连接/读取超时。"""


_CLOSED_WINERRORS = {
    winerror.ERROR_BROKEN_PIPE,
    winerror.ERROR_PIPE_NOT_CONNECTED,
    winerror.ERROR_NO_DATA,
    winerror.ERROR_INVALID_HANDLE,
    winerror.ERROR_OPERATION_ABORTED,
} if sys.platform == "win32" else set()


def _map_pipe_error(exc: BaseException) -> PipeError:
    if isinstance(exc, pywintypes.error) and exc.winerror in _CLOSED_WINERRORS:
        return PipeClosedError(str(exc))
    return PipeError(str(exc))


# ---------------------------------------------------------------------------
# Overlapped I/O 基元（关键设计决策）
#
# 为什么必须 FILE_FLAG_OVERLAPPED：Windows 对**非 overlapped** 句柄上的同步
# ReadFile/WriteFile/FlushFileBuffers 做句柄级串行化——一个挂起的 ReadFile 会
# 阻塞同句柄上的并发 WriteFile（以及 FlushFileBuffers）。帧通道是全双工的
# （server 常驻读等下一帧、同时可能要写），非 overlapped 必然死锁
# （实测：服务端 recv 挂起时 send ack 永久阻塞）。overlapped 后读写真并发，
# 且 CancelIoEx 可精确取消挂起操作（close 不再依赖跨线程 CloseHandle 的竞态）。
# ---------------------------------------------------------------------------


def _cancel_all_overlapped_io(handle: int) -> None:
    """CancelIoEx(handle, NULL)：取消该句柄上全部挂起的 overlapped I/O。

    pywin32 311 只封装了 CancelIo（仅取消调用线程发起的 I/O），跨线程取消
    需要 CancelIoEx——ctypes 直调 kernel32（pywin32 能力缺口补丁，无新依赖）。
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.CancelIoEx(ctypes.c_void_p(handle), None)
    except Exception:  # noqa: BLE001
        pass


def _read_overlapped(handle: int, size: int) -> bytes:
    """overlapped 读（阻塞至有数据/出错/被取消）；对端关闭返回 b''。"""
    buf = win32file.AllocateReadBuffer(size)
    ov = pywintypes.OVERLAPPED()
    ov.hEvent = win32event.CreateEvent(None, True, False, None)
    try:
        hr, _ = win32file.ReadFile(handle, buf, ov)
        if hr == 0:
            # 立即完成
            n = win32file.GetOverlappedResult(handle, ov, True)
            return bytes(buf[:n])
        if hr != winerror.ERROR_IO_PENDING:
            raise _map_pipe_error(pywintypes.error(hr, "ReadFile", "overlapped 启动失败"))
        win32event.WaitForSingleObject(ov.hEvent, win32event.INFINITE)
        try:
            n = win32file.GetOverlappedResult(handle, ov, True)
        except pywintypes.error as exc:
            if exc.winerror in _CLOSED_WINERRORS:
                return b""
            raise _map_pipe_error(exc) from exc
        return bytes(buf[:n])
    except pywintypes.error as exc:
        if exc.winerror in _CLOSED_WINERRORS:
            return b""
        raise _map_pipe_error(exc) from exc
    finally:
        ov.hEvent.Close()


def _write_overlapped(handle: int, data: bytes | memoryview) -> int:
    """overlapped 写；返回写入字节数。对端关闭抛 PipeClosedError。"""
    ov = pywintypes.OVERLAPPED()
    ov.hEvent = win32event.CreateEvent(None, True, False, None)
    try:
        hr, written = win32file.WriteFile(handle, data, ov)
        if hr == 0:
            return int(written)
        if hr != winerror.ERROR_IO_PENDING:
            raise _map_pipe_error(pywintypes.error(hr, "WriteFile", "overlapped 启动失败"))
        win32event.WaitForSingleObject(ov.hEvent, win32event.INFINITE)
        try:
            return int(win32file.GetOverlappedResult(handle, ov, True))
        except pywintypes.error as exc:
            raise _map_pipe_error(exc) from exc
    except pywintypes.error as exc:
        raise _map_pipe_error(exc) from exc
    finally:
        ov.hEvent.Close()


class PipeStream:
    """一条已建立的全双工管道连接（overlapped 字节模式），asyncio 包装
    （win32 I/O 走 to_thread 阻塞等待事件）。

    句柄必须以 FILE_FLAG_OVERLAPPED 打开（open_pipe/serve_pipe 均已保证）——
    非 overlapped 句柄上挂起的 ReadFile 会串行化并阻塞同句柄的 WriteFile，
    全双工场景必然死锁。

    附加帧便捷接口 send_frame/recv_frame（长度前缀 JSON，跨连接缓存帧级残留）。
    """

    def __init__(self, handle: int, *, read_chunk: int = 65536) -> None:
        self._handle = handle
        self._read_chunk = read_chunk
        self._closed = False
        self._decoder = FrameDecoder()
        self._pending_frames: deque[Any] = deque()

    @property
    def closed(self) -> bool:
        return self._closed

    async def read(self, size: int | None = None, *, timeout: float | None = None) -> bytes:
        """读至多 size 字节（有数据即返回）；对端关闭返回 b''。

        注意（契约）：timeout 触发后底层读线程仍挂在管道上（Windows 无法安全
        中途取消已开始拷贝的用户缓冲），此后到达的字节会被孤儿读消费——
        超时后该 stream 只能 close()，不得继续读（当前调用点均为「超时即关闭」）。
        """
        if self._closed:
            return b""

        def _read() -> bytes:
            return _read_overlapped(self._handle, size or self._read_chunk)

        try:
            if timeout is None:
                return await asyncio.to_thread(_read)
            return await asyncio.wait_for(asyncio.to_thread(_read), timeout)
        except asyncio.TimeoutError as exc:
            raise PipeTimeoutError(f"管道读取超时（{timeout}s）") from exc

    async def read_exactly(self, size: int, *, timeout: float | None = None) -> bytes:
        """读满 size 字节；提前关闭抛 PipeClosedError。"""
        parts: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = await self.read(remaining, timeout=timeout)
            if not chunk:
                raise PipeClosedError(f"管道提前关闭（缺 {remaining}/{size} 字节）")
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    async def write(self, data: bytes) -> None:
        """全量写入（循环处理短写）；对端关闭抛 PipeClosedError。"""
        if self._closed:
            raise PipeClosedError("管道已关闭，无法写入")
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            offset += await asyncio.to_thread(_write_overlapped, self._handle, view[offset:])

    async def send_frame(self, payload: Any) -> None:
        await self.write(encode_frame(payload))

    async def recv_frame(self, *, timeout: float | None = None) -> Any:
        """读一条完整帧（连接内缓存跨帧残留）。"""
        while not self._pending_frames:
            chunk = await self.read(timeout=timeout)
            if not chunk:
                raise PipeClosedError("管道已关闭，无完整帧可读")
            for frame in self._decoder.feed(chunk):
                self._pending_frames.append(frame)
        return self._pending_frames.popleft()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        def _close() -> None:
            # 先取消挂起的 overlapped I/O（解除其他线程的读写阻塞），再收尾句柄
            _cancel_all_overlapped_io(self._handle)
            try:
                win32file.FlushFileBuffers(self._handle)  # 等对端读完缓冲（overlapped 下不串行化）
            except pywintypes.error:
                pass
            try:
                win32pipe.DisconnectNamedPipe(self._handle)
            except pywintypes.error:
                pass
            try:
                win32file.CloseHandle(self._handle)
            except pywintypes.error:
                pass

        await asyncio.to_thread(_close)


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------


def open_pipe_sync(path: str, *, timeout: float = 10.0) -> "PipeStreamSync":
    """连接命名管道（同步形态，供 httpx 同步 transport / 非异步调用点使用）。

    同步形态按「写请求→读响应」严格串行使用，无需 overlapped。
    """
    handle = _connect_handle(path, timeout=timeout, overlapped=False)
    return PipeStreamSync(handle)


async def open_pipe(path: str, *, timeout: float = 10.0) -> PipeStream:
    """连接命名管道（异步形态，FILE_FLAG_OVERLAPPED——全双工并发读写的前提）。
    服务端未起/实例忙时重试至 timeout。"""
    handle = await asyncio.to_thread(_connect_handle, path, timeout=timeout, overlapped=True)
    return PipeStream(handle)


def _connect_handle(path: str, *, timeout: float, overlapped: bool) -> int:
    deadline = time.monotonic() + timeout
    flags = win32file.FILE_FLAG_OVERLAPPED if overlapped else 0
    while True:
        try:
            return win32file.CreateFile(
                path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                flags,
                None,
            )
        except pywintypes.error as exc:
            # ERROR_PIPE_BUSY：所有实例忙；ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND：
            # 服务端尚未创建（accept 线程竞态）——重试至 timeout
            if exc.winerror not in (
                winerror.ERROR_PIPE_BUSY,
                winerror.ERROR_FILE_NOT_FOUND,
                winerror.ERROR_PATH_NOT_FOUND,
            ):
                raise _map_pipe_error(exc) from exc
            if time.monotonic() >= deadline:
                raise PipeTimeoutError(f"连接命名管道超时（{timeout}s）：{path}") from exc
            time.sleep(0.05)


class PipeStreamSync:
    """PipeStream 的同步（阻塞）形态：供 httpx 同步 transport 等场景。"""

    def __init__(self, handle: int, *, read_chunk: int = 65536) -> None:
        self._handle = handle
        self._read_chunk = read_chunk
        self._closed = False

    def read(self, size: int | None = None) -> bytes:
        if self._closed:
            return b""
        try:
            _hr, data = win32file.ReadFile(self._handle, size or self._read_chunk)
            return bytes(data)
        except pywintypes.error as exc:
            if exc.winerror in _CLOSED_WINERRORS:
                return b""
            raise _map_pipe_error(exc) from exc

    def read_exactly(self, size: int) -> bytes:
        parts: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self.read(remaining)
            if not chunk:
                raise PipeClosedError(f"管道提前关闭（缺 {remaining}/{size} 字节）")
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            try:
                _hr, written = win32file.WriteFile(self._handle, view[offset:])
            except pywintypes.error as exc:
                raise _map_pipe_error(exc) from exc
            offset += int(written)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for close_step in (
            win32file.FlushFileBuffers,
            win32pipe.DisconnectNamedPipe,
            win32file.CloseHandle,
        ):
            try:
                close_step(self._handle)
            except pywintypes.error:
                pass


# ---------------------------------------------------------------------------
# 服务端（SDDL 收紧 + 可选对端进程身份校验）
# ---------------------------------------------------------------------------


def current_user_sid() -> str:
    """当前进程用户的字符串 SID（构造管道 SDDL 用）。"""
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    return win32security.ConvertSidToStringSid(sid)


def default_pipe_sddl() -> str:
    """默认管道 SDDL：仅当前用户与 SYSTEM 完全控制（D:P 保护 DACL，不继承父对象）。

    跨用户进程（含其他登录会话）连打开管道都会被内核拒绝；同用户进程由
    verify_client（PID→镜像路径）与应用层令牌继续防。
    """
    return f"D:P(A;;GA;;;{current_user_sid()})(A;;GA;;;SY)"


def _security_attributes(sddl: str) -> "pywintypes.SECURITY_ATTRIBUTES":
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = descriptor
    attrs.bInheritHandle = 0
    return attrs


def _close_handle_silently(handle: int | None) -> None:
    if handle is None:
        return
    try:
        win32file.CloseHandle(handle)
    except pywintypes.error:
        pass


def make_image_verifier(allowed_image_paths: Collection[str]) -> Callable[[int], bool]:
    """构造对端进程身份校验器：PID → 进程镜像完整路径，必须在白名单内（大小写/分隔符归一）。

    与「spawn 前 exe 验签」叠加构成闭环：exe 本身可信（启动时验签）→ 连接者确为该 exe。
    """
    normalized = {os.path.normcase(os.path.normpath(p)) for p in allowed_image_paths}

    def verify(pid: int) -> bool:
        try:
            # GetModuleFileNameEx（PSAPI）需要 QUERY_INFORMATION|VM_READ；
            # pywin32 311 未封装 QueryFullProcessImageName
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
            )
            try:
                image = win32process.GetModuleFileNameEx(handle, 0)
            finally:
                win32api.CloseHandle(handle)
        except (pywintypes.error, OSError):
            return False
        return os.path.normcase(os.path.normpath(image)) in normalized

    return verify


ConnectionHandler = Callable[[PipeStream], Awaitable[None]]


class PipeServer:
    """命名管道服务端：每连接一个实例（ConnectNamedPipe overlapped + stop event 可取消，
    已建立的连接经 call_soon_threadsafe 交回 asyncio 事件循环）。

    - 实例句柄带 FILE_FLAG_OVERLAPPED（全双工并发读写的前提；非 overlapped 句柄上
      挂起的 ReadFile 会串行化阻塞同句柄 WriteFile，必然死锁）
    - SDDL 默认收紧到仅当前用户 + SYSTEM（跨用户内核级拒绝）
    - verify_client：可选对端进程身份校验（PID → bool），失败立即断开
    """

    def __init__(
        self,
        path: str,
        on_connection: ConnectionHandler,
        *,
        sddl: str | None = None,
        verify_client: Callable[[int], bool] | None = None,
    ) -> None:
        self._path = path
        self._on_connection = on_connection
        self._sddl = sddl or default_pipe_sddl()
        self._verify_client = verify_client
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._stop_event: int | None = None
        self._accept_thread: "threading.Thread | None" = None
        self._connections: set[asyncio.Task[None]] = set()
        self._consecutive_create_failures = 0

    async def start(self) -> None:
        if sys.platform != "win32":
            raise PipeError("命名管道仅支持 Windows")
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        import threading

        # stop event：accept 线程的 ConnectNamedPipe 等待可被取消（不靠自连/跨线程关句柄）
        self._stop_event = win32event.CreateEvent(None, True, False, None)
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name=f"pipe-accept:{self._path}", daemon=True
        )
        self._accept_thread.start()

    async def stop(self) -> None:
        self._stopping = True
        if self._stop_event is not None:
            # 唤醒 accept 线程的 WaitForMultipleObjects 使其退出
            win32event.SetEvent(self._stop_event)
        if self._accept_thread is not None:
            await asyncio.to_thread(self._accept_thread.join, 3.0)
            self._accept_thread = None
        if self._stop_event is not None:
            self._stop_event.Close()
            self._stop_event = None
        for task in list(self._connections):
            task.cancel()
        self._connections.clear()

    def _accept_one(self, handle: int) -> bool:
        """等待一个客户端连入（overlapped，可被 stop event 取消）。True=有客户端。"""
        ov = pywintypes.OVERLAPPED()
        ov.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            try:
                hr = win32pipe.ConnectNamedPipe(handle, ov)
            except pywintypes.error as exc:
                hr = exc.winerror
            if hr in (0, winerror.ERROR_PIPE_CONNECTED):
                return True
            if hr != winerror.ERROR_IO_PENDING:
                return False
            assert self._stop_event is not None
            rc = win32event.WaitForMultipleObjects(
                [ov.hEvent, self._stop_event], False, win32event.INFINITE
            )
            if rc != win32event.WAIT_OBJECT_0:
                # stop event：取消挂起的 ConnectNamedPipe 后退出
                _cancel_all_overlapped_io(handle)
                return False
            try:
                win32file.GetOverlappedResult(handle, ov, True)
            except pywintypes.error:
                return False
            return True
        finally:
            ov.hEvent.Close()

    def _accept_loop(self) -> None:
        while not self._stopping:
            handle: int | None = None
            try:
                handle = win32pipe.CreateNamedPipe(
                    self._path,
                    win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    65536,
                    65536,
                    0,
                    _security_attributes(self._sddl),
                )
            except pywintypes.error:
                if self._stopping:
                    return
                # CreateNamedPipe 持续失败（如名称冲突/ACL 异常）：限速告警，别静默刷
                self._consecutive_create_failures += 1
                if self._consecutive_create_failures in (1, 10, 60):
                    print(
                        f"[np_transport] CreateNamedPipe 失败重试中（{self._path}，"
                        f"第 {self._consecutive_create_failures} 次）",
                        file=sys.stderr,
                        flush=True,
                    )
                time.sleep(0.2)
                continue
            if not self._accept_one(handle):
                _close_handle_silently(handle)
                if self._stopping:
                    return
                continue
            self._consecutive_create_failures = 0
            if self._stopping:
                _close_handle_silently(handle)
                return
            if self._verify_client is not None:
                try:
                    pid = int(win32pipe.GetNamedPipeClientProcessId(handle))
                except (pywintypes.error, ValueError):
                    pid = -1
                if pid < 0 or not self._verify_client(pid):
                    try:
                        win32pipe.DisconnectNamedPipe(handle)
                    except pywintypes.error:
                        pass
                    _close_handle_silently(handle)
                    continue
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._dispatch, handle)

    def _dispatch(self, handle: int) -> None:
        stream = PipeStream(handle)
        task = asyncio.create_task(self._guarded(stream))
        self._connections.add(task)
        task.add_done_callback(self._connections.discard)

    async def _guarded(self, stream: PipeStream) -> None:
        try:
            await self._on_connection(stream)
        finally:
            await stream.close()


async def serve_pipe(
    path: str,
    on_connection: ConnectionHandler,
    *,
    sddl: str | None = None,
    verify_client: Callable[[int], bool] | None = None,
) -> PipeServer:
    """启动命名管道服务端（便捷包装）。"""
    server = PipeServer(path, on_connection, sddl=sddl, verify_client=verify_client)
    await server.start()
    return server


# ---------------------------------------------------------------------------
# httpx transport（HTTP/1.1 字节流原样过管道）
# ---------------------------------------------------------------------------


def _serialize_http_request(method: str, raw_path: bytes, headers: list[tuple[str, str]], body: bytes) -> bytes:
    head = f"{method} {raw_path.decode('ascii', 'replace')} HTTP/1.1\r\n"
    for name, value in headers:
        head += f"{name}: {value}\r\n"
    head += "\r\n"
    return head.encode("latin-1") + body


def _prepare_headers(request_headers: Any, body_len: int) -> list[tuple[str, str]]:
    """规整请求头：确保 Host / Content-Length / Connection: close（每请求一连接，无复用歧义）。"""
    skip = {"transfer-encoding", "content-length", "connection"}
    headers = [(k, v) for k, v in request_headers if k.lower() not in skip]
    headers.append(("Content-Length", str(body_len)))
    headers.append(("Connection", "close"))
    return headers


class _HttpResponseHead:
    def __init__(self, status: int, headers: list[tuple[str, str]], rest: bytes) -> None:
        self.status = status
        self.headers = headers
        self.rest = rest  # 头块之后已读出的体字节


async def _read_response_head(stream: PipeStream, read_timeout: float | None = None) -> _HttpResponseHead:
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = await stream.read(timeout=read_timeout)
        if not chunk:
            raise PipeClosedError("读取响应头前管道被关闭")
        buf.extend(chunk)
        if len(buf) > 256 * 1024:
            raise PipeError("响应头超长（>256KB）")
    head_bytes, rest = bytes(buf).split(b"\r\n\r\n", 1)
    lines = head_bytes.split(b"\r\n")
    try:
        status = int(lines[0].split(b" ", 2)[1])
    except (IndexError, ValueError) as exc:
        raise PipeError(f"响应状态行非法: {lines[0][:80]!r}") from exc
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            headers.append((name.decode("latin-1").strip(), value.decode("latin-1").strip()))
    return _HttpResponseHead(status, headers, rest)


def _read_response_head_sync(stream: PipeStreamSync) -> _HttpResponseHead:
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = stream.read()
        if not chunk:
            raise PipeClosedError("读取响应头前管道被关闭")
        buf.extend(chunk)
        if len(buf) > 256 * 1024:
            raise PipeError("响应头超长（>256KB）")
    head_bytes, rest = bytes(buf).split(b"\r\n\r\n", 1)
    lines = head_bytes.split(b"\r\n")
    try:
        status = int(lines[0].split(b" ", 2)[1])
    except (IndexError, ValueError) as exc:
        raise PipeError(f"响应状态行非法: {lines[0][:80]!r}") from exc
    headers = []
    for line in lines[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            headers.append((name.decode("latin-1").strip(), value.decode("latin-1").strip()))
    return _HttpResponseHead(status, headers, rest)


class _AsyncPipeBodyStream(httpx.AsyncByteStream):
    """响应体流：按 Content-Length / chunked / EOF 三种形态增量产出（SSE 走 chunked/EOF）。"""

    def __init__(self, stream: PipeStream, head: _HttpResponseHead) -> None:
        self._stream = stream
        self._head = head
        header_map = {k.lower(): v for k, v in head.headers}
        self._chunked = "chunked" in header_map.get("transfer-encoding", "").lower()
        self._content_length = (
            int(header_map["content-length"]) if "content-length" in header_map else None
        )

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._chunked:
            async for chunk in self._iter_chunked():
                yield chunk
            return
        if self._content_length is not None:
            data = self._head.rest
            remaining = self._content_length - len(data)
            if data:
                yield bytes(data[: self._content_length])
            while remaining > 0:
                chunk = await self._stream.read(min(65536, remaining))
                if not chunk:
                    raise PipeClosedError("响应体未收满管道即关闭")
                remaining -= len(chunk)
                yield chunk
            return
        # 无长度声明：读到 EOF（Connection: close 形态；SSE 常见）
        if self._head.rest:
            yield bytes(self._head.rest)
        while True:
            chunk = await self._stream.read()
            if not chunk:
                return
            yield chunk

    async def _iter_chunked(self) -> AsyncIterator[bytes]:
        buf = bytearray(self._head.rest)

        async def _read_line() -> bytes:
            while b"\r\n" not in buf:
                chunk = await self._stream.read()
                if not chunk:
                    raise PipeClosedError("chunked 体中途断流")
                buf.extend(chunk)
            line, _, rest = bytes(buf).partition(b"\r\n")
            buf.clear()
            buf.extend(rest)
            return line

        async def _read_exact(n: int) -> bytes:
            while len(buf) < n:
                chunk = await self._stream.read()
                if not chunk:
                    raise PipeClosedError("chunked 体中途断流")
                buf.extend(chunk)
            out = bytes(buf[:n])
            del buf[:n]
            return out

        while True:
            size_line = await _read_line()
            size = int(size_line.split(b";", 1)[0].strip(), 16)
            if size == 0:
                # 尾部 trailer 直至空行
                while (await _read_line()) != b"":
                    pass
                return
            data = await _read_exact(size)
            await _read_exact(2)  # 块尾 CRLF
            yield data

    async def aclose(self) -> None:
        await self._stream.close()


class _SyncPipeBodyStream(httpx.SyncByteStream):
    def __init__(self, stream: PipeStreamSync, head: _HttpResponseHead) -> None:
        self._stream = stream
        self._head = head
        header_map = {k.lower(): v for k, v in head.headers}
        self._chunked = "chunked" in header_map.get("transfer-encoding", "").lower()
        self._content_length = (
            int(header_map["content-length"]) if "content-length" in header_map else None
        )

    def __iter__(self) -> Iterator[bytes]:
        if self._chunked:
            yield from self._iter_chunked()
            return
        if self._content_length is not None:
            data = self._head.rest
            remaining = self._content_length - len(data)
            if data:
                yield bytes(data[: self._content_length])
            while remaining > 0:
                chunk = self._stream.read(min(65536, remaining))
                if not chunk:
                    raise PipeClosedError("响应体未收满管道即关闭")
                remaining -= len(chunk)
                yield chunk
            return
        if self._head.rest:
            yield bytes(self._head.rest)
        while True:
            chunk = self._stream.read()
            if not chunk:
                return
            yield chunk

    def _iter_chunked(self) -> Iterator[bytes]:
        buf = bytearray(self._head.rest)

        def _read_line() -> bytes:
            while b"\r\n" not in buf:
                chunk = self._stream.read()
                if not chunk:
                    raise PipeClosedError("chunked 体中途断流")
                buf.extend(chunk)
            line, _, rest = bytes(buf).partition(b"\r\n")
            buf.clear()
            buf.extend(rest)
            return line

        def _read_exact(n: int) -> bytes:
            while len(buf) < n:
                chunk = self._stream.read()
                if not chunk:
                    raise PipeClosedError("chunked 体中途断流")
                buf.extend(chunk)
            out = bytes(buf[:n])
            del buf[:n]
            return out

        while True:
            size_line = _read_line()
            size = int(size_line.split(b";", 1)[0].strip(), 16)
            if size == 0:
                while _read_line() != b"":
                    pass
                return
            data = _read_exact(size)
            _read_exact(2)
            yield data

    def close(self) -> None:
        self._stream.close()


class NamedPipeTransport(httpx.AsyncBaseTransport):
    """httpx 异步传输：HTTP/1.1 请求字节流原样写入命名管道并解析响应。

    对端是桌面主进程的本机代理（纯文本 HTTP/1.1，无 TLS/压缩）；
    每请求一条管道连接（Connection: close），无连接复用歧义。
    """

    def __init__(self, pipe_path: str, *, connect_timeout: float = 10.0) -> None:
        self._pipe_path = pipe_path
        self._connect_timeout = connect_timeout

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        raw_path = request.url.raw_path
        headers = _prepare_headers(list(request.headers.items()), len(body))
        if not any(k.lower() == "host" for k, _v in headers):
            headers.insert(0, ("Host", request.url.host or "localhost"))
        # httpx 超时经 request.extensions["timeout"] 携带（自定义 transport 不读即失效）：
        # connect 作用于管道建立，read 作用于响应头读取（响应体流式段不设上限——
        # SSE 长空闲间隔是正常形态；桌面代理挂死时由 openai SDK 的整体超时/上层兜底）
        ext_timeout = request.extensions.get("timeout")
        connect_to = self._connect_timeout
        read_to: float | None = None
        if isinstance(ext_timeout, dict):
            if isinstance(ext_timeout.get("connect"), (int, float)):
                connect_to = float(ext_timeout["connect"])
            if isinstance(ext_timeout.get("read"), (int, float)):
                read_to = float(ext_timeout["read"])
        stream = await open_pipe(self._pipe_path, timeout=connect_to)
        try:
            await stream.write(_serialize_http_request(request.method, raw_path, headers, body))
            head = await _read_response_head(stream, read_timeout=read_to)
        except BaseException:
            await stream.close()
            raise
        return httpx.Response(
            status_code=head.status,
            headers=head.headers,
            stream=_AsyncPipeBodyStream(stream, head),
        )

    async def aclose(self) -> None:  # 无持久资源（每请求一连接）
        return None


class NamedPipeSyncTransport(httpx.BaseTransport):
    """httpx 同步传输：供同步 OpenAI 客户端等旁路使用。"""

    def __init__(self, pipe_path: str, *, connect_timeout: float = 10.0) -> None:
        self._pipe_path = pipe_path
        self._connect_timeout = connect_timeout

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        raw_path = request.url.raw_path
        headers = _prepare_headers(list(request.headers.items()), len(body))
        if not any(k.lower() == "host" for k, _v in headers):
            headers.insert(0, ("Host", request.url.host or "localhost"))
        stream = open_pipe_sync(self._pipe_path, timeout=self._connect_timeout)
        try:
            stream.write(_serialize_http_request(request.method, raw_path, headers, body))
            head = _read_response_head_sync(stream)
        except BaseException:
            stream.close()
            raise
        return httpx.Response(
            status_code=head.status,
            headers=head.headers,
            stream=_SyncPipeBodyStream(stream, head),
        )

    def close(self) -> None:
        return None
