# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""桌面形态 E2A 通道编排：stdio（桌面主进程）+ 命名管道（gateway 兄弟进程）。

桌面 E2A stdio 形态（密钥包 ``e2aTransport == 'stdio'``，判定见
e2a_transports.e2a_stdio_mode_enabled——**字段判定而非密钥包存在**：迁移期
桌面默认仍是 WS 形态但同样下发密钥包）时，AgentServer 不再监听 TCP 18592，
改由本模块起两条通道（claw_desktop 仓 docs/named-pipe-migration-design.md §5.2）：

- **stdio**（桌面主进程 ↔ AgentServer）：匿名管道句柄继承，第三方进程无法
  连接/枚举。首个业务帧必须是 ``{"type":"auth","token":<e2aToken>}``——
  校验是纵深防御（通道本身已是边界），失败即退出进程；通过后回
  connection.ack（与 WS 形态一致的首事件）并进入公共连接内核。
  stdin EOF（桌面关闭管道）经 ``on_stdio_closed`` 上抛（app_agentserver
  编排进程退出）。
- **命名管道**（``pipes.agentE2a``，gateway 兄弟进程 → AgentServer）：
  stdio 无法连兄弟进程。serve_pipe（SDDL 仅本人+SYSTEM）+
  verify_client=make_image_verifier([sys.executable])（gateway 与 agent 是
  同一个 jiuwenswarm.exe）+ 相同 auth 首帧校验（失败仅断开该连接，
  不影响 stdio 通道）。

非 stdio 形态（无密钥包 / ``e2aTransport='ws'``）：
``start_desktop_e2a_channels`` 返回 None，调用方回退原 TCP 监听，行为零变化。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import IO, Any, Awaitable, Callable

from jiuwenswarm.common.secrets_bootstrap import get_secret
from jiuwenswarm.server.e2a_transports import (
    PipeMessageTransport,
    StdioMessageTransport,
    e2a_stdio_mode_enabled,
    redirect_stdout_to_stderr,
    verify_auth_frame,
)

logger = logging.getLogger(__name__)

# 管道连接首帧 auth 的等待超时（秒）
_PIPE_AUTH_TIMEOUT_SECONDS = 10.0
_PIPE_REMOTE_LABEL = "named-pipe:agent-e2a"
_STDIO_REMOTE_LABEL = "stdio:desktop"

# 连接内核签名：agent_ws_server.AgentWebSocketServer.run_connection
RunConnection = Callable[..., Awaitable[None]]


class DesktopE2aChannels:
    """桌面形态两条 E2A 通道的生命周期宿主（stop 幂等）。

    - stdio：桌面主进程连接（每进程一条，断即进程退出编排）；
    - 命名管道：gateway 兄弟进程连接（serve_pipe 多实例，连接级失败不扩散）。
    """

    def __init__(
        self,
        run_connection: RunConnection,
        *,
        on_stdio_closed: Callable[[], None] | None = None,
        stdin: IO[bytes] | None = None,
        stdout_fd: int = 1,
    ) -> None:
        self._run_connection = run_connection
        self._on_stdio_closed = on_stdio_closed
        # stdin/stdout_fd 为测试 seam（生产默认 stdin_binary_stream() + fd 1）
        self._stdin = stdin
        self._stdout_fd = stdout_fd
        self.pipe_server: Any = None
        self.stdio_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        # stdout 改道 stderr 的幂等兜底（exe_entry 已在分发前改道——那里足够早，
        # openjiuwen 日志器创建时捕获的就是改道后的 sys.stdout；这里覆盖非
        # exe 入口的启动路径）
        redirect_stdout_to_stderr()
        self.stdio_task = asyncio.create_task(self._stdio_main(), name="e2a-stdio")
        await self._start_pipe_server()

    async def stop(self) -> None:
        if self.pipe_server is not None:
            try:
                await self.pipe_server.stop()
            except Exception as exc:  # noqa: BLE001 - 关停路径容错
                logger.warning("[E2A][desktop] agent-e2a 管道 server 停止失败: %s", exc)
            self.pipe_server = None
        task = self.stdio_task
        self.stdio_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 关停路径容错
                logger.warning("[E2A][desktop] stdio 通道停止失败: %s", exc)

    # ------------------------------------------------------------------ stdio

    async def _stdio_main(self) -> None:
        transport: StdioMessageTransport | None = None
        try:
            transport = StdioMessageTransport(self._stdin, stdout_fd=self._stdout_fd)
            # 首个业务帧必须是 auth 帧（密钥包 e2aToken 常量时间比对）。
            # stdio 是句柄继承通道（第三方无法连接），本校验为纵深防御；
            # 失败即返回 → on_stdio_closed → 进程整体退出。
            raw = await transport.recv_text()
            frame: Any = None
            if raw is not None:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    frame = None
            expected = str(get_secret("e2aToken", "") or "")
            if not verify_auth_frame(frame, expected):
                logger.error(
                    "[E2A][desktop] stdio auth 首帧校验失败，退出进程（纵深防御）"
                )
                return
            logger.info("[E2A][desktop] stdio auth 校验通过，进入 E2A 连接内核")
            await self._run_connection(transport, remote=_STDIO_REMOTE_LABEL)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[E2A][desktop] stdio 通道异常")
        finally:
            if transport is not None:
                try:
                    await transport.close()
                except Exception:  # noqa: BLE001 - 关停路径容错
                    pass
            on_closed = self._on_stdio_closed
            if on_closed is not None:
                try:
                    on_closed()
                except Exception:  # noqa: BLE001 - 回调异常不能阻断关停
                    logger.exception("[E2A][desktop] on_stdio_closed 回调异常")

    # -------------------------------------------------------------- 命名管道

    async def _start_pipe_server(self) -> None:
        pipe_path = str(get_secret("pipes.agentE2a", "") or "").strip()
        if not pipe_path:
            logger.warning(
                "[E2A][desktop] 密钥包缺少 pipes.agentE2a，gateway 兄弟侧 E2A 不可用"
            )
            return
        if sys.platform != "win32":
            logger.error(
                "[E2A][desktop] 命名管道仅支持 Windows，agent-e2a 管道 server 未启动: %s",
                pipe_path,
            )
            return
        try:
            from jiuwenswarm.common.np_transport import make_image_verifier, serve_pipe
        except Exception as exc:  # pragma: no cover - pywin32 缺失的防御分支
            logger.error("[E2A][desktop] 命名管道传输不可用: %s", exc)
            return
        # gateway 与 agent 是同一个 jiuwenswarm.exe：PID→镜像白名单校验对端身份
        # （连接时 GetNamedPipeClientProcessId → 镜像路径比对，防同用户仿冒进程）
        verify_client = make_image_verifier([sys.executable])
        try:
            self.pipe_server = await serve_pipe(
                pipe_path,
                self._handle_pipe_connection,
                verify_client=verify_client,
            )
        except Exception as exc:  # noqa: BLE001 - 管道失败不影响 stdio 通道
            logger.error("[E2A][desktop] agent-e2a 管道 server 启动失败: %s", exc)
            return
        logger.info("[E2A][desktop] agent-e2a 命名管道 server 已启动: %s", pipe_path)

    async def _handle_pipe_connection(self, stream: Any) -> None:
        """管道连接处理：首帧 auth（e2aToken）→ 通过后进入公共连接内核。

        鉴权失败/首帧异常即返回（``PipeServer`` 兜底关管）。连接级异常只影响
        本连接，不波及 stdio 通道与其他管道连接。
        """
        from jiuwenswarm.common.np_transport import FrameCodecError, PipeError

        try:
            try:
                first = await stream.recv_frame(timeout=_PIPE_AUTH_TIMEOUT_SECONDS)
            except (FrameCodecError, PipeError) as exc:
                logger.info("[E2A][desktop] 管道连接首帧读取失败，断开: %s", exc)
                return
            expected = str(get_secret("e2aToken", "") or "")
            if not verify_auth_frame(first, expected):
                logger.warning("[E2A][desktop] 管道连接 auth 校验失败，断开")
                return
            await self._run_connection(
                PipeMessageTransport(stream), remote=_PIPE_REMOTE_LABEL
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 连接级容错
            logger.exception("[E2A][desktop] 管道连接处理异常")


async def start_desktop_e2a_channels(
    run_connection: RunConnection,
    *,
    on_stdio_closed: Callable[[], None] | None = None,
    stdin: IO[bytes] | None = None,
    stdout_fd: int = 1,
) -> DesktopE2aChannels | None:
    """桌面 E2A stdio 形态（密钥包 ``e2aTransport == 'stdio'``）启动双通道。

    非 stdio 形态（无密钥包 / ``e2aTransport='ws'``）返回 None——调用方回退
    原 TCP 18592 监听（行为零变化）。
    """
    if not e2a_stdio_mode_enabled():
        return None
    channels = DesktopE2aChannels(
        run_connection,
        on_stdio_closed=on_stdio_closed,
        stdin=stdin,
        stdout_fd=stdout_fd,
    )
    await channels.start()
    return channels
