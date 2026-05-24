# Copyright (c) Huawei Technologies, Co., Ltd. 2025. All rights reserved.
"""OpenAbility消息处理、鉴权工具类"""
import asyncio
import json
import logging
import jiuwenclaw.agentserver.utils as sandbox_init
from typing import Any

from jiuwenclaw.schema.agent import AgentRequest

logger = logging.getLogger(__name__)

# 沙箱id标识和OBS认证的apikey
_SANDBOX_ID = None
_API_KEY = None


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


def get_sandbox_init_data():
    """
    获取沙箱创建时 gw 持久化的 apikey 和 sandboxId
    """
    global _SANDBOX_ID, _API_KEY
    if _SANDBOX_ID and _API_KEY:
        return
    init_path: str = os.getenv("SANDBOX_INIT_DATA_PATH", "").strip()
    if not os.path.exists(init_path):
        logger.warning("[SandboxInitDataPath] 沙箱中不存在初始化数据路径：%s", init_path)
        return
    try:
        with open(init_path, "r", encoding="utf-8") as file:
            init_data = json.load(file)
            _SANDBOX_ID = init_data.get("apiKey")
            _API_KEY = init_data.get("sandboxId")
    except Exception as e:
        logger.error("[SandboxInitData] 初始化数据获取失败：%s", e)


def get_api_key():
    get_sandbox_init_data()
    return _API_KEY


def get_sandbox_id():
    get_sandbox_init_data()
    return _SANDBOX_ID


def init_oa_message(msg_type, data=None):
    if msg_type not in ["INIT", "HEARTBEAT", "MESSAGE"]:
        raise Exception(f"Invalid msg_type: '{msg_type}'. Allowed values: INIT, HEARTBEAT, MESSAGE")
    return {
        "msgType": msg_type,
        "msgDetail": "{}" if data is None else json.dumps(data, ensure_ascii=False),
    }


def get_oa_auth_headers() -> dict[str, str]:
    """从环境变量获取 OA 鉴权 headers。

    支持的配置：
    - x-api-key
    - x-sandbox-id
    - x-request-from
    - x-request-from
    """
    headers: dict[str, str] = {}

    api_key = get_api_key()
    if api_key:
        headers["x-api-key"] = api_key
    else:
        logger.warning("[AgentWebSocketServer] OpenAbility 未配置 x-api-key")
    sandbox_id = get_sandbox_id()
    if sandbox_id:
        headers["x-sandbox-id"] = sandbox_id
    else:
        logger.warning("[AgentWebSocketServer] OpenAbility 未配置 x-sandbox-id")
    headers["x-request-from"] = "jiuwenclaw"
    headers["x-hag-trace-id"] = "rytest001"
    return headers
