"""OpenYuanRong 函数入口 - clawee handler."""

import asyncio
import json
from dataclasses import asdict
from typing import Any

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
