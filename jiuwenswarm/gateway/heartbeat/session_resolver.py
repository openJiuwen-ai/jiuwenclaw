# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""HeartbeatSessionResolver — 会话存在性检查与会话删除通知.

第一版只做最小检查(方案 §7.5):
  - ``resolve(channel_id, session_id)``：session 目录存在且 metadata 可读时返回会话摘要,否则 None。
  - ``on_session_deleted(session_id)``：会话删除/归档时通知 scheduler 清理关联任务。

是否真正可投递、是否要取消当前流、是否做通道路由,应交给
``MessageHandler.publish_user_messages()`` 以及既有 MessageHandler 流程处理,
避免 Heartbeat 新增一套与现有消息入口不一致的判定逻辑。

参考:``jiuwenswarm心跳任务重构方案设计.md`` §7.5、§5.2。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class SessionSummary:
    """``resolve`` 返回的会话最小摘要。"""

    session_id: str
    channel_id: str
    title: str | None = None
    exists: bool = True
    route_metadata: dict[str, Any] | None = None


class _SchedulerCallback(Protocol):
    """scheduler 需要实现的回调接口(避免循环导入,用 Protocol)。"""

    async def on_session_deleted(self, session_id: str) -> None: ...


class HeartbeatSessionResolver:
    """封装会话存在性检查与删除通知。

    不依赖 ``MessageHandler`` / scheduler 具体实现,通过回调解耦。
    """

    def __init__(self, scheduler: _SchedulerCallback | None = None) -> None:
        self._scheduler = scheduler

    def set_scheduler(self, scheduler: _SchedulerCallback) -> None:
        """延迟注入 scheduler(规避构造期循环依赖)。"""
        self._scheduler = scheduler

    # ---- 会话存在性检查 ----

    @staticmethod
    def _read_session_metadata(session_id: str) -> dict[str, Any] | None:
        """读取 session metadata;不存在或不可读返回 None。

        使用 cache_bust=True 跨进程强制读磁盘(scheduler 是独立进程/循环)。
        """
        try:
            from jiuwenswarm.server.runtime.session.session_metadata import (
                _read_metadata,
            )

            data = _read_metadata(session_id, cache_bust=True)
            return data if isinstance(data, dict) and data else None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"temporary session metadata read failure for {session_id}: {exc}"
            ) from exc

    def resolve(self, channel_id: str, session_id: str) -> SessionSummary | None:
        """第一版最小检查:session 目录存在且 metadata 可读。

        Args:
            channel_id: 心跳任务绑定的原会话入口(web/tui/feishu/...)。
            session_id: 心跳任务绑定的原会话 ID。

        Returns:
            SessionSummary 或 None(不存在/不可读)。
        """
        sid = str(session_id or "").strip()
        cid = str(channel_id or "").strip()
        if not sid:
            return None
        data = self._read_session_metadata(sid)
        if data is None:
            return None
        title = data.get("title") or data.get("name")
        delivery = data.get("delivery_context")
        if not isinstance(delivery, dict):
            delivery = {}
        route_metadata = delivery.get("route_metadata")
        return SessionSummary(
            session_id=sid,
            channel_id=str(delivery.get("channel_id") or cid).strip() or cid,
            title=str(title) if isinstance(title, str) and title.strip() else None,
            route_metadata=(
                dict(route_metadata) if isinstance(route_metadata, dict) else None
            ),
        )

    # ---- 会话删除通知 ----

    async def on_session_deleted(self, session_id: str) -> None:
        """会话删除/归档入口调用;转交 scheduler 按 session_deleted_policy 处理。

        两条必须接入的删除入口(方案 §5.2):
          - Web/TUI session 删除:``app_web_handlers.py::_session_delete``
          - Team session runtime 删除:``team_manager.py::delete_session_runtime``
        """
        sid = str(session_id or "").strip()
        if not sid:
            return
        if self._scheduler is None:
            logger.warning(
                "[HeartbeatSessionResolver] scheduler not set, "
                "cannot handle session deletion for session_id=%s",
                sid,
            )
            return
        try:
            await self._scheduler.on_session_deleted(sid)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[HeartbeatSessionResolver] on_session_deleted callback failed "
                "session_id=%s: %s",
                sid,
                exc,
            )
