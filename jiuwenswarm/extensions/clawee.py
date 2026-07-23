"""OpenYuanRong 函数入口 - clawee handler."""

import asyncio
import json
from dataclasses import asdict
from typing import Any, Awaitable, Callable

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.tenant_agent_pool import TenantAgentPool


def payload_to_request(request: dict[str, Any]) -> AgentRequest:
    """将函数 payload 转换为 AgentRequest.

    Args:
        request: 函数请求字典

    Returns:
        AgentRequest 对象
    """
    req_method = request.get("req_method")
    if req_method is not None and isinstance(req_method, str):
        req_method = ReqMethod(req_method)

    return AgentRequest(
        request_id=request.get("request_id"),
        channel_id=request.get("channel_id", ""),
        session_id=request.get("session_id"),
        req_method=req_method,
        params=request.get("params", {}),
        is_stream=request.get("is_stream", False),
        timestamp=request.get("timestamp", 0.0),
        metadata=request.get("metadata"),
    )


def to_json(msg: Any) -> str:
    """将对象转换为 JSON 字符串."""
    if msg:
        return json.dumps(asdict(msg), ensure_ascii=False)
    return ""


def chunk_to_payload(chunk: AgentResponseChunk) -> str:
    """将 chunk 转换为 payload 字符串."""
    return to_json(chunk)


def response_to_payload(resp: AgentResponse) -> str:
    """将 response 转换为 payload 字符串."""
    return to_json(resp)


def init(context):
    """函数初始化."""
    try:
        TenantAgentPool.get_instance()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("[clawee] Failed to initialize TenantAgentPool")
        raise


# === [yuanrong] 短路接口映射表 ===
# faas 路径不经过 agent_ws_server 的消息循环（那里才有 SESSION_LIST 等分发）。
# 对一部分「无需进入聊天路径、可直接处理并返回标准 AgentResponse」的请求，
# 在这里按 ReqMethod 注册一个 async handler：ahandler 进 chat 流程前先查表分发，
# 命中即直接返回，未命中走默认 chat 路径。
# 新增短路接口只需实现一个 _shortcut_* 函数并在此表追加一行，无需改动 ahandler。
MethodHandler = Callable[[AgentRequest], Awaitable[AgentResponse]]


async def _session_list(request: AgentRequest) -> AgentResponse:
    """session.list 短路：扫描 sessions 目录，返回历史会话基础信息列表.

    复用与 agent_ws_server.AgentWebSocketServer._handle_session_list 相同的扫描逻辑，
    返回标准 AgentResponse 形状 (payload={"sessions": [...]})，使 faas / ws 两条路径
    返回一致。扫描失败时返回空列表 + ok=True（与 ws 端一致），不阻断接口。
    """
    from jiuwenswarm.common.utils import get_agent_sessions_dir
    from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata

    sessions: list[dict[str, Any]] = []
    try:
        sessions_dir = get_agent_sessions_dir()
        if sessions_dir.exists():
            for entry in sorted(sessions_dir.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
                if not entry.is_dir():
                    continue
                # 强制跳过缓存，确保获取跨进程写入的最新数据（如 Gateway 的 /color 设置）
                meta = get_session_metadata(entry.name, cache_bust=True)
                if not meta:
                    meta = {
                        "session_id": entry.name,
                        "channel_id": "",
                        "title": "",
                        "message_count": 0,
                        "last_message_at": entry.stat().st_mtime,
                    }
                sessions.append(meta)
    except Exception:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("[clawee] session.list scan failed")
    return AgentResponse(
        request_id=request.request_id,
        channel_id=request.channel_id,
        ok=True,
        payload={"sessions": sessions},
        metadata=request.metadata,
    )


METHOD_HANDLERS: dict[ReqMethod, MethodHandler] = {
    ReqMethod.SESSION_LIST: _session_list,
}


async def ahandler(event, context=None):
    """异步处理函数."""
    try:
        request = payload_to_request(event)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("[clawee] Failed to parse event")
        return None

    pool = TenantAgentPool.get_instance()

    # 短路分发：命中映射表的接口直接处理并返回，不进入聊天路径。
    method = METHOD_HANDLERS.get(request.req_method)
    if method is not None:
        return response_to_payload(await method(request))

    try:
        if request.is_stream:
            # 流式处理
            async for chunk in pool.process_message_stream(request):
                payload = chunk_to_payload(chunk)
                if context is not None and hasattr(context, "get_stream"):
                    context.get_stream().write(payload)
        else:
            # 非流式处理
            resp = await pool.process_message(request)
            return response_to_payload(resp)

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("[clawee] Error during message processing")
        # 构建错误响应
        error_response = AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=False,
            payload={"error": str(e)},
        )
        return to_json(error_response)

    return None


def handler(event, context=None):
    """同步入口."""
    return asyncio.run(ahandler(event, context))


def pre_stop():
    """函数停止前的清理."""
    pass
