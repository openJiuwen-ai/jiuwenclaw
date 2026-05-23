# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServer 工具函数."""
import os
import json
import logging
from typing import Any

from jiuwenclaw.schema.agent import AgentRequest

logger = logging.getLogger(__name__)

# 沙箱id标识和OBS认证的apikey
_SANDBOX_ID = None
_API_KEY = None


def get_chat_id(request: AgentRequest) -> str | None:
    """获取请求的 Chat ID（平台聊天标识）。

    优先使用顶层字段，向后兼容 metadata 方式。

    Args:
        request: AgentServer 请求对象

    Returns:
        平台聊天标识（Chat ID），如果无法获取则返回 None
    """
    # 1. 优先使用顶层字段
    if request.chat_id:
        return request.chat_id

    # 2. 向后兼容：从 metadata 获取（优先级按平台）
    if request.metadata:
        return (
                request.metadata.get('feishu_chat_id') or
                request.metadata.get('wecom_chat_id') or
                request.metadata.get('dingtalk_chat_id') or
                request.metadata.get('xiaoyi_session_id')
        )
    return None


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
