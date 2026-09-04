# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""请求级长生命周期 MCP session 池（stdio / sse / streamable-http）。

对 chrome-devtools-mcp 这类有状态连接器会反复开关浏览器（"flash-open"）。
按 (session_key, server_name, params 指纹) 池化一个长生命周期 session，
首次 invoke/注册时起，命中以下任一条件时销毁：
    - 用户轮次正常结束（close-on-final，interface 层按流终态判定）；
    - 用户主动 cancel / session 逐出 / 服务关闭；
    - 空闲 TTL 超时（sweep 兜底，防审批卡悬挂泄漏）。
HITL 审批暂停（chat.invocation_paused）时刻意保活：审批把一次对话拆成
多个后端请求，若按请求清理，chrome-devtools 等有状态连接器会随每个请求
结束被杀掉、浏览器窗口反复闪退。pause 期间
worker 跨请求存活，resume 请求按同 (session_key, server, 指纹) 复用。
params 指纹区分配置漂移：office-claw 每请求 env 带 INVOCATION_ID，
指纹不同自然各自独立 worker，不会被跨请求错乱复用。

sse / streamable-http 请求级连接器统一走本模块的长连接 worker
（复用 openjiuwen SseClient / StreamableHttpClient，
 connect 时经 Runner.callback_framework 注入 auth）。

并发与 cancel-scope 约束（关键，勿改动超时机制）：
- stdio 的 anyio cancel scope 是 task-local，跨 task 调 call_tool 会报
  "Attempted to exit a cancel scope that isn't the current tasks's current
  cancel scope"。故 session 必须驻留在专用 owner task：调用方把请求投队列并
  await Future，owner task 排干队列在自身上下文里跑 session.call_tool。
- stdio 超时必须用 anyio.fail_after（cancel scope）：MCP stdio transport 的
  anyio task group / cancel scope 要求进入与退出在同一 task；asyncio.wait_for
  把协程放到新 Task 跑，会破坏该不变量。
- remote（sse/streamable-http）超时必须用 asyncio.wait_for：remote transport
  （尤其 streamable-http）内部自带后台 task + anyio cancel scope；若用
  anyio.fail_after 在本 task 套一层 scope，外层 scope 与 transport 后台 task
  的 scope 跨 task 退出会抛 "Attempted to exit a cancel scope that isn't the
  current tasks's current cancel scope"（企查查 streamable-http 实测故障）。
  asyncio.wait_for 在新 Task 里跑协程，scope 隔离，与 SseClient 自身超时
  机制（同样 asyncio.wait_for）一致。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from typing import Any, Mapping
import anyio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig

from jiuwenclaw.agentserver.tools.mcp_toolkits import create_mcp_tool

logger = logging.getLogger(__name__)

# stdio MCP 无 apply_mcp_call_timeout_patch 兜底（仅覆盖 HTTP），故对
# call_tool / discovery 各加超时。call_tool 超时可经
# JIUWENCLAW_MCP_CALL_TOOL_TIMEOUT_S 覆盖（慢工具如大页面 evaluate_script）。


def _mcp_call_tool_timeout_s() -> float:
    raw = (os.environ.get("JIUWENCLAW_MCP_CALL_TOOL_TIMEOUT_S") or "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return 30.0


_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S = 30.0
_WORKER_CLOSE_TIMEOUT_S = 5.0

_REMOTE_TRANSPORTS = ("sse", "streamable-http")

# 空闲 TTL：worker 最后一次被使用后超过该时长仍未被 close-on-final /
# cancel / session 逐出显式关闭，sweep 强制回收（防审批卡悬挂、异常路径
# 泄漏孤儿进程/浏览器）。可经 JIUWENCLAW_SESSION_MCP_IDLE_TTL_S 覆盖。


def _session_mcp_idle_ttl_s() -> float:
    raw = (os.environ.get("JIUWENCLAW_SESSION_MCP_IDLE_TTL_S") or "").strip()
    if raw:
        try:
            return max(60.0, float(raw))
        except ValueError:
            pass
    return 600.0


# ---------------------------------------------------------------------------
# Pooled worker：owner task + 调用队列
# ---------------------------------------------------------------------------


class _McpCallRequest:
    """一条从 invoke task 投递给 owner task 的 call_tool 请求。"""

    __slots__ = ("tool_name", "arguments", "future")

    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()


class PooledMcpWorker:
    """持有单个 MCP 进程/长连接（stdio/sse/streamable-http）的 owner task。

    对外暴露 ``call_tool``，调用方按 ``ClientSession.call_tool`` 契约消费
    返回值（remote 经 _RemoteMcpCallAdapter 统一为 CallToolResult 形状）。
    """

    __slots__ = ("queue", "task", "server_name", "last_used")

    def __init__(self, server_name: str) -> None:
        self.queue: asyncio.Queue[_McpCallRequest | None] = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.server_name = server_name
        # 空闲 TTL 判定基准：创建时间初始化，每次成功投递 call_tool 刷新。
        self.last_used: float = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.task is not None and not self.task.done()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """向 owner task 投递 call_tool 并等待结果。"""
        if not self.alive:
            raise RuntimeError(
                f"request-scoped MCP worker for '{self.server_name}' is not running"
            )
        req = _McpCallRequest(name, arguments)
        self.last_used = time.monotonic()
        await self.queue.put(req)
        # 投递后复查：alive 检查与 put 之间 owner task 可能恰好退出（close
        # 哨兵/异常）。owner 退出路径会排干队列（set_exception），但若 req
        # 在排干之后才入队，这里靠 done 校验兜底，避免 await 永挂。
        task = self.task
        if task is not None and task.done():
            if not req.future.done():
                req.future.set_exception(
                    RuntimeError(
                        f"request-scoped MCP worker for '{self.server_name}' "
                        "exited before dispatch"
                    )
                )
        return await req.future


# 按 (session_key, server_name, params 指纹) 池化：同 key 并发 invoke 共享一个
# 进程/浏览器；同一会话内跨请求（HITL 审批拆分出的多个 request）也复用，
# 参数漂移（如 env 带 per-request token）时指纹不同自然隔离。
# session_key 形如 "channel::mode::session_id"，由 tool_manager 在注册时写入
# worker 上下文（PooledRequestMcpTool._session_key）。
_request_scoped_mcp_sessions: dict[tuple[str, str, str], PooledMcpWorker] = {}
# pool key -> 使用过该 worker 的 request_id 集合：仅用于
# release_request_scoped_mcp_sessions 遗留入口的孤儿回收映射。
_request_worker_owners: dict[tuple[str, str, str], set[str]] = {}
# 串行化 (re)build：同 key 并发首调只起一个 owner task，避免竞态泄漏失败者。
_request_scoped_mcp_build_lock = asyncio.Lock()


def _pool_key(session_key: str, server_name: str, params: Mapping[str, Any]) -> tuple[str, str, str]:
    """池键 = (session_key, server_name, params 指纹)。

    params 指纹只覆盖影响进程身份的字段（command/args/env/cwd 等），忽略
    ``_mcp_client_type`` 这类路由标记。office-claw 每请求 env 带
    INVOCATION_ID/CALLBACK_TOKEN，指纹不同 → 各请求独立 worker，
    不复用；chrome-devtools 等参数恒定连接器指纹相同 → 跨请求复用。
    """
    fp_src = {
        k: params.get(k)
        for k in ("command", "args", "env", "cwd",  # pylint: disable=complicate-comprehension
                  "encoding_error_handler", "server_path", "auth_headers", "auth_query_params", "params")
        if params.get(k) is not None
    }
    try:
        fp = hashlib.sha256(
            json.dumps(fp_src, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:16]
    except (TypeError, ValueError):
        fp = str(sorted((str(k), str(v)) for k, v in fp_src.items()))[:16]
    return (str(session_key or ""), str(server_name or ""), fp)


async def _drain_queue_with_error(worker: PooledMcpWorker, exc: BaseException) -> None:
    """worker 无法继续时，失败所有已排队的 caller。"""
    while not worker.queue.empty():
        req = worker.queue.get_nowait()
        if req is None:
            continue
        if not req.future.done():
            req.future.set_exception(exc)
        worker.queue.task_done()


async def _enter_stdio_mcp_session(stack: AsyncExitStack, params: Mapping[str, Any]) -> Any:
    """stdio transport：起子进程 ClientSession，生命周期交由 stack 管理。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    read, write = await stack.enter_async_context(
        stdio_client(
            StdioServerParameters(
                command=str(params["command"]),
                args=list(params.get("args") or []),
                env=dict(params.get("env") or {}),
                cwd=str(params.get("cwd") or ""),
                encoding_error_handler=str(
                    params.get("encoding_error_handler") or "strict"
                ),
            )
        )
    )
    session = await stack.enter_async_context(
        ClientSession(read, write, sampling_callback=None)
    )
    await session.initialize()
    return session


def _remote_mcp_client_cls(client_type: str) -> Any | None:
    """返回 sse/streamable-http 对应的高层 MCP client 类（openjiuwen 提供）。"""
    try:
        if client_type == "sse":
            from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient

            return SseClient
        if client_type == "streamable-http":
            from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import (
                StreamableHttpClient,
            )

            return StreamableHttpClient
    except ImportError as exc:
        logger.warning(
            "remote MCP client class for transport '%s' import failed: %s",
            client_type,
            exc,
        )
    return None


def _build_remote_mcp_config(
    server_name: str,
    params: Mapping[str, Any],
    client_type: str,
) -> McpServerConfig:
    """从 worker params 重建 ``McpServerConfig``。

    discovery（tool_manager.list_remote_mcp_connector_tools）与 worker
    （_enter_remote_mcp_session）都要 new 一个 remote client，配置字段手工
    重复易漂移，集中在此。字段口径：server_id 缺省回退 server_name，
    再回退传入的 server_name，避免空 id。
    """

    return McpServerConfig(
        server_id=str(params.get("server_id") or params.get("server_name") or server_name),
        server_name=str(params.get("server_name") or server_name),
        server_path=str(params.get("server_path") or ""),
        client_type=client_type,
        params=dict(params.get("params") or {}),
        auth_headers=dict(params.get("auth_headers") or {}),
        auth_query_params=dict(params.get("auth_query_params") or {}),
    )


class _RemoteMcpCallAdapter:
    """让 remote MCP client 的 call_tool 返回形状对齐 stdio ClientSession。

    openjiuwen 的 ``SseClient`` / ``StreamableHttpClient`` 已经把
    ``CallToolResult`` 抽成裸文本/值返回（extract_mcp_tool_result_content）；
    而 PooledRequestMcpTool.invoke 按 stdio ``ClientSession.call_tool`` 的
    契约读 ``result.content[-1].text``。本适配器把 remote 的裸返回值重新包成
    ``CallToolResult(content=[TextContent])``，使两条路径在 invoke 处统一，
    避免 ``'str' object has no attribute 'content'``。
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        from mcp.types import CallToolResult, TextContent

        raw = await self._client.call_tool(name, arguments)
        # extract_mcp_tool_result_content 对空结果返回 None。
        if raw is None:
            return CallToolResult(content=[])
        return CallToolResult(content=[TextContent(type="text", text=_remote_mcp_result_to_text(raw))])


def _remote_mcp_result_to_text(raw: Any) -> str:
    """把 extract_mcp_tool_result_content 的裸返回值规整成可读文本。

    返回形状：str（文本/image 占位串）/ dict（model_dump）/ 原始 data / 其它。
    - str：原样用。
    - dict：JSON 序列化（ensure_ascii=False 保留中文），比 str() 的 repr 可读。
    - 其它：str() 兜底。
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(raw)
    return str(raw)


async def _enter_remote_mcp_session(
    stack: AsyncExitStack,
    params: Mapping[str, Any],
    client_type: str,
) -> Any:
    """sse/streamable-http transport：复用 openjiuwen 高层 client，长连接复用。

    connect 时 ``Runner.callback_framework.trigger(TOOL_AUTH)`` 注入 auth_headers
    （relay 下发的 ``Authorization: Bearer xxx`` 经 HeaderQueryAuthStrategy 加到
    请求头）。把 disconnect 注册进 stack，使 worker 退出时统一关连接。
    """
    client_cls = _remote_mcp_client_cls(client_type)
    if client_cls is None:
        raise ValueError(f"unsupported remote MCP transport: {client_type}")

    rebuild_cfg = _build_remote_mcp_config(params.get("server_name") or "", params, client_type)
    client = client_cls(rebuild_cfg)
    connected = await client.connect(timeout=_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S)
    if not connected:
        raise RuntimeError(
            f"remote MCP client connect returned false: {rebuild_cfg.server_path}"
        )

    async def _disconnect() -> None:
        try:
            await client.disconnect(timeout=10.0)
        except Exception as exc:
            logger.debug("remote MCP client disconnect failed: %s", exc)

    stack.push_async_callback(_disconnect)
    return _RemoteMcpCallAdapter(client)


async def _run_mcp_worker(
    params: Mapping[str, Any],
    worker: PooledMcpWorker,
) -> None:
    """Owner task：持有 MCP client 句柄，排干调用队列。

    按 ``params["_mcp_client_type"]`` 分派：
    - stdio（默认/缺省）：起 ``stdio_client`` + ``ClientSession`` 进程。
    - sse / streamable-http：复用 openjiuwen ``SseClient`` / ``StreamableHttpClient``，
      connect 后长连接复用（自带 owner-task/cancel-scope/超时/重连 + auth 注入）。

    全程在本 task 内执行，使 stdio 的 anyio task group / cancel scope 不逃逸到
    外来 task。task 被 cancel（清理）或队列收到 ``None`` 哨兵时退出
    （关闭 stdio 进程 / 远端连接）。任何退出路径都必须排干队列中剩余的
    caller（set_exception），否则竞态下投递到已死队列的请求会永挂。
    """

    client_type = str(params.get("_mcp_client_type") or "").lower() or "stdio"
    is_remote = client_type in _REMOTE_TRANSPORTS

    async with AsyncExitStack() as stack:
        try:
            if is_remote:
                session = await _enter_remote_mcp_session(stack, params, client_type)
            else:
                session = await _enter_stdio_mcp_session(stack, params)
        except Exception as exc:
            # 初始化失败：失败已排队的 caller，退出以便下次 invoke 时 acquire 重建。
            logger.warning(
                "request-scoped MCP worker init failed: server=%s transport=%s error=%s",
                worker.server_name,
                client_type,
                exc,
            )
            await _drain_queue_with_error(worker, exc)
            return
        while True:
            req = await worker.queue.get()
            if req is None:
                # close 哨兵。竞态窗口：put_nowait(None) 到本 task resume 之间，
                # 并发 caller 的 alive 检查可能已通过并把新 req 排在 None 之后；
                # break 前必须排干，否则该 req 的 future 永挂。
                await _drain_queue_with_error(
                    worker,
                    RuntimeError(
                        f"request-scoped MCP worker for '{worker.server_name}' "
                        "is closing"
                    ),
                )
                break
            try:
                # 超时分派：
                # - stdio：必须用 anyio.fail_after（同步 cancel scope，进入与退出
                #   同在 owner task）。asyncio.wait_for 把协程放到新 Task 跑，会破坏
                #   stdio transport task group 的 cancel-scope 不变量。
                # - remote（sse/streamable-http）：必须用 asyncio.wait_for。remote
                #   transport 内部自带后台 task + anyio cancel scope；在本 task 套
                #   anyio.fail_after 会跨 task 退出 scope 抛 RuntimeError（企查查
                #   streamable-http 实测故障）。
                if is_remote:
                    result = await asyncio.wait_for(
                        session.call_tool(req.tool_name, arguments=req.arguments),
                        timeout=_mcp_call_tool_timeout_s(),
                    )
                else:
                    with anyio.fail_after(_mcp_call_tool_timeout_s()):
                        result = await session.call_tool(
                            req.tool_name, arguments=req.arguments
                        )
            except TimeoutError as exc:
                if not req.future.done():
                    req.future.set_exception(exc)
                worker.queue.task_done()
                logger.warning(
                    "request-scoped MCP worker call_tool timed out: "
                    "server=%s tool=%s timeout=%.0fs",
                    worker.server_name,
                    req.tool_name,
                    _mcp_call_tool_timeout_s(),
                )
                # remote transport：SseClient/StreamableHttpClient 的 _submit 把调用
                # 转发到其内部 owner_task 串行执行；外层超时只取消等待方的 future，
                # 不会取消 owner_task 上正在跑的命令。若 continue 复用 worker，下次
                # invoke 投的新命令会排在未完成的旧命令后面，最长阻塞 60s
                # （SseClient 内部 asyncio.wait_for 上限）。故超时即退出 worker：
                # AsyncExitStack 关闭触发 disconnect（SseClient 内部 cancel
                # owner_task 清掉未完成命令），下次 invoke 的 acquire 发现 worker
                # 已死便重建。
                if is_remote:
                    await _drain_queue_with_error(worker, exc)
                    break
                continue
            except BaseException as exc:
                # 即使 cancel/keyboard 也要解除 caller，避免 invoke 永挂死 worker。
                if not req.future.done():
                    req.future.set_exception(exc)
                worker.queue.task_done()
                if not isinstance(exc, Exception):
                    # Cancelled/KeyboardInterrupt/SystemExit：worker 被销毁，
                    # 先排干队列再 raise。
                    await _drain_queue_with_error(worker, exc)
                    raise
                # 普通调用失败：保留循环，让 invoke 的 force_rebuild 重建 worker。
                continue
            if not req.future.done():
                req.future.set_result(result)
            worker.queue.task_done()


# ---------------------------------------------------------------------------
# acquire / close / release
# ---------------------------------------------------------------------------


async def acquire_request_scoped_mcp_session(
    session_key: str,
    server_name: str,
    params: Mapping[str, Any],
    *,
    force_rebuild: bool = False,
) -> PooledMcpWorker:
    """返回本会话+连接器的长生命周期 worker（缺失/死亡时透明重建）。

    owner task 首次使用时起、后续 invoke 复用；死掉时透明重建。
    force_rebuild=True 丢弃缓存 worker 起新的（invoke 重试路径在死进程后用）。
    """

    key = _pool_key(session_key, server_name, params)
    worker = _request_scoped_mcp_sessions.get(key)
    if force_rebuild and worker is not None:
        await _close_pooled_worker(worker, key)
        worker = None
    if worker is not None and worker.alive:
        _record_worker_owner(key, session_key, server_name, params, worker)
        return worker
    # (re)build：旧 worker 缺失或已死。串行化 build 以免同 key 并发首调起多个 owner task。
    async with _request_scoped_mcp_build_lock:
        worker = _request_scoped_mcp_sessions.get(key)
        if worker is not None and worker.alive:
            _record_worker_owner(key, session_key, server_name, params, worker)
            return worker
        worker = PooledMcpWorker(server_name)
        _request_scoped_mcp_sessions[key] = worker
        # create_task 会复制当前 context（含 TOOL_AUTH callback 等），
        # stdio 的 task group 在 owner task 内进入，cancel scope 属于 owner
        # task，跨 invoke task 安全。
        worker.task = asyncio.create_task(_run_mcp_worker(params, worker))
    _record_worker_owner(key, session_key, server_name, params, worker)
    # sweep 在 build 锁外触发：_close_pooled_worker 极端卡死时不能级联
    # 持有全局 build 锁（那会冻结所有 session 的 MCP 调用）。
    await sweep_idle_session_mcp_workers()
    return worker


def _record_worker_owner(
    key: tuple[str, str, str],
    session_key: str,
    server_name: str,
    params: Mapping[str, Any],
    worker: PooledMcpWorker,
) -> None:
    """记录 request→worker 归属（遗留清理入口的孤儿回收映射），best-effort。"""
    rid = str(params.get("_request_id") or "")
    if not rid:
        return
    _request_worker_owners.setdefault(key, set()).add(rid)


async def close_pooled_mcp_worker(session_key: str, server_name: str) -> None:
    """Best-effort 关闭并移除单个 (session_key, server_name) 下的全部 worker。

    同一 (session_key, server_name) 理论上只有一份活跃指纹；遍历匹配
    前两元即可兜住指纹漂移遗留的旧 worker。
    """
    keys = [k for k in _request_scoped_mcp_sessions
            if k[0] == str(session_key or "") and k[1] == str(server_name or "")]
    for key in keys:
        worker = _request_scoped_mcp_sessions.get(key)
        if worker is None:
            continue
        await _close_pooled_worker(worker, key)


async def _close_pooled_worker(
    worker: PooledMcpWorker,
    key: tuple[str, str],
) -> None:
    """Best-effort stop+remove one pooled worker (kills the stdio process)."""

    _request_scoped_mcp_sessions.pop(key, None)
    task = worker.task
    if task is None:
        return
    if task.done():
        # owner task 已退出（可能 cancel 后已 done，或异常退出），suppress
        # 其 CancelledError/异常，避免泄漏到调用方。
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            logger.warning("normal exit")
            pass
        return
    # Ask the owner loop to exit gracefully, then await (cancel as fallback)。
    # 用 anyio.fail_after（同步 cancel scope，与 dev-stable 一致）：await 的是
    # worker task 完成，scope 在本 task 进入与退出，安全；不用 asyncio.wait_for
    # （它把协程放到新 Task 跑，会破坏 MCP transport 的 cancel-scope 不变量）。
    try:
        worker.queue.put_nowait(None)
    except Exception as exc:
        logger.warning(
            "request-scoped MCP worker close: put_nowait(None) failed "
            "server=%s key=%s error=%s (will fall back to task.cancel)",
            worker.server_name, key, exc,
        )
    try:
        with anyio.fail_after(_WORKER_CLOSE_TIMEOUT_S):
            await task
    except (asyncio.CancelledError, TimeoutError):
        # CancelledError: 被 await 的 task 已 cancelled（外部 cancel 或哨兵后退出）。
        # TimeoutError: anyio.fail_after 抛内置 TimeoutError（asyncio.TimeoutError
        # 的父类）。cancel 后的等待必须有上限：owner task 若卡在同步阻塞 IO
        # （如子进程管道 read）则不响应 cancel，无界 await 会卡住调用方——而本
        # 函数可能在 acquire 的全局 build 锁内被 sweep 调到，卡死 = 全服务 MCP
        # 不可用。超时后放弃等待、记录泄漏，由进程退出/TTL 兜底。
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(task, timeout=_WORKER_CLOSE_TIMEOUT_S)
        except asyncio.CancelledError:
            # 预期行为：task 响应了 cancel，正常退出
            logger.warning("normal exit")
            pass
        except (asyncio.TimeoutError, TimeoutError):
            logger.error(
                "request-scoped MCP worker unresponsive to cancel (leaked): "
                "server=%s key=%s",
                worker.server_name, key,
            )
        except Exception as e:
            logger.warning(
                "request-scoped MCP worker cancel-fallback raised: server=%s key=%s error=%s",
                worker.server_name, key, e,
            )
    except asyncio.CancelledError:
        # 清理路径自身被 cancel：仍 cancel worker 以免 stdio 进程/浏览器泄漏。
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(task, timeout=_WORKER_CLOSE_TIMEOUT_S)
        except asyncio.CancelledError:
            # 预期行为：task 响应了 cancel，正常退出
            logger.warning("normal exit")
            pass
        except (asyncio.TimeoutError, TimeoutError):
            logger.error(
                "request-scoped MCP worker unresponsive to cancel (leaked): "
                "server=%s key=%s",
                worker.server_name, key,
            )
        except Exception as e:
            logger.warning(
                "request-scoped MCP worker cancel-fallback raised: server=%s key=%s error=%s",
                worker.server_name, key, e,
            )
        raise
    except Exception as exc:
        # owner task 抛了非超时/非 cancel 的异常（多数是初始化失败已退出），记录根因。
        logger.warning(
            "request-scoped MCP worker owner task exited with error "
            "server=%s key=%s error=%s",
            worker.server_name, key, exc,
        )


async def release_session_mcp_workers(session_key: str) -> None:
    """停止本会话的全部长生命周期 MCP worker（close-on-final / cancel 清理钩子）。

    best-effort：单个 worker 关闭失败不影响其余。
    """
    sk = str(session_key or "")
    keys = [k for k in _request_scoped_mcp_sessions if k[0] == sk]
    for key in keys:
        worker = _request_scoped_mcp_sessions.get(key)
        if worker is None:
            continue
        await _close_pooled_worker(worker, key)


async def release_request_scoped_mcp_sessions(request_id: str) -> None:
    """回收与该 request_id 关联、且已无人续用的池化 worker。

    兼容遗留入口：请求级注册表清理时调用。session 级复用上线后，worker
    不再随请求销毁——同一 worker 会被 HITL 审批拆出的后续请求继续使用，
    因此**只要 worker 还活着就不动它**（归属记录只用来定位尸体）：
    - worker 已死：从池与归属表中摘除，防池膨胀；
    - worker 存活但归属表里只剩本请求：也保留（下一个审批请求可能马上
      就要复用它；真正无人使用由空闲 TTL sweep 兜底回收）。
    """
    rid = str(request_id or "")
    stale_keys: list[tuple[str, str, str]] = []
    for key, rids in list(_request_worker_owners.items()):
        if rid not in rids:
            continue
        rids.discard(rid)
        if not rids:
            _request_worker_owners.pop(key, None)
        worker = _request_scoped_mcp_sessions.get(key)
        if worker is None or not worker.alive:
            stale_keys.append(key)
    for key in stale_keys:
        worker = _request_scoped_mcp_sessions.get(key)
        if worker is not None:
            await _close_pooled_worker(worker, key)


async def sweep_idle_session_mcp_workers(
    *,
    idle_ttl_s: float | None = None,
) -> int:
    """回收空闲超时的 worker（防审批卡悬挂/异常路径泄漏孤儿进程）。

    在每次 acquire 时顺带执行（懒触发，无需独立后台任务）。返回回收数。
    """
    ttl = idle_ttl_s if idle_ttl_s is not None else _session_mcp_idle_ttl_s()
    now = time.monotonic()
    stale: list[tuple[tuple[str, str, str], PooledMcpWorker]] = []
    for key, worker in _request_scoped_mcp_sessions.items():
        if worker.alive and (now - worker.last_used) > ttl:
            stale.append((key, worker))
        elif not worker.alive:
            # 已死 worker：直接从池里摘掉，防止池膨胀。
            stale.append((key, worker))
    for key, worker in stale:
        await _close_pooled_worker(worker, key)
    return len(stale)


def _clear_request_scoped_mcp_sessions_for_tests() -> None:
    """测试用：清空池状态（不调用 cancel，避免跨事件循环 await）。"""
    for worker in list(_request_scoped_mcp_sessions.values()):
        with contextlib.suppress(Exception):
            worker.queue.put_nowait(None)
        task = worker.task
        if task is not None:
            task.cancel()
    _request_scoped_mcp_sessions.clear()
    _request_worker_owners.clear()


# ---------------------------------------------------------------------------
# 周期 sweep：兜底回收无任何 MCP 活动后遗留的空闲 worker
# ---------------------------------------------------------------------------

# 检查间隔 60s：最小 TTL 为 60s（env 强设），默认 600s，60s 粒度足够精确。
_SWEEP_INTERVAL_S = 60.0
_sweep_task: asyncio.Task | None = None


async def _session_mcp_sweep_loop() -> None:
    """周期回收空闲超时/已死的池化 MCP worker。

    懒触发 sweep（acquire 顺带）覆盖正常流量；但"用户触发 HITL 暂停后
    直接关闭页面、session 长时间未逐出"的场景下不再有任何 acquire，
    TTL 到期的 worker（chrome 浏览器等 stdio 子进程）只能靠本循环回收。
    """
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL_S)
        try:
            reclaimed = await sweep_idle_session_mcp_workers()
            if reclaimed:
                logger.info(
                    "session MCP periodic sweep reclaimed %d idle worker(s)",
                    reclaimed,
                )
        except asyncio.CancelledError as e:
            logger.warning(f"Cancel error: {e}")
            raise
        except Exception:
            logger.exception("session MCP periodic sweep failed")


def start_session_mcp_sweep_loop() -> None:
    """启动周期 sweep 后台任务（幂等，服务启动时调用一次）。"""
    global _sweep_task
    if _sweep_task is not None and not _sweep_task.done():
        return
    _sweep_task = asyncio.create_task(_session_mcp_sweep_loop())


def stop_session_mcp_sweep_loop() -> None:
    """停止周期 sweep 后台任务（服务关闭时调用，可缺省）。"""
    global _sweep_task
    if _sweep_task is not None:
        _sweep_task.cancel()
        _sweep_task = None


# ---------------------------------------------------------------------------
# 连接器发现（注册时一次性 list_tools 取 schema，与长生命周期 worker 分离）
# ---------------------------------------------------------------------------


def _extract_mcp_tool_defs(response: Any) -> list[dict[str, Any]]:
    """从 mcp ClientSession.list_tools() 的响应里抽 tool schema。"""
    tools = getattr(response, "tools", None) or []
    return [
        {
            "name": getattr(tool, "name", "") or "",
            "description": getattr(tool, "description", "") or "",
            "input_params": getattr(tool, "inputSchema", {}) or {},
        }
        for tool in tools
    ]


async def discover_stdio_mcp_tools(
    params: Mapping[str, Any],
    *,
    server_name: str = "",
) -> list[dict[str, Any]]:
    """stdio 发现：单次拉进程 list_tools 后关闭（带 30s 超时）。

    用户配置的连接器是任意命令（不像可信的自带 office-claw），防护
    initialize/list_tools 卡死注册流程。用 anyio.fail_after 而非
    asyncio.wait_for（后者破坏 stdio cancel-scope 不变量）。
    任意失败返回 [] 以免单个坏连接器中断注册。
    """
    from jiuwenclaw.agentserver.tools.ephemeral_stdio_mcp_tool import (
        list_stdio_mcp_tool_defs,
    )

    try:
        # anyio.fail_after 是同步 context manager（cancel scope）：进入与退出同在
        # 本 task，满足 stdio transport task group 的 cancel-scope 不变量。
        with anyio.fail_after(_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S):
            return await list_stdio_mcp_tool_defs(dict(params))
    except TimeoutError:
        logger.warning(
            "request-scoped MCP connector '%s' discovery timed out after "
            "%.0fs (initialize/list_tools hung)",
            server_name,
            _MCP_CONNECTOR_DISCOVERY_TIMEOUT_S,
        )
        return []
    except Exception as exc:
        logger.warning(
            "request-scoped MCP connector '%s' tool discovery failed: %s",
            server_name,
            exc,
        )
        return []


def build_remote_connect_params(server_name: str, server_cfg: Any) -> dict[str, Any]:
    """构建 sse/streamable-http 连接器的连接快照（供 worker 长连接复用）。

    字段口径与 dev-stable connect_params 一致：带 ``_mcp_client_type`` 供
    ``_run_mcp_worker`` 按 transport 分派。
    """
    client_type = str(getattr(server_cfg, "client_type", "") or "").lower()
    return {
        "_mcp_client_type": client_type,
        "server_name": str(getattr(server_cfg, "server_name", "") or server_name),
        "server_id": str(getattr(server_cfg, "server_id", "") or ""),
        "server_path": str(getattr(server_cfg, "server_path", "") or ""),
        "auth_headers": dict(getattr(server_cfg, "auth_headers", {}) or {}),
        "auth_query_params": dict(getattr(server_cfg, "auth_query_params", {}) or {}),
        "params": dict(getattr(server_cfg, "params", {}) or {}),
    }


async def discover_remote_mcp_tools(
    server_name: str,
    server_cfg: Any,
    client_type: str,
) -> list[dict[str, Any]]:
    """sse/streamable-http 发现：connect → list_tools → disconnect。

    复用 openjiuwen 高层 client（connect 时经 Runner.callback_framework
    注入 auth_headers）。remote transport 内部自带后台 task + anyio cancel
    scope，超时必须用 asyncio.wait_for（新 task 隔离 scope）；任意失败
    返回 [] 以免单个坏连接器中断注册。
    """
    client_cls = _remote_mcp_client_cls(client_type)
    if client_cls is None:
        logger.warning(
            "request-scoped MCP connector '%s' transport '%s' has no client class; skipping",
            server_name,
            client_type,
        )
        return []

    connect_params = build_remote_connect_params(server_name, server_cfg)
    rebuild_cfg = _build_remote_mcp_config(server_name, connect_params, client_type)
    client = client_cls(rebuild_cfg)
    connected = False
    try:
        try:
            connected = await asyncio.wait_for(
                client.connect(timeout=_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S),
                timeout=_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "request-scoped MCP connector '%s' discovery timed out after "
                "%.0fs (connect/list_tools hung)",
                server_name,
                _MCP_CONNECTOR_DISCOVERY_TIMEOUT_S,
            )
            return []
        if not connected:
            logger.warning(
                "request-scoped MCP connector '%s' (%s) connect failed: %s",
                server_name,
                client_type,
                connect_params["server_path"],
            )
            return []
        try:
            # StreamableHttpClient.list_tools 的 timeout 参数当前未生效（直接调
            # session.list_tools 无超时），必须靠这层 asyncio.wait_for 兜底防卡死。
            tools = await asyncio.wait_for(
                client.list_tools(timeout=_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S),
                timeout=_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "request-scoped MCP connector '%s' list_tools timed out after %.0fs",
                server_name,
                _MCP_CONNECTOR_DISCOVERY_TIMEOUT_S,
            )
            return []
        return [
            {
                "name": getattr(t, "name", "") or "",
                "description": getattr(t, "description", "") or "",
                "input_params": getattr(t, "input_params", None)
                or getattr(t, "inputSchema", {})
                or {},
            }
            for t in (tools or [])
        ]
    except Exception as exc:
        logger.warning(
            "request-scoped MCP connector '%s' (%s) tool discovery failed: %s",
            server_name,
            client_type,
            exc,
        )
        return []
    finally:
        if connected:
            try:
                await client.disconnect(timeout=10.0)
            except Exception as exc:
                logger.debug(
                    "request-scoped MCP connector '%s' discovery disconnect failed: %s",
                    server_name,
                    exc,
                )


# ---------------------------------------------------------------------------
# 请求级 MCP 工具（注册进 resource_mgr / ability_manager 的 Tool 实例）
# ---------------------------------------------------------------------------


class PooledRequestMcpTool(Tool):
    """通过 (session_key, server_name, params 指纹) 池化 session 调用单个 MCP 工具。

    替代旧 EphemeralStdioMcpTool：stdio 进程 / sse / streamable-http 连接
    按会话池化，首次 invoke（或注册时探测）起，轮次正常结束（close-on-final）
    / cancel / 空闲超时时销毁，有状态连接器（chrome-devtools 等）不再每次
    调用开关浏览器，也不再随 HITL 审批拆出的每个请求闪退。

    - card.name 是 LLM 可见的带 server 前缀 qualified name（``{server}__{tool}``），
      不能直接拿去 call_tool；``raw_tool_name`` 才是 MCP server 上真正注册的名字。
    - stdio 参数经 ContextVar getter 在 invoke 时解析（与旧实现一致），
      且 worker owner task 的 context 在注册 task 创建时复制，天然按请求隔离。
    - remote 参数是注册时的冻结快照（server_path/auth/params）。
    - session_key 决定跨请求复用范围；request_id 仅用于归属记录（遗留清理）。
    """

    def __init__(
        self,
        card: ToolCard,
        get_params: Any,
        *,
        raw_tool_name: str | None = None,
        request_id: str = "",
        server_name: str = "",
        session_key: str = "",
    ) -> None:
        super().__init__(card)
        self._get_params = get_params
        self._raw_tool_name = raw_tool_name if raw_tool_name is not None else card.name
        self._request_id = str(request_id or "")
        self._server_name = str(server_name or "")
        self._session_key = str(session_key or "")

    def _resolve_params(self) -> dict[str, Any]:
        try:
            params = self._get_params()
        except Exception:
            params = {}
        return params if isinstance(params, dict) else {}

    async def stream(self, inputs: Any, **kwargs: Any):
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    async def invoke(self, inputs: Any, **kwargs: Any) -> dict[str, Any]:
        arguments = inputs if isinstance(inputs, dict) else {}
        params = self._resolve_params()
        # params 快照带上归属 request_id（仅用于遗留清理入口的孤儿回收映射）。
        if self._request_id:
            params = {**params, "_request_id": self._request_id}
        try:
            worker = await acquire_request_scoped_mcp_session(
                self._session_key, self._server_name, params
            )
            try:
                result = await worker.call_tool(self._raw_tool_name, arguments=arguments)
            except (TimeoutError, asyncio.TimeoutError):
                # 结果未知：remote 的 asyncio.wait_for 超时只取消等待方，
                # 服务端可能已受理执行；stdio 超时后 worker 循环保留、命令
                # 可能仍在进程内跑。非幂等工具（写库/发消息）自动重试会
                # 双重执行，直接上抛交由上层（LLM/用户）决策。
                raise
            except Exception:
                # 池化进程/连接在请求中途死掉（崩溃/stdin 关闭）：
                # 丢弃重建一次再重试。注意 worker.call_tool 对已死 worker
                # 抛 RuntimeError("not running")，重建后可安全重试（原
                # 请求从未投递成功）；BrokenPipe/连接断类同理。
                worker = await acquire_request_scoped_mcp_session(
                    self._session_key,
                    self._server_name,
                    params,
                    force_rebuild=True,
                )
                result = await worker.call_tool(self._raw_tool_name, arguments=arguments)
            result_content: str | None = None
            if result is not None and getattr(result, "content", None):
                result_content = getattr(result.content[-1], "text", None)
            return {"result": result_content}
        except Exception as exc:
            raise build_error(
                StatusCode.TOOL_MCP_EXECUTION_ERROR,
                cause=exc,
                reason=str(exc),
                method="invoke",
                card=self._card,
            ) from exc


__all__ = [
    "PooledMcpWorker",
    "PooledRequestMcpTool",
    "acquire_request_scoped_mcp_session",
    "close_pooled_mcp_worker",
    "release_request_scoped_mcp_sessions",
    "release_session_mcp_workers",
    "sweep_idle_session_mcp_workers",
    "start_session_mcp_sweep_loop",
    "stop_session_mcp_sweep_loop",
    "discover_stdio_mcp_tools",
    "discover_remote_mcp_tools",
    "build_remote_connect_params",
    "_mcp_call_tool_timeout_s",
    "_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S",
    "_build_remote_mcp_config",
    "_remote_mcp_client_cls",
    "_pool_key",
    "_clear_request_scoped_mcp_sessions_for_tests",
]
