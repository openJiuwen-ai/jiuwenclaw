# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""pushId 持久化管理器.

从 ``pushIdList.json`` 读写 pushId 列表，用于 pushBroadcast 向所有已注册
pushId 广播推送通知。对应 xy_channel 的 ``utils/pushid-manager.ts``。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# 与 TS 侧保持同一文件，便于双栈共存时共享同一份 pushId 列表。
PUSHID_LIST_FILE: Final[str] = "/home/sandbox/.openclaw/pushIdList.json"


def _resolve_path() -> Path:
    """返回 pushId 列表文件路径。

    ``PUSHID_LIST_FILE`` 指向容器内路径 ``/home/sandbox/.openclaw``；在
    Windows 本机调试环境下回退到 ``~/.openclaw``，与 ``memory_query`` 里
    ``Path.home() / ".openclaw"`` 的约定一致。
    """
    candidate = Path(PUSHID_LIST_FILE)
    try:
        # 尝试创建目录；成功说明路径可写。
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return Path.home() / ".openclaw" / "pushIdList.json"


def _read_push_id_list() -> list[str]:
    """读取 pushId 列表，文件不存在或格式非法时返回空列表。"""
    file_path = _resolve_path()
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.error("[PushIdManager] Failed to read pushIdList: %s", exc)
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("[PushIdManager] pushIdList.json is not valid JSON: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning(
            "[PushIdManager] pushIdList.json is not an array, returning empty array"
        )
        return []
    return [str(item) for item in data if isinstance(item, str)]


def _write_push_id_list(push_ids: list[str]) -> None:
    """写入 pushId 列表。"""
    file_path = _resolve_path()
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(push_ids, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("[PushIdManager] Failed to write pushIdList: %s", exc)
        raise


def add_push_id(push_id: str) -> None:
    """添加新的 pushId（去重），失败不抛异常以避免影响主流程。"""
    if not push_id or not isinstance(push_id, str):
        logger.warning("[PushIdManager] Invalid pushId: %r", push_id)
        return
    try:
        current = _read_push_id_list()
        if push_id in current:
            logger.log(
                logging.INFO,
                "[PushIdManager] pushId already exists: %s...",
                push_id[:20],
            )
            return
        current.append(push_id)
        _write_push_id_list(current)
        logger.log(
            logging.INFO,
            "[PushIdManager] Added new pushId: %s..., total=%d",
            push_id[:20],
            len(current),
        )
    except Exception as exc:
        logger.error("[PushIdManager] Failed to add pushId: %s", exc)


def get_all_push_ids() -> list[str]:
    """获取所有已注册 pushId。"""
    try:
        push_ids = _read_push_id_list()
        logger.log(
            logging.INFO,
            "[PushIdManager] Retrieved %d pushIds",
            len(push_ids),
        )
        return push_ids
    except Exception as exc:
        logger.error("[PushIdManager] Failed to get all pushIds: %s", exc)
        return []


def clear_all_push_ids() -> None:
    """清空所有 pushId（用于测试或重置）。"""
    try:
        _write_push_id_list([])
        logger.log(logging.INFO, "[PushIdManager] Cleared all pushIds")
    except Exception as exc:
        logger.error("[PushIdManager] Failed to clear pushIds: %s", exc)

