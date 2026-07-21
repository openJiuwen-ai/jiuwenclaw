from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
from jiuwenclaw.gateway.cron.store import CronJobStore
from jiuwenclaw.gateway.cron.store_base import CronJobStoreBackend
from jiuwenclaw.gateway.cron.cron_expr import (
    clamp_wake_offset_for_delay_seconds,
    iso_to_seven_field_cron,
    validate_cron_schedule_not_stale,
)
from jiuwenclaw.gateway.cron.scheduler import _cron_next_push_dt, CronSchedulerService
from jiuwenclaw.gateway.cron.models import (
    CronTargetChannel,
    is_valid_target_channel_id,
    normalize_target_channel_id,
    upgrade_bare_feishu_target_for_multi_bot_config,
)
from jiuwenclaw.agentserver.tools.cron_tool_context import (
    get_cron_tool_channel_id,
    get_cron_tool_metadata,
    get_cron_tool_session_id,
)
from jiuwenclaw.agentserver.gateway_push import (
    GatewayPushTransport,
    WebSocketGatewayPushTransport,
)
from jiuwenclaw.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)

# 按 asyncio Task 隔离：多 session 并发时不能用单例字段存路由，否则后到的请求会覆盖先到的 session_id。
_cron_route_ctx: contextvars.ContextVar[CronToolRoute | None] = contextvars.ContextVar(
    "jiuwenclaw_cron_route", default=None
)


@dataclass(frozen=True, slots=True)
class CronToolRoute:
    """当前请求同步到 Gateway 时使用的路由（含企业三元组）。"""

    request_id: str = ""
    channel_id: str = CronTargetChannel.WEB.value
    session_id: str | None = None
    chat_type: str | None = None  # "group" 表示群聊, "p2p" 或 None 表示私聊
    group_id: str | None = None
    bot_id: str | None = None
    user_id: str | None = None


class CronTools:
    """Agent-side cron tools with local cron_jobs.json as source of truth.

    路由用 ContextVar 按 Task 隔离（与 interface 中 ``push_cron_route`` / ``reset_cron_route`` 配对）；
    同进程一套 LocalFunction，并发安全依赖当前 asyncio 任务的上下文而非单例可变字段。
    
    包含内置调度器，即使 Gateway 未启动也能执行定时任务。
    """

    def __init__(
        self,
        gateway_push: GatewayPushTransport | None = None,
        *,
        agent_client: Any | None = None,
        message_handler: Any | None = None,
    ) -> None:
        self._gateway_push: GatewayPushTransport = gateway_push or WebSocketGatewayPushTransport()
        self._local_store = CronJobStore(
            path=get_user_workspace_dir() / "agent" / "home" / "cron_jobs.json"
        )
        # 内置调度器，用于在 Agent-side 执行定时任务
        self._scheduler: CronSchedulerService | None = None
        self._agent_client = agent_client
        self._message_handler = message_handler
        self._scheduler_started = False
        self._shared_store: CronJobStoreBackend | None = None
        self._shared_store_tried = False

    async def ensure_scheduler(self) -> CronSchedulerService | None:
        """Ensure the scheduler is started."""
        # 企业就绪时由 Gateway 调度权威；Agent 不启本地 scheduler，避免双触发
        if self._enterprise_ready():
            return None
        if self._scheduler is not None and self._scheduler.is_running():
            return self._scheduler
        
        if self._scheduler_started:
            # Already tried to start but failed or stopped
            return self._scheduler
        
        # Try to create and start scheduler
        try:
            # Lazy import to avoid circular dependency
            from jiuwenclaw.gateway.agent_client import AgentServerClient
            
            agent_client = self._agent_client
            message_handler = self._message_handler
            
            # If not provided, try to get from singletons
            if agent_client is None:
                try:
                    agent_client = AgentServerClient.get_instance()
                except RuntimeError:
                    agent_client = None
            
            if message_handler is None:
                try:
                    from jiuwenclaw.gateway.message_handler import MessageHandler
                    message_handler = MessageHandler.get_instance()
                except RuntimeError:
                    message_handler = None
            
            if agent_client is None:
                logger.warning("[CronTools] Cannot start scheduler: AgentServerClient not available")
                self._scheduler_started = True  # Mark as tried
                return None
            
            self._scheduler = CronSchedulerService(
                store=self._local_store,
                agent_client=agent_client,
                message_handler=message_handler,
            )
            await self._scheduler.start()
            logger.info("[CronTools] Scheduler started successfully")
            self._scheduler_started = True
            return self._scheduler
            
        except Exception as exc:
            logger.warning("[CronTools] Failed to start scheduler: %s", exc)
            self._scheduler_started = True  # Mark as tried
            return None

    async def _reload_scheduler(self) -> None:
        """Reload scheduler if it's running."""
        scheduler = await self.ensure_scheduler()
        if scheduler is not None:
            try:
                await scheduler.reload()
                logger.debug("[CronTools] Scheduler reloaded")
            except Exception as exc:
                logger.warning("[CronTools] Failed to reload scheduler: %s", exc)

    @staticmethod
    def push_cron_route(route: CronToolRoute) -> contextvars.Token:
        """进入一轮 Agent 执行前调用；须与 ``reset_cron_route`` 配对（通常在 finally 中）。"""
        return _cron_route_ctx.set(route)

    @staticmethod
    def reset_cron_route(token: contextvars.Token) -> None:
        _cron_route_ctx.reset(token)

    @staticmethod
    def _route() -> CronToolRoute:
        return CronTools.resolve_route()

    @classmethod
    def resolve_route(cls) -> CronToolRoute:
        r = _cron_route_ctx.get()
        if r is not None and str(r.request_id or "").strip():
            return r
        fallback = cls._runtime_route_fallback()
        if fallback is not None:
            return fallback
        return r if r is not None else CronToolRoute()

    @classmethod
    def _runtime_route_fallback(cls) -> CronToolRoute | None:
        """K8s 多 Pod 下 delete/list 等未 push_cron_route 时，从 Deep 请求上下文补全路由。"""
        metadata = get_cron_tool_metadata()
        if not isinstance(metadata, dict):
            return None
        request_id = str(metadata.get("request_id") or "").strip()
        if not request_id:
            return None
        channel_id = (
            str(get_cron_tool_channel_id() or "").strip()
            or CronTargetChannel.WEB.value
        )
        session_raw = get_cron_tool_session_id()
        session_id = (
            str(session_raw).strip()
            if isinstance(session_raw, str) and session_raw.strip()
            else None
        )
        chat_type = str(metadata.get("chat_type") or "").strip() or None
        from jiuwenclaw.gateway.cron.enterprise_gate import extract_routing_triple

        group_id, bot_id, user_id = extract_routing_triple(metadata)
        return CronToolRoute(
            request_id=request_id,
            channel_id=channel_id,
            session_id=session_id,
            chat_type=chat_type,
            group_id=group_id,
            bot_id=bot_id,
            user_id=user_id,
        )

    async def _shared_gateway_store(self) -> CronJobStoreBackend | None:
        if self._shared_store_tried:
            return self._shared_store
        self._shared_store_tried = True
        try:
            from jiuwenclaw.gateway.cron.factory import create_gateway_cron_store

            self._shared_store = await create_gateway_cron_store()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CronTools] shared gateway store unavailable: %s", exc)
            self._shared_store = None
        return self._shared_store

    async def _send_split(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        from jiuwenclaw.e2a.constants import E2A_RESPONSE_KIND_CRON

        r = self.resolve_route()
        payload = {
            "request_id": r.request_id,
            "channel_id": r.channel_id,
            "session_id": r.session_id,
            "response_kind": E2A_RESPONSE_KIND_CRON,
            "body": {
                "action": action,
                "status": "ok",
                "data": dict(params or {}),
                "message": "",
            },
        }
        await self._gateway_push.send_push(payload)
        return {"action": action, "status": "forwarded", "data": None, "message": "cron request forwarded to gateway"}

    async def _send(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._send_split(action, params)

    @staticmethod
    def _is_valid_target(value: str) -> bool:
        return is_valid_target_channel_id(value)

    def _default_target_from_channel(self) -> str:
        channel_raw = self._resolve_channel_id()
        channel = channel_raw.lower()
        if channel.startswith("feishu:"):
            return normalize_target_channel_id(channel_raw, default=CronTargetChannel.WEB.value)
        if channel.startswith("feishu"):
            return CronTargetChannel.FEISHU.value
        if channel.startswith("wecom"):
            return CronTargetChannel.WECOM.value
        if channel.startswith("xiaoyi"):
            return CronTargetChannel.XIAOYI.value
        if channel.startswith("whatsapp"):
            return CronTargetChannel.WHATSAPP.value
        return CronTargetChannel.WEB.value

    def _resolve_channel_id(self) -> str:
        r = self._route()
        channel_raw = str(r.channel_id or "").strip()
        if channel_raw:
            return channel_raw
        request_id = str(r.request_id or "").strip()
        if ":" not in request_id:
            return ""
        return request_id.rsplit(":", 1)[0].strip()

    def _normalize_targets_param(self, raw: Any) -> str:
        target = str(raw or "").strip()
        if self._is_valid_target(target):
            normalized = normalize_target_channel_id(target, default=CronTargetChannel.WEB.value)
            logger.info(
                "[CronTools] normalize targets from explicit value: raw=%s normalized=%s route_channel=%s",
                target,
                normalized,
                self._route().channel_id,
            )
            return normalized
        fallback = self._default_target_from_channel()
        logger.info(
            "[CronTools] normalize targets from fallback: raw=%s fallback=%s route_channel=%s request_id=%s",
            target,
            fallback,
            self._route().channel_id,
            self._route().request_id,
        )
        return fallback

    def _routing_session_id_for_targets(self, targets_str: str) -> str | None:
        """Bind delivery session for web and feishu:<app_id> cron targets."""
        t = str(targets_str or "").strip()
        sid = self._route().session_id
        if not isinstance(sid, str) or not sid.strip():
            return None
        if t == CronTargetChannel.WEB.value or t.startswith("feishu:"):
            return sid.strip()
        return None

    def _upgrade_bare_feishu_to_route_app_id(self, targets_str: str) -> str:
        """多 bot 仅注册 feishu:<app_id>；若 LLM 显式写了 targets=feishu，则改为当前会话 bot。"""
        t = str(targets_str or "").strip()
        if t != CronTargetChannel.FEISHU.value:
            return t
        ch = self._resolve_channel_id()
        if ch.startswith("feishu:"):
            out = normalize_target_channel_id(ch, default=CronTargetChannel.WEB.value)
            if out.startswith("feishu:"):
                logger.info(
                    "[CronTools] upgrade bare feishu target for multi-bot: %s -> %s (route=%s)",
                    t,
                    out,
                    ch,
                )
                return out
        # 路由无 app_id（如 Web）但配置仅一个多 bot 子节点时，与网关 CronController 逻辑一致。
        return upgrade_bare_feishu_target_for_multi_bot_config(t)

    def _routing_identity_payload(self) -> dict[str, str]:
        r = self._route()
        out: dict[str, str] = {}
        if r.group_id:
            out["group_id"] = r.group_id
        if r.bot_id:
            out["bot_id"] = r.bot_id
        if r.user_id:
            out["user_id"] = r.user_id
        return out

    @staticmethod
    def _enterprise_ready() -> bool:
        from jiuwenclaw.gateway.cron.enterprise_gate import enterprise_cron_enabled

        return enterprise_cron_enabled()

    async def _list_jobs_enterprise(self) -> list[dict[str, Any]]:
        """企业路径：按 (group_id, bot_id, user_id) 只读查询。

        有 ``jiuwenclaw_id`` 时走 GatewayDb（带实例隔离）；无 jid 时绕过
        ``list_records`` 的空短路，直接用 DB handler 按三元组查。
        """
        from jiuwenclaw.gateway.cron.enterprise_gate import (
            extract_routing_triple,
            get_bound_jiuwenclaw_id,
            routing_triple_complete,
        )
        from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module

        identity = self._routing_identity_payload()
        g, b, u = extract_routing_triple(identity)
        if not routing_triple_complete(g, b, u):
            raise ValueError("enterprise cron list requires group_id, bot_id and user_id")

        filters: dict[str, Any] = {"group_id": g, "bot_id": b, "user_id": u}
        order_by: list[tuple[str, bool]] = [("updated_at", True)]
        gdb_mod = import_manager_ws_client_module("core.enterprise_config.gateway_db")
        jid = get_bound_jiuwenclaw_id()
        if jid:
            rows = await gdb_mod.GatewayDb.current().list_records(
                "cron_job",
                filters=filters,
                order_by=order_by,
            )
        else:
            logger.info(
                "[CronTools] enterprise list without jiuwenclaw_id; "
                "query cron_job by routing triple only"
            )
            db_mod = import_manager_ws_client_module("infrastructure.db")
            handler = await db_mod.ensure_db_handler(log_prefix="cron_job")
            raw_rows = await handler.list_records(
                "cron_job",
                filters,
                limit=10_000,
                offset=0,
                order_by=order_by,
            )
            row_to_dict = getattr(gdb_mod, "_row_to_dict", None)
            rows = []
            for raw in raw_rows or []:
                if callable(row_to_dict):
                    rows.append(row_to_dict(raw))
                elif isinstance(raw, dict):
                    rows.append(raw)
                elif hasattr(raw, "to_dict") and callable(raw.to_dict):
                    rows.append(raw.to_dict())
                else:
                    rows.append(raw)

        out: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id") or "").strip()
            if not job_id:
                continue
            item = {
                "id": job_id,
                "name": row.get("name"),
                "enabled": bool(row.get("enabled", False)),
                "expired": bool(row.get("expired", False)),
                "cron_expr": row.get("cron_expr"),
                "timezone": row.get("timezone"),
                "wake_offset_seconds": row.get("wake_offset_seconds"),
                "description": row.get("description") or "",
                "targets": row.get("targets"),
                "session_id": row.get("session_id"),
                "chat_type": row.get("chat_type"),
                "mode": row.get("mode") or "agent",
                "delete_after_run": bool(row.get("delete_after_run", False)),
                "group_id": row.get("group_id"),
                "bot_id": row.get("bot_id"),
                "user_id": row.get("user_id"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            out.append(item)
        return out

    async def list_jobs(self) -> Any:
        # 与 ensure_scheduler 对齐：仅企业就绪（jid 已绑定）走企业只读
        if self._enterprise_ready():
            return await self._list_jobs_enterprise()
        jobs = await self._local_store.list_jobs()
        if jobs:
            return [j.to_dict() for j in jobs]
        shared = await self._shared_gateway_store()
        if shared is None:
            return []
        try:
            shared_jobs = await shared.list_jobs()
            return [j.to_dict() for j in shared_jobs]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CronTools] list jobs from shared store failed: %s", exc)
            return []

    async def get_job(self, job_id: str) -> Any:
        if self._enterprise_ready():
            jobs = await self._list_jobs_enterprise()
            for item in jobs:
                if str(item.get("id") or "") == str(job_id or "").strip():
                    return item
            return None
        job = await self._local_store.get_job(job_id)
        if job is not None:
            return job.to_dict()
        shared = await self._shared_gateway_store()
        if shared is None:
            return None
        try:
            shared_job = await shared.get_job(job_id)
            return shared_job.to_dict() if shared_job else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CronTools] get job from shared store failed: %s", exc)
            return None

    async def create_job(self, params: dict[str, Any]) -> Any:
        from jiuwenclaw.gateway.cron.cron_job_mutations import build_new_cron_job
        from jiuwenclaw.gateway.cron.enterprise_gate import routing_triple_complete

        normalized = dict(params or {})
        normalized.pop("session_id", None)
        targets_str = self._normalize_targets_param(normalized.get("targets"))
        targets_str = self._upgrade_bare_feishu_to_route_app_id(str(targets_str))
        normalized["targets"] = targets_str
        timezone = str(normalized.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
        cron_expr = str(normalized.get("cron_expr") or "").strip()
        delete_after_run = normalized.get("delete_after_run")
        delay_raw = normalized.get("delay_seconds")
        if delay_raw is not None:
            delay = float(delay_raw)
            if delay <= 0:
                raise ValueError("delay_seconds must be positive")
            run_at = datetime.now(tz=ZoneInfo(timezone)) + timedelta(seconds=delay)
            cron_expr = iso_to_seven_field_cron(run_at.isoformat(), timezone=timezone)
            delete_after_run = True if delete_after_run is None else delete_after_run
            # 企业就绪：短 delay 时 harness 默认 wake_offset=300 会使 wake_at 早于 now；收敛到 ≤ delay
            if self._enterprise_ready():
                raw_wake = normalized.get("wake_offset_seconds")
                clamped_wake = clamp_wake_offset_for_delay_seconds(raw_wake, delay)
                if raw_wake is not None:
                    try:
                        before = int(raw_wake)
                    except (TypeError, ValueError):
                        before = raw_wake
                else:
                    before = None
                if before != clamped_wake:
                    logger.info(
                        "[CronTools] clamp wake_offset for delay_seconds=%s: %s -> %s",
                        delay,
                        before,
                        clamped_wake,
                    )
                normalized["wake_offset_seconds"] = clamped_wake
                logger.info(
                    "[CronTools] schedule one-shot via delay_seconds=%s cron_expr=%s wake_offset=%s",
                    delay,
                    cron_expr,
                    clamped_wake,
                )
            else:
                logger.info(
                    "[CronTools] schedule one-shot via delay_seconds=%s cron_expr=%s",
                    delay,
                    cron_expr,
                )
        elif not cron_expr:
            raise ValueError("cron_expr or delay_seconds is required")
        else:
            validate_cron_schedule_not_stale(cron_expr=cron_expr, timezone=timezone)
        logger.info(
            "[CronTools] create_job: route(channel=%s session=%s request=%s) input.targets=%s normalized.targets=%s",
            self._route().channel_id,
            self._route().session_id,
            self._route().request_id,
            params.get("targets") if isinstance(params, dict) else None,
            targets_str,
        )
        session_kw: dict[str, Any] = {}
        routing_sid = self._routing_session_id_for_targets(targets_str)
        if routing_sid:
            session_kw["session_id"] = routing_sid
        chat_type = self._route().chat_type
        if chat_type:
            session_kw["chat_type"] = chat_type

        identity = self._routing_identity_payload()

        # 企业就绪写路径：不写本地权威；以 Gateway push 为写意图，Gateway 落库
        if self._enterprise_ready():
            if not routing_triple_complete(
                identity.get("group_id"),
                identity.get("bot_id"),
                identity.get("user_id"),
            ):
                raise ValueError("enterprise cron requires group_id, bot_id and user_id")
            job = build_new_cron_job(
                job_id=str(normalized.get("id") or "").strip() or None,
                name=str(normalized.get("name") or "").strip(),
                cron_expr=cron_expr,
                timezone=timezone,
                description=str(normalized.get("description") or ""),
                targets=targets_str,
                enabled=bool(normalized.get("enabled", True)),
                wake_offset_seconds=normalized.get("wake_offset_seconds"),
                delete_after_run=delete_after_run,
                mode=normalized.get("mode"),
                group_id=identity.get("group_id"),
                bot_id=identity.get("bot_id"),
                user_id=identity.get("user_id"),
                **session_kw,
            )
            try:
                await self._send("create", job.to_dict())
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"enterprise cron create push failed: {exc}") from exc
            return job.to_dict()

        # 未就绪 / 非企业：本地 file store；身份仅经 push 带给 Gateway（若有）
        job = await self._local_store.create_job(
            job_id=str(normalized.get("id") or "").strip() or None,
            name=str(normalized.get("name") or "").strip(),
            cron_expr=cron_expr,
            timezone=timezone,
            description=str(normalized.get("description") or ""),
            targets=targets_str,
            enabled=bool(normalized.get("enabled", True)),
            wake_offset_seconds=normalized.get("wake_offset_seconds"),
            delete_after_run=delete_after_run,
            **session_kw,
        )
        push_payload = job.to_dict()
        push_payload.update(identity)
        try:
            await self._send("create", push_payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CronTools] sync create to gateway failed: %s", exc)

        # Reload scheduler to pick up the new job
        await self._reload_scheduler()

        return job.to_dict()

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> Any:
        from jiuwenclaw.gateway.cron.enterprise_gate import strip_sticky_identity_fields

        normalized_patch = strip_sticky_identity_fields(dict(patch or {}))
        normalized_patch.pop("session_id", None)
        if "targets" in normalized_patch:
            normalized_patch["targets"] = self._normalize_targets_param(normalized_patch.get("targets"))
            normalized_patch["targets"] = self._upgrade_bare_feishu_to_route_app_id(
                str(normalized_patch.get("targets") or "")
            )
            t = str(normalized_patch.get("targets") or "").strip()
            routing_sid = self._routing_session_id_for_targets(t)
            if routing_sid:
                normalized_patch["session_id"] = routing_sid
        chat_type = self._route().chat_type
        normalized_patch["chat_type"] = chat_type if chat_type else None
        identity = self._routing_identity_payload()

        if self._enterprise_ready():
            try:
                await self._send(
                    "update",
                    {"job_id": job_id, "patch": normalized_patch, **identity},
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"enterprise cron update push failed: {exc}") from exc
            # 企业不以本地写为成功；返回 patch 后的只读视图（尽力）
            current = await self.get_job(job_id)
            if isinstance(current, dict):
                merged = dict(current)
                merged.update(normalized_patch)
                return merged
            return {"id": job_id, **normalized_patch, **identity}

        job = await self._local_store.update_job(job_id, normalized_patch)
        try:
            await self._send("update", {"job_id": job_id, "patch": normalized_patch, **identity})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CronTools] sync update to gateway failed: %s", exc)

        # Reload scheduler to pick up the changes
        await self._reload_scheduler()

        return job.to_dict()

    async def delete_job(self, job_id: str) -> Any:
        identity = self._routing_identity_payload()
        if self._enterprise_ready():
            try:
                await self._send("delete", {"job_id": job_id, **identity})
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"enterprise cron delete push failed: {exc}") from exc
            return True

        gateway_synced = False
        try:
            await self._send("delete", {"job_id": job_id, **identity})
            gateway_synced = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CronTools] sync delete to gateway failed: %s", exc)

        deleted_local = await self._local_store.delete_job(job_id)
        deleted_shared = False
        shared = await self._shared_gateway_store()
        if shared is not None:
            try:
                deleted_shared = await shared.delete_job(job_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[CronTools] delete from shared store failed: %s", exc)

        # Reload scheduler to pick up the changes
        await self._reload_scheduler()

        return gateway_synced or deleted_local or deleted_shared

    async def toggle_job(self, job_id: str, enabled: bool) -> Any:
        identity = self._routing_identity_payload()
        if self._enterprise_ready():
            try:
                await self._send(
                    "toggle",
                    {"job_id": job_id, "enabled": bool(enabled), **identity},
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"enterprise cron toggle push failed: {exc}") from exc
            current = await self.get_job(job_id)
            if isinstance(current, dict):
                current = dict(current)
                current["enabled"] = bool(enabled)
                return current
            return {"id": job_id, "enabled": bool(enabled), **identity}

        job = await self._local_store.update_job(job_id, {"enabled": bool(enabled)})
        try:
            await self._send(
                "toggle",
                {"job_id": job_id, "enabled": bool(enabled), **identity},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CronTools] sync toggle to gateway failed: %s", exc)

        # Reload scheduler to pick up the changes
        await self._reload_scheduler()

        return job.to_dict()

    async def preview_job(self, job_id: str, count: int = 5) -> Any:
        if self._enterprise_ready():
            job_dict = await self.get_job(job_id)
            if job_dict is None:
                raise KeyError("job not found")
            cron_expr = str(job_dict.get("cron_expr") or "").strip()
            timezone = str(job_dict.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
            wake_offset = int(job_dict.get("wake_offset_seconds") or 0)
        else:
            job = await self._local_store.get_job(job_id)
            if job is None:
                raise KeyError("job not found")
            cron_expr = job.cron_expr
            timezone = job.timezone
            wake_offset = int(job.wake_offset_seconds or 0)
        count = max(1, min(int(count), 50))
        tz = ZoneInfo(timezone)
        base = datetime.now(tz=tz)
        out: list[dict[str, Any]] = []
        push_dt = base
        for _ in range(count):
            try:
                push_dt = _cron_next_push_dt(cron_expr, push_dt)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "CroniterBadDateError" in msg or "failed to find next date" in msg:
                    break
                raise
            wake_dt = push_dt - timedelta(seconds=max(0, wake_offset))
            out.append({"wake_at": wake_dt.isoformat(), "push_at": push_dt.isoformat()})
        return out

    async def run_now(self, job_id: str) -> Any:
        identity = self._routing_identity_payload()
        return await self._send("run_now", {"job_id": job_id, **identity})

    async def _create_job_tool(self, **kwargs: Any) -> Any:
        params: dict[str, Any] = {
            "name": kwargs.get("name"),
            "cron_expr": kwargs.get("cron_expr"),
            "timezone": kwargs.get("timezone"),
            "targets": kwargs.get("targets", ""),
            "enabled": kwargs.get("enabled", True),
            "description": kwargs.get("description"),
        }
        wake_offset_seconds = kwargs.get("wake_offset_seconds")
        if wake_offset_seconds is not None:
            params["wake_offset_seconds"] = wake_offset_seconds
        return await self.create_job(params)

    async def _update_job_tool(self, job_id: str, patch: dict[str, Any]) -> Any:
        return await self.update_job(job_id, patch)

    async def _preview_job_tool(self, job_id: str, count: int = 5) -> Any:
        return await self.preview_job(job_id, count)

    def get_tools(self) -> list[Tool]:
        def make_tool(name: str, description: str, input_params: dict, func) -> Tool:
            card = ToolCard(
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="cron_list_jobs",
                description="List all cron jobs.",
                input_params={"type": "object", "properties": {}},
                func=self.list_jobs,
            ),
            make_tool(
                name="cron_get_job",
                description="Get a cron job by id.",
                input_params={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                func=self.get_job,
            ),
            make_tool(
                name="cron_create_job",
                description="Create cron job.",
                input_params={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "cron_expr": {"type": "string"},
                        "timezone": {"type": "string"},
                        "description": {"type": "string"},
                        "targets": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "wake_offset_seconds": {"type": "integer"},
                    },
                    "required": ["name", "cron_expr", "timezone", "description"],
                },
                func=self._create_job_tool,
            ),
            make_tool(
                name="cron_update_job",
                description="Update cron job.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "patch": {"type": "object"},
                    },
                    "required": ["job_id", "patch"],
                },
                func=self._update_job_tool,
            ),
            make_tool(
                name="cron_delete_job",
                description="Delete cron job by id.",
                input_params={"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
                func=self.delete_job,
            ),
            make_tool(
                name="cron_toggle_job",
                description="Enable or disable cron job.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["job_id", "enabled"],
                },
                func=self.toggle_job,
            ),
            make_tool(
                name="cron_preview_job",
                description="Preview next runs.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["job_id"],
                },
                func=self._preview_job_tool,
            ),
            make_tool(
                name="cron_run_now",
                description="Trigger run now.",
                input_params={"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
                func=self.run_now,
            ),
        ]
