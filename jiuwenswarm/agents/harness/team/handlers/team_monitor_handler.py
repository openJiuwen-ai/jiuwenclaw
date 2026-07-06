# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Monitor 处理器.

处理 Team Monitor 的事件流和状态查询，将团队状态转换为前端可消费的格式.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openjiuwen.agent_teams.monitor import TeamMonitor
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType

from jiuwenswarm.agents.harness.team.event_types import (
    get_team_event_type,
    get_event_category,
)
from jiuwenswarm.agents.harness.team.handlers.base_monitor_handler import BaseMonitorHandler

logger = logging.getLogger(__name__)


class TeamMonitorHandler(BaseMonitorHandler):
    """Team Monitor 处理器.

    封装 Monitor 的创建、事件处理和状态查询，提供简化的接口给前端.
    """

    def __init__(self, monitor: TeamMonitor, session_id: str):
        super().__init__(monitor, session_id)

    # ------------------------------------------------------------------
    # Collect loop — consumes monitor.events()
    # ------------------------------------------------------------------

    async def _collect_events(self) -> None:
        """后台任务：收集 Monitor 事件."""
        try:
            async for event in self._monitor.events():
                if not self._running:
                    break
                event_dict = await self._convert_event_to_dict(event)
                if event_dict:
                    await self._event_queue.put(event_dict)
        except Exception as e:
            logger.error(
                "[TeamMonitorHandler] 事件收集失败: session_id=%s, error=%s",
                self._session_id,
                e,
            )

    # ------------------------------------------------------------------
    # Event conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_member_spawned(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base["member_id"] = event.member_name
        return base

    @staticmethod
    def _handle_member_status_changed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base.update({
            "member_id": event.member_name,
            "old_status": event.old_status,
            "new_status": event.new_status,
        })
        return base

    @staticmethod
    def _handle_member_execution_changed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base.update({
            "member_id": event.member_name,
            "old_status": event.old_status,
            "new_status": event.new_status,
        })
        return base

    @staticmethod
    def _handle_member_restarted(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base.update({
            "member_id": event.member_name,
            "reason": event.reason,
            "restart_count": event.restart_count,
        })
        return base

    @staticmethod
    def _handle_member_shutdown(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base.update({
            "member_id": event.member_name,
            "force": event.force,
        })
        return base

    @staticmethod
    def _handle_task_created(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base.update({
            "task_id": event.task_id,
            "status": event.status,
        })
        return base

    @staticmethod
    def _handle_task_claimed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base["task_id"] = event.task_id
        return base

    @staticmethod
    def _handle_task_completed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base["task_id"] = event.task_id
        return base

    @staticmethod
    def _handle_task_cancelled(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base["task_id"] = event.task_id
        return base

    @staticmethod
    def _handle_task_unblocked(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        base["task_id"] = event.task_id
        return base

    async def _handle_message(self, base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        message_content, message_protocol = await self._get_message_display(event.message_id)
        base.update({
            "message_id": event.message_id,
            "from_member": event.from_member_name,
            "to_member": event.to_member_name,
            "content": message_content,
            "protocol": message_protocol,
        })
        return base

    async def _handle_broadcast(self, base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        message_content, message_protocol = await self._get_message_display(event.message_id)
        base.update({
            "message_id": event.message_id,
            "from_member": event.from_member_name,
            "content": message_content,
            "protocol": message_protocol,
        })
        return base

    async def _get_message_display(self, message_id: str | None) -> tuple[str, str]:
        if not message_id or not self._monitor:
            return "", "plain"
        try:
            from openjiuwen.agent_teams.context import set_session_id, reset_session_id
            token = set_session_id(self._session_id)
            try:
                messages = await self._monitor.get_messages()
                for message in messages:
                    if message.message_id == message_id:
                        protocol = self._normalize_message_protocol(message.protocol)
                        content = self._normalize_message_content(message.content or "", protocol)
                        return content, protocol
                return "", "plain"
            finally:
                reset_session_id(token)
        except Exception as e:
            logger.warning(
                "[TeamMonitorHandler] 查询消息内容失败: message_id=%s, error=%s",
                message_id,
                e,
            )
            return "", "plain"

    @staticmethod
    def _normalize_message_protocol(protocol: Any) -> str:
        value = str(protocol or "plain").strip().lower()
        return value or "plain"

    @staticmethod
    def _normalize_message_content(content: str, protocol: str) -> str:
        if protocol != "json" or not content.strip():
            return content
        try:
            return json.dumps(json.loads(content), ensure_ascii=False)
        except (TypeError, ValueError):
            return content

    async def _convert_event_to_dict(self, event: MonitorEvent) -> dict[str, Any] | None:
        team_event_type = get_team_event_type(event.event_type)
        if team_event_type is None:
            return None

        event_category = get_event_category(team_event_type)

        event_data: dict[str, Any] = {
            "type": team_event_type.value,
            "team_id": event.team_name,
        }

        if event.member_name:
            event_data["member_id"] = event.member_name

        event_handlers = {
            MonitorEventType.MEMBER_SPAWNED: self._handle_member_spawned,
            MonitorEventType.MEMBER_STATUS_CHANGED: self._handle_member_status_changed,
            MonitorEventType.MEMBER_EXECUTION_CHANGED: self._handle_member_execution_changed,
            MonitorEventType.MEMBER_RESTARTED: self._handle_member_restarted,
            MonitorEventType.MEMBER_SHUTDOWN: self._handle_member_shutdown,
            MonitorEventType.TASK_CREATED: self._handle_task_created,
            MonitorEventType.TASK_CLAIMED: self._handle_task_claimed,
            MonitorEventType.TASK_COMPLETED: self._handle_task_completed,
            MonitorEventType.TASK_CANCELLED: self._handle_task_cancelled,
            MonitorEventType.TASK_UNBLOCKED: self._handle_task_unblocked,
            MonitorEventType.MESSAGE: self._handle_message,
            MonitorEventType.BROADCAST: self._handle_broadcast,
        }

        handler = event_handlers.get(event.event_type)
        if handler is None:
            return None

        if asyncio.iscoroutinefunction(handler):
            event_data = await handler(event_data, event)
        else:
            event_data = handler(event_data, event)

        return {
            "event_type": event_category.value,
            "session_id": self._session_id,
            "event": event_data,
        }

    # ------------------------------------------------------------------
    # Properties and snapshot
    # ------------------------------------------------------------------

    @property
    def team_id(self) -> str | None:
        return self._monitor.team_name if self._monitor else None

    async def get_team_snapshot(self) -> dict[str, Any] | None:
        """获取当前团队状态快照，用于刷新后恢复成员列表和任务列表。"""
        if self._monitor is None:
            return None
        try:
            members = await self._monitor.get_members()
            team_info = await self._monitor.get_team_info()
            leader_name = team_info.leader_member_name if team_info else None
            if leader_name:
                members = [m for m in members if m.member_name != leader_name]
            tasks = await self._monitor.get_tasks() or []
            return {
                "members": [
                    {
                        "member_id": m.member_name,
                        "name": m.display_name,
                        "status": m.status,
                        "execution_status": m.execution_status,
                        "mode": m.mode,
                    }
                    for m in members
                ],
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "team_name": t.team_name,
                        "title": t.title,
                        "content": t.content,
                        "status": t.status,
                        "assignee": t.assignee,
                        "updated_at": t.updated_at,
                    }
                    for t in tasks
                ],
                "team_id": self._monitor.team_name,
            }
        except Exception as e:
            logger.warning(
                "[TeamMonitorHandler] get_team_snapshot failed: session_id=%s, error=%s",
                self._session_id,
                e,
            )
            return None

