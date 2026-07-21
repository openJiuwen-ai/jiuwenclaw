# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TriggerEngine — 统一触发器引擎.

管理所有触发器的生命周期，提供 CRUD API，并在触发时调度 Avatar 执行任务。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any

from jiuwenavatar.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenavatar.common.enterprise import make_service_id, merge_routing, parse_tenant_list_filters
from jiuwenavatar.common.schema.message import EventType, Message, ReqMethod
from jiuwenavatar.gateway.trigger.models import TriggerConfig, TriggerStatus, TriggerType
from jiuwenavatar.gateway.trigger.store import TriggerStore
from jiuwenavatar.gateway.trigger.base import ITrigger, TriggerCallback
from jiuwenavatar.gateway.trigger.cron_trigger import CronTrigger
from jiuwenavatar.gateway.trigger.heartbeat_trigger import HeartbeatTrigger
from jiuwenavatar.gateway.trigger.webhook_trigger import WebhookTrigger
from jiuwenavatar.gateway.trigger.event_trigger import EventTrigger

logger = logging.getLogger(__name__)


class TriggerEngine:
    """Singleton engine that manages all triggers.

    Responsibilities:
    1. CRUD operations on triggers (persisted via TriggerStore)
    2. Start/stop trigger runtimes (Cron, Heartbeat, Webhook, Event)
    3. When a trigger fires, dispatch to the associated Avatar
    4. Expose WebSocket API handlers
    """

    _instance: TriggerEngine | None = None

    def __init__(self) -> None:
        self._store = TriggerStore()
        self._active_triggers: dict[str, ITrigger] = {}
        self._on_fire_callback: TriggerCallback | None = None
        # Dispatch dependencies (wired at Gateway startup via configure_dispatch).
        self._agent_client: Any | None = None
        self._message_handler: Any | None = None
        # Scheduling is owned by a single process (Gateway). Other processes
        # (e.g. AgentServer) only perform CRUD persistence and never start the
        # scheduling loops, so a trigger fires exactly once and in the process
        # that can actually dispatch it.
        self._scheduling_enabled: bool = False
        self._active_sig: dict[str, str] = {}  # trigger_id -> updated_at signature
        self._watch_task: asyncio.Task | None = None
        self._watch_interval: float = 5.0
        self._watch_last_mtime: float = 0.0
        self._mission_sessions: dict[str, str] = {}
        self._mission_runs: dict[str, str] = {}

    @classmethod
    def get_instance(cls) -> TriggerEngine:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_fire_callback(self, callback: TriggerCallback) -> None:
        """Set the callback that gets invoked when any trigger fires.

        This is typically set during Gateway startup to dispatch
        to the Avatar via the message pipeline.
        """
        self._on_fire_callback = callback

    def configure_dispatch(self, *, agent_client: Any, message_handler: Any) -> None:
        """Wire the dispatch dependencies and install the real fire callback.

        Without this, fired triggers only update ``last_triggered_at`` and never
        actually drive the Avatar. Called once during Gateway startup with the
        same ``agent_client`` / ``message_handler`` used by the cron scheduler.
        """
        self._agent_client = agent_client
        self._message_handler = message_handler
        self._scheduling_enabled = True
        self.set_fire_callback(self._dispatch_fire)

    async def _default_fire_callback(self, config: TriggerConfig, prompt: str) -> None:
        """Default fire callback — logs and updates trigger state."""
        logger.info(
            "Trigger %s (%s) fired for avatar %s",
            config.id, config.type, config.avatar_id,
        )
        config.last_triggered_at = datetime.now().isoformat()
        self._store.save_trigger(config)

    @staticmethod
    def _dispatch_timeout() -> float:
        """触发器派发等待 AgentServer 完成的超时（秒）。

        触发器驱动的任务（如代码检视）可能跑数十分钟，远超普通非流式请求的
        默认 600s 上限；这里单独放大，并允许用环境变量
        ``JIUWENAVATAR_TRIGGER_DISPATCH_TIMEOUT`` 覆盖。
        """
        default = 1800.0  # 30 分钟
        raw = os.getenv("JIUWENAVATAR_TRIGGER_DISPATCH_TIMEOUT")
        if not raw:
            return default
        try:
            value = float(raw)
            return value if value > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _resolve_avatar_route(avatar_id: str) -> dict[str, str]:
        """Resolve cloud routing fields for an Avatar, keeping standalone fallback."""

        try:
            from jiuwenavatar.server.runtime.persona import PersonaManager

            avatar = PersonaManager.get_instance().get_avatar(avatar_id) or {}
        except Exception:  # noqa: BLE001
            avatar = {}

        group_id = str(avatar.get("group_id") or "").strip()
        owner_user_id = str(avatar.get("owner_user_id") or "").strip()
        service_id = str(avatar.get("service_id") or "").strip()
        if not service_id and avatar_id:
            service_id = make_service_id(group_id or "default", avatar_id)
        agent_id = str(avatar.get("agent_id") or owner_user_id or "").strip()
        return {
            "service_id": service_id,
            "agent_id": agent_id,
            "group_id": group_id,
            "owner_user_id": owner_user_id,
        }

    async def _dispatch_fire(self, config: TriggerConfig, prompt: str) -> None:
        """Real fire callback — dispatch the trigger prompt to the Avatar.

        Sends a non-streaming ``chat.send`` to the AgentServer (so the Avatar
        actually executes the task), records the outcome on the trigger, and
        best-effort pushes the result text back to the configured channel.
        """
        logger.info(
            "Trigger %s (%s) fired for avatar %s",
            config.id, config.type, config.avatar_id,
        )
        config.last_triggered_at = datetime.now().isoformat()
        config.last_error = None
        ts = format(int(time.time() * 1000), "x")
        run_id = f"trigger-{config.id}-{ts}"
        session_id = f"trigger_{ts}_{config.id}"
        route = self._resolve_avatar_route(config.avatar_id)

        # 执行记录：为本次触发建一条 Mission（running），完成后更新状态并按需生成
        # Report，使「报告」页（missions.list / reports.list）能同步看到这次执行。
        mission_mgr = self._get_mission_manager()
        mission_id: str | None = None
        if mission_mgr is not None:
            try:
                from jiuwenavatar.gateway.report.models import MissionStatus

                mission = mission_mgr.create_mission(
                    avatar_id=config.avatar_id,
                    trigger_id=config.id,
                    prompt=prompt,
                    service_id=route.get("service_id") or None,
                    agent_id=route.get("agent_id") or None,
                    group_id=route.get("group_id") or None,
                    owner_user_id=route.get("owner_user_id") or None,
                )
                mission_id = mission.id
                mission_mgr.update_mission_runtime(
                    mission_id,
                    run_id=run_id,
                    session_id=session_id,
                    service_id=route.get("service_id") or None,
                    agent_id=route.get("agent_id") or None,
                )
                mission_mgr.update_mission_status(mission_id, MissionStatus.RUNNING)
                self._mission_sessions[mission_id] = session_id
                self._mission_runs[mission_id] = run_id
            except Exception:  # noqa: BLE001
                logger.exception("Trigger %s: failed to create mission", config.id)

        if self._agent_client is None:
            # Dispatch not configured — fall back to state-only behaviour.
            self._store.save_trigger(config)
            self._finalize_mission(mission_mgr, mission_id, ok=False, summary="dispatch not configured")
            return

        result_text = ""
        ok = False
        try:
            envelope = e2a_from_agent_fields(
                request_id=run_id,
                channel_id="__trigger__",
                session_id=session_id,
                req_method=ReqMethod.CHAT_SEND,
                params=merge_routing(
                    {
                        "avatar_id": config.avatar_id,
                        "content": prompt,
                        "query": prompt,
                        "mode": "agent",
                    },
                    service_id=route.get("service_id", ""),
                    agent_id=route.get("agent_id", ""),
                    avatar_id=config.avatar_id,
                    group_id=route.get("group_id", ""),
                    user_id=route.get("owner_user_id", ""),
                ),
                is_stream=False,
                timestamp=time.time(),
                metadata={"trigger": {"trigger_id": config.id, "name": config.name, "run_id": run_id}},
            )
            resp = await self._agent_client.send_request(envelope, timeout=self._dispatch_timeout())
            result_text = self._extract_text(resp.payload)
            ok = bool(getattr(resp, "ok", False))
            if not ok:
                config.last_error = result_text or "agent returned not-ok"
            logger.info(
                "Trigger %s dispatch finished ok=%s text_len=%d",
                config.id, ok, len(result_text or ""),
            )
        except Exception as exc:  # noqa: BLE001
            config.last_error = str(exc)
            result_text = result_text or str(exc)
            logger.exception("Trigger %s dispatch failed", config.id)
        finally:
            self._store.save_trigger(config)
            if mission_id is not None:
                self._mission_sessions.pop(mission_id, None)
                self._mission_runs.pop(mission_id, None)

        # 落地执行结果：更新 Mission 状态，成功且开启 generate_report 时生成报告。
        succeeded = ok and not config.last_error
        self._finalize_mission(
            mission_mgr,
            mission_id,
            ok=succeeded,
            summary=(result_text or config.last_error or "")[:2000],
        )
        if mission_mgr is not None and mission_id is not None and succeeded and config.generate_report:
            try:
                mission_mgr.create_report(
                    mission_id=mission_id,
                    avatar_id=config.avatar_id,
                    title=config.name or "执行报告",
                    summary=result_text or "",
                    sections=[{"name": "Prompt", "content": prompt}] if prompt else None,
                    metrics={"trigger_id": config.id, "run_id": run_id},
                )
            except Exception:  # noqa: BLE001
                logger.exception("Trigger %s: failed to create report", config.id)

        # Best-effort: push result text back to the configured channel.
        if (
            self._message_handler is not None
            and config.generate_report
            and result_text
            and config.target_channel
        ):
            try:
                msg = Message(
                    id=f"trigger-push-{run_id}",
                    type="event",
                    channel_id=config.target_channel,
                    session_id=None,
                    params={},
                    timestamp=time.time(),
                    ok=True,
                    payload={
                        "content": result_text,
                        "trigger": {"trigger_id": config.id, "name": config.name, "run_id": run_id},
                    },
                    event_type=EventType.CHAT_FINAL,
                    metadata=None,
                )
                await self._message_handler.publish_robot_messages(msg)
            except Exception:  # noqa: BLE001
                logger.exception("Trigger %s push result failed", config.id)

    async def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        """Cancel a running trigger mission and best-effort interrupt its AgentServer session."""
        mission_mgr = self._get_mission_manager()
        if mission_mgr is None:
            return {"error": "MissionManager unavailable"}
        mission = mission_mgr.get_mission(mission_id)
        if mission is None:
            return {"error": f"Mission not found: {mission_id}"}
        status = str(mission.get("status") or "")
        if status not in {"pending", "running"}:
            return {"mission": mission, "cancelled": False, "reason": "mission is not running"}

        session_id = str(mission.get("session_id") or self._mission_sessions.get(mission_id) or "")
        interrupt_sent = False
        interrupt_error = ""
        if self._agent_client is not None and session_id:
            try:
                envelope = e2a_from_agent_fields(
                    request_id=f"mission-cancel-{mission_id}",
                    channel_id="__trigger__",
                    session_id=session_id,
                    req_method=ReqMethod.CHAT_CANCEL,
                    params=merge_routing(
                        {"session_id": session_id, "intent": "cancel"},
                        service_id=str(mission.get("service_id") or ""),
                        agent_id=str(mission.get("agent_id") or ""),
                        avatar_id=str(mission.get("avatar_id") or ""),
                        group_id=str(mission.get("group_id") or ""),
                        user_id=str(mission.get("owner_user_id") or ""),
                    ),
                    is_stream=False,
                    timestamp=time.time(),
                    metadata={"mission_cancel": {"mission_id": mission_id}},
                )
                await self._agent_client.send_request(envelope, timeout=30.0)
                interrupt_sent = True
            except Exception as exc:  # noqa: BLE001
                interrupt_error = str(exc)
                logger.warning("Mission %s interrupt failed: %s", mission_id, exc)

        cancelled = mission_mgr.cancel_mission(mission_id)
        if cancelled is None:
            return {"error": f"Mission not found: {mission_id}"}
        return {
            "mission": cancelled.model_dump(),
            "cancelled": True,
            "interrupt_sent": interrupt_sent,
            "interrupt_error": interrupt_error,
        }

    @staticmethod
    def _extract_text(payload: Any) -> str:
        """Best-effort extraction of clean displayable text from an agent payload.

        Drills into nested dicts and also unwraps the common case where a field
        is a *stringified* Python dict such as
        ``"{'output': '...markdown...', 'result_type': 'answer'}"`` — otherwise
        that raw repr leaks into the report summary.
        """
        if isinstance(payload, dict):
            for key in ("content", "text", "output", "answer", "message"):
                if key in payload:
                    text = TriggerEngine._coerce_text(payload.get(key))
                    if text:
                        return text
            return ""
        return TriggerEngine._coerce_text(payload)

    @staticmethod
    def _coerce_text(value: Any) -> str:
        """Reduce an arbitrary agent value to clean displayable text."""
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("output", "text", "content", "answer", "message"):
                if key in value:
                    text = TriggerEngine._coerce_text(value.get(key))
                    if text:
                        return text
            return ""
        if isinstance(value, str):
            s = value.strip()
            # Unwrap a stringified dict repr, e.g. "{'output': '...'}".
            if s.startswith("{") and s.endswith("}") and "output" in s:
                import ast

                try:
                    parsed = ast.literal_eval(s)
                except (ValueError, SyntaxError):
                    parsed = None
                if isinstance(parsed, dict):
                    inner = TriggerEngine._coerce_text(parsed)
                    if inner:
                        return inner
            return value
        return ""

    @staticmethod
    def _get_mission_manager() -> Any | None:
        """Return the shared MissionManager, or None if unavailable."""
        try:
            from jiuwenavatar.gateway.report import MissionManager

            return MissionManager.get_instance()
        except Exception:  # noqa: BLE001
            logger.exception("TriggerEngine: MissionManager unavailable")
            return None

    @staticmethod
    def _finalize_mission(mission_mgr: Any | None, mission_id: str | None, *, ok: bool, summary: str = "") -> None:
        """Mark a mission completed/failed; never raises."""
        if mission_mgr is None or mission_id is None:
            return
        try:
            from jiuwenavatar.gateway.report.models import MissionStatus

            status = MissionStatus.COMPLETED if ok else MissionStatus.FAILED
            mission_mgr.update_mission_status(mission_id, status, result_summary=summary)
        except Exception:  # noqa: BLE001
            logger.exception("Trigger: failed to finalize mission %s", mission_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """Start all enabled triggers from storage."""
        triggers = self._store.list_triggers()
        started = 0
        for trigger_config in triggers:
            if trigger_config.enabled:
                try:
                    await self._start_trigger(trigger_config)
                    started += 1
                except Exception:
                    logger.exception("Failed to start trigger %s", trigger_config.id)
        logger.info("TriggerEngine: started %d/%d triggers", started, len(triggers))

    async def stop_all(self) -> None:
        """Stop all running triggers."""
        await self.stop_watching()
        for trigger_id, trigger in list(self._active_triggers.items()):
            try:
                await trigger.stop()
            except Exception:
                logger.exception("Failed to stop trigger %s", trigger_id)
        self._active_triggers.clear()
        logger.info("TriggerEngine: all triggers stopped")

    async def _start_trigger(self, config: TriggerConfig) -> None:
        """Create and start a trigger instance."""
        # Only the scheduling process (Gateway) starts runtime loops. Elsewhere
        # (AgentServer) creating a trigger just persists it; the Gateway picks it
        # up via the store watcher. This prevents double-scheduling and ensures
        # fire happens where dispatch dependencies exist.
        if not self._scheduling_enabled:
            return
        if config.id in self._active_triggers:
            return

        callback = self._on_fire_callback or self._default_fire_callback

        trigger: ITrigger
        if config.type == TriggerType.CRON:
            trigger = CronTrigger(config, callback)
        elif config.type == TriggerType.HEARTBEAT:
            trigger = HeartbeatTrigger(config, callback)
        elif config.type == TriggerType.WEBHOOK:
            trigger = WebhookTrigger(config, callback)
        elif config.type == TriggerType.EVENT:
            trigger = EventTrigger(config, callback)
        else:
            logger.error("Unknown trigger type: %s", config.type)
            return

        await trigger.start()
        self._active_triggers[config.id] = trigger
        self._active_sig[config.id] = config.updated_at or ""

    async def _stop_trigger(self, trigger_id: str) -> None:
        """Stop and remove a trigger instance."""
        self._active_sig.pop(trigger_id, None)
        trigger = self._active_triggers.pop(trigger_id, None)
        if trigger:
            await trigger.stop()

    # ------------------------------------------------------------------
    # Store synchronization (Gateway scheduling process only)
    # ------------------------------------------------------------------

    def _store_mtime(self) -> float:
        try:
            return self._store.path.stat().st_mtime
        except OSError:
            return 0.0

    async def reload_from_store(self) -> None:
        """Re-sync active runtimes with persisted store.

        Picks up triggers created/updated/deleted by any process (including the
        AgentServer process), starting/stopping/restarting scheduling loops as
        needed. No-op outside the scheduling process.
        """
        if not self._scheduling_enabled:
            return
        try:
            triggers = self._store.list_triggers()
        except Exception:
            logger.exception("TriggerEngine reload: failed to list triggers")
            return
        by_id = {t.id: t for t in triggers}

        # Stop triggers that were deleted or disabled externally.
        for tid in list(self._active_triggers.keys()):
            cfg = by_id.get(tid)
            if cfg is None or not cfg.enabled:
                await self._stop_trigger(tid)

        # Start new triggers, and restart ones whose config changed.
        for cfg in triggers:
            if not cfg.enabled:
                continue
            running = cfg.id in self._active_triggers
            if running and self._active_sig.get(cfg.id) != (cfg.updated_at or ""):
                await self._stop_trigger(cfg.id)
                running = False
            if not running:
                try:
                    await self._start_trigger(cfg)
                except Exception:
                    logger.exception("TriggerEngine reload: failed to start %s", cfg.id)

    async def start_watching(self, *, interval: float = 5.0) -> None:
        """Start polling triggers.json for external changes (Gateway only)."""
        if not self._scheduling_enabled or self._watch_task is not None:
            return
        self._watch_interval = max(1.0, float(interval))
        self._watch_last_mtime = self._store_mtime()
        self._watch_task = asyncio.create_task(self._watch_loop(), name="trigger-store-watch")
        logger.info("TriggerEngine: store watcher started (interval=%.1fs)", self._watch_interval)

    async def stop_watching(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    async def _watch_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._watch_interval)
                mtime = self._store_mtime()
                if mtime and mtime != self._watch_last_mtime:
                    self._watch_last_mtime = mtime
                    logger.info("TriggerEngine: triggers.json changed, reloading")
                    await self.reload_from_store()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("TriggerEngine watch loop error")
                await asyncio.sleep(self._watch_interval)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_triggers(
        self,
        *,
        avatar_id: str | None = None,
        group_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List triggers, optionally filtered by avatar and tenant."""
        if avatar_id:
            triggers = self._store.list_triggers_by_avatar(
                avatar_id,
                group_id=group_id,
                owner_user_id=owner_user_id,
            )
        else:
            triggers = self._store.list_triggers(group_id=group_id, owner_user_id=owner_user_id)
        return [t.model_dump() for t in triggers]

    def _resolve_tenant_scope(self, kwargs: dict[str, Any]) -> tuple[str | None, str | None, bool]:
        tenant = parse_tenant_list_filters(kwargs)
        if tenant is None:
            return None, None, True
        if not tenant.is_valid:
            return None, None, False
        owner = tenant.user_id or None
        return tenant.group_id, owner, True

    def _trigger_matches_tenant(
        self,
        trigger: TriggerConfig,
        *,
        group_id: str | None,
        owner_user_id: str | None,
    ) -> bool:
        if group_id is not None and (trigger.group_id or "") != group_id:
            return False
        if owner_user_id and (trigger.owner_user_id or "") != owner_user_id:
            return False
        return True

    def _get_trigger_for_tenant(self, trigger_id: str, **kwargs: Any) -> TriggerConfig | None:
        trigger = self._store.get_trigger(trigger_id)
        if trigger is None:
            return None
        group_id, owner_user_id, allowed = self._resolve_tenant_scope(kwargs)
        if not allowed:
            return None
        if group_id is None:
            return trigger
        if not self._trigger_matches_tenant(trigger, group_id=group_id, owner_user_id=owner_user_id):
            return None
        return trigger

    def get_trigger(self, trigger_id: str, **kwargs: Any) -> dict[str, Any] | None:
        """Get a single trigger by ID."""
        trigger = self._get_trigger_for_tenant(trigger_id, **kwargs)
        return trigger.model_dump() if trigger else None

    async def create_trigger(self, **kwargs: Any) -> dict[str, Any]:
        """Create a new trigger, save it, and start it if enabled."""
        group_id, owner_user_id, allowed = self._resolve_tenant_scope(kwargs)
        if not allowed:
            raise ValueError("group_id is required in enterprise mode")
        if group_id is not None:
            kwargs.setdefault("group_id", group_id)
            if owner_user_id:
                kwargs.setdefault("owner_user_id", owner_user_id)

        config = TriggerConfig(**kwargs)

        # Validate required fields per type
        self._validate_trigger_config(config)

        self._store.save_trigger(config)

        if config.enabled:
            try:
                await self._start_trigger(config)
            except Exception:
                logger.exception("Failed to start newly created trigger %s", config.id)
                config.status = TriggerStatus.ERROR
                config.last_error = "Failed to start"
                self._store.save_trigger(config)

        logger.info("Created trigger %s (%s) for avatar %s", config.id, config.type, config.avatar_id)
        return config.model_dump()

    async def update_trigger(self, trigger_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update a trigger. Restarts it if running."""
        existing = self._get_trigger_for_tenant(trigger_id, **kwargs)
        if existing is None:
            raise ValueError(f"Trigger not found: {trigger_id}")

        # Merge updates
        update_data = existing.model_dump()
        update_data.update(kwargs)
        update_data.pop("trigger_id", None)
        update_data["updated_at"] = datetime.now().isoformat()

        config = TriggerConfig(**update_data)
        self._validate_trigger_config(config)

        # Restart if running
        if trigger_id in self._active_triggers:
            await self._stop_trigger(trigger_id)
            if config.enabled:
                await self._start_trigger(config)

        self._store.save_trigger(config)
        return config.model_dump()

    async def delete_trigger(self, trigger_id: str, **kwargs: Any) -> None:
        """Delete a trigger and stop it if running."""
        existing = self._get_trigger_for_tenant(trigger_id, **kwargs)
        if existing is None:
            raise ValueError(f"Trigger not found: {trigger_id}")
        if trigger_id in self._active_triggers:
            await self._stop_trigger(trigger_id)
        self._store.delete_trigger(trigger_id)
        logger.info("Deleted trigger %s", trigger_id)

    # ------------------------------------------------------------------
    # Webhook / Event dispatch
    # ------------------------------------------------------------------

    async def handle_webhook_request(
        self, path: str, body: bytes, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Handle an incoming webhook HTTP request.

        Finds the matching WebhookTrigger by path and delegates.
        """
        for trigger_id, trigger in self._active_triggers.items():
            if isinstance(trigger, WebhookTrigger) and trigger.config.webhook_path == path:
                return await trigger.handle_request(body, headers)

        return {"error": f"No webhook trigger registered at path: {path}", "status": 404}

    async def emit_event(self, source: str, event_type: str, event_data: dict[str, Any]) -> None:
        """Emit an event to all matching EventTriggers."""
        for trigger_id, trigger in self._active_triggers.items():
            if isinstance(trigger, EventTrigger) and trigger.matches_event(source, event_type):
                try:
                    await trigger.handle_event(event_data)
                except Exception:
                    logger.exception("EventTrigger %s failed to handle event", trigger_id)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_trigger_config(self, config: TriggerConfig) -> None:
        """Validate required fields based on trigger type."""
        if config.type == TriggerType.CRON and not config.cron_expr:
            raise ValueError("cron_expr is required for cron triggers")
        if config.type == TriggerType.HEARTBEAT and (not config.interval_seconds or config.interval_seconds <= 0):
            raise ValueError("interval_seconds > 0 is required for heartbeat triggers")
        if config.type == TriggerType.WEBHOOK and not config.webhook_path:
            raise ValueError("webhook_path is required for webhook triggers")
        if config.type == TriggerType.EVENT and (not config.event_source or not config.event_type):
            raise ValueError("event_source and event_type are required for event triggers")
        if not config.trigger_prompt:
            raise ValueError("trigger_prompt is required")

    # ------------------------------------------------------------------
    # WebSocket API Handlers
    # ------------------------------------------------------------------

    async def handle_triggers_list(self, **kwargs: Any) -> dict[str, Any]:
        avatar_id = kwargs.get("avatar_id")
        group_id, owner_user_id, allowed = self._resolve_tenant_scope(kwargs)
        if not allowed:
            return {"triggers": []}
        return {
            "triggers": self.list_triggers(
                avatar_id=avatar_id,
                group_id=group_id,
                owner_user_id=owner_user_id,
            )
        }

    async def handle_triggers_get(self, *, trigger_id: str, **kwargs: Any) -> dict[str, Any]:
        trigger = self.get_trigger(trigger_id, **kwargs)
        if trigger is None:
            return {"error": f"Trigger not found: {trigger_id}"}
        return {"trigger": trigger}

    async def handle_triggers_create(self, **kwargs: Any) -> dict[str, Any]:
        try:
            trigger = await self.create_trigger(**kwargs)
            return {"trigger": trigger}
        except ValueError as e:
            return {"error": str(e)}

    async def handle_triggers_update(self, *, trigger_id: str, **kwargs: Any) -> dict[str, Any]:
        try:
            trigger = await self.update_trigger(trigger_id, **kwargs)
            return {"trigger": trigger}
        except ValueError as e:
            return {"error": str(e)}

    async def handle_triggers_delete(self, *, trigger_id: str, **kwargs: Any) -> dict[str, Any]:
        try:
            await self.delete_trigger(trigger_id, **kwargs)
            return {"success": True}
        except ValueError as e:
            return {"error": str(e)}


def get_trigger_engine() -> TriggerEngine:
    """Get the singleton TriggerEngine instance."""
    return TriggerEngine.get_instance()
