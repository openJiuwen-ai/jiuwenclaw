# Copyright (c) Huawei Technologies, Co., Ltd. 2025. All rights reserved.
"""OpenAbility消息处理工具类"""
import asyncio
import logging
import jiuwenclaw.agentserver.utils as sandbox_init
from typing import Any

from jiuwenclaw.schema.agent import AgentRequest

logger = logging.getLogger(__name__)


async def oa_wait_connection_ack(ws: Any, timeout: float = 10.0) -> bool:
    """等待 OA 返回第一条建连成功消息。

    成功消息格式: {"code": "0", "desc": "success"}

    Returns:
        bool: 是否成功建立连接
    """
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        data = json.loads(raw)

        # 检查成功响应格式
        if data.get("code") == "0" and data.get("desc") == "success":
            logger.info("[AgentWebSocketServer] 收到 OpenAbility 建连成功响应: %s", data)
            return True
        else:
            logger.error("[AgentWebSocketServer] OpenAbility 建连失败，响应: %s", data)
            return False
    except Exception as e:
        logger.exception("[AgentWebSocketServer] 等待 OpenAbility 建连响应异常: %s", e)
        return False


def get_oa_auth_headers() -> dict[str, str]:
    """从环境变量获取 OA 鉴权 headers。

    支持的配置：
    - x-api-key
    - x-sandbox-id
    """
    headers: dict[str, str] = {}

    api_key = sandbox_init.get_api_key()
    if api_key:
        headers["x-api-key"] = api_key
    else:
        logger.warning("[AgentWebSocketServer] OpenAbility 未配置 x-api-key")
    sandbox_id = sandbox_init.get_sandbox_id()
    if sandbox_id:
        headers["x-sandbox-id"] = sandbox_id
    else:
        logger.warning("[AgentWebSocketServer] OpenAbility 未配置 x-sandbox-id")
    return headers
