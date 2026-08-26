# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentWebSocketServer - Gateway 与 AgentServer 之间的 WebSocket 服务端."""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib
import json
import logging
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, ClassVar, Optional
from weakref import WeakValueDictionary

from openjiuwen.core.common.logging import server_logger
from websockets.exceptions import ConnectionClosed as WebSocketConnectionClosed

from jiuwenswarm.agents.harness.common.auto_harness import AutoHarnessService, reset_harness_packages_state
from jiuwenswarm.server.gateway_push.wire import build_server_push_wire
from jiuwenswarm.server.ws_send import send_wire_payload
from jiuwenswarm.agents.harness.common.tools.acp_output_tools import get_acp_output_manager
from jiuwenswarm.common.utils import (
    get_agent_sessions_dir,
    get_config_file,
    mask_sensitive,
    resolve_tenant_sessions_dir,
)
from jiuwenswarm.common.e2a.agent_compat import e2a_to_agent_request
from jiuwenswarm.common.e2a.constants import (
    E2A_CANCEL_SOURCE_CLIENT_DISCONNECT,
    E2A_INTERNAL_CANCEL_SOURCE_KEY,
    E2A_WIRE_INTERNAL_METADATA_KEYS,
)
from jiuwenswarm.common.e2a.gateway_normalize import (
    E2A_FALLBACK_FAILED_KEY,
    E2A_INTERNAL_CONTEXT_KEY,
    E2A_LEGACY_AGENT_REQUEST_KEY,
)
from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
    encode_json_parse_error_wire,
)
from jiuwenswarm.common.model_config_validation import is_placeholder_api_base
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenswarm.common.version import __version__
from jiuwenswarm.common.ws_diagnostics import (
    describe_ws_exception,
    describe_ws_peer,
    format_ws_diagnostics,
)
from jiuwenswarm.common.ws_limits import AGENT_WS_MAX_MESSAGE_BYTES
from jiuwenswarm.extensions.hook_event import AgentServerHookEvents
from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    is_interrupt_resume_payload,
)
from jiuwenswarm.agents.harness.common.rails.permissions.permissions_persist import persist_cli_trusted_directory
from jiuwenswarm.extensions.hooks_context import AgentServerChatHookContext, AgentWsServerStartHookContext
from jiuwenswarm.server.runtime.agent_manager import AgentManager, ACP_DEFAULT_CAPABILITIES
from jiuwenswarm.server.runtime.tenant_agent_pool import TenantAgentPool
from jiuwenswarm.server.runtime.session.session_metadata import get_all_sessions_metadata
from jiuwenswarm.server.runtime.session.session_history import (
    append_compact_history_records,
    enrich_history_messages_session_id,
    history_exists,
    load_history_records,
)
from jiuwenswarm.server.runtime.agent_adapter.sysop_builder import (
    build_filesystem_policy,
    build_yuanrong_sandbox_status_view,
    effective_files_from_policy,
    find_auto_managed_match,
    find_nested_files_conflict,
    list_effective_sandbox_files,
    validate_sandbox_files_runtime,
)
from jiuwenswarm.server.utils.utils import is_team_params
from jiuwenswarm.server.context import AgentServerServices, RequestContext
from jiuwenswarm.server.dispatch import dispatch_to_handler
from jiuwenswarm.server.transports.push_registry import (
    WS_PUSH_SUBSCRIBER_ID,
    get_push_registry,
)
from jiuwenswarm.server.transports.sink import WSSink
from jiuwenswarm.server.pipeline import dispatch_parsed_request
from jiuwenswarm.server.wire_parse import parse_inbound
from jiuwenswarm.server.handlers.ops import (  # noqa: F401  — re-exported for tests
    _reset_active_browser_runtimes_if_available,
)

# 默认路径实现在 handlers/_default.py。本模块留守的连接管理与解析段仍要用其中几个判定函数。
from jiuwenswarm.server.handlers._default import (  # noqa: F401  — re-exported for tests
    _handle_stream,
    _handle_unary,
    _is_explicit_plan_entry_request,
    _is_readonly_goal_get_request,
    _is_stateless_method_request,
    _prepare_tenant_code_mode_chat_turn,
    _session_mode_sync_lock,
    _should_sync_code_mode_state,
    _uses_tenant_pool,
)
# 进程级共享状态与「chat 轮次」方法集合定义在 _shared.py，两边 import 的是同一批对象。
from jiuwenswarm.server.handlers._shared import (  # noqa: F401  — re-exported for tests
    _CODE_MODE_SYNC_METHODS,
    _inject_plan_mode_activation_reminder,
    _session_mode_sync_locks,
    _sync_chat_request_metadata,
)

from jiuwenswarm.server.handlers._shared import (  # noqa: F401  — re-exported for tests
    _apply_resolved_mode_to_request,
    _is_team_metadata_mode,
    _background_session_kvc_tasks,
    _log_background_session_kvc_failure,
    _plan_exited_sessions,
    _sessions_dir_for_request,
    resolve_agent_request_mode,
    resolve_request_project_dir,
)
from jiuwenswarm.common.config import (
    DEFAULT_SANDBOX_POLICY_FILE,
    DEFAULT_SANDBOX_STARTUP_MODE,
    get_config,
    get_default_models,
    get_mcp_server_config,
    get_mcp_servers,
    get_sandbox_endpoint,
    get_sandbox_runtime,
    get_sandbox_startup_mode,
    get_sandbox_startup_mode_explicit,
    remove_mcp_server_in_config,
    resolve_preserve_file_sharing_mode_default,
    resolve_sandbox_policy_path,
    set_mcp_server_enabled_in_config,
    update_sandbox_endpoint,
    update_sandbox_runtime,
    upsert_mcp_server_in_config,
)
from jiuwenswarm.server.sandbox.jiuwenbox_runner import JiuwenBoxRunner
from jiuwenswarm.common.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_origin_check_enabled,
    is_allowed_browser_origin,
)

logger = logging.getLogger(__name__)

_INTERFACE_DEEP_MODULE = "jiuwenswarm.server.runtime.agent_adapter.interface_deep"
_skill_index_warmup_task: asyncio.Task[None] | None = None
_skill_index_warmup_roots_cache: list[str] = []
_skill_index_warmup_enabled_cache: str | None = None


def _import_interface_deep_blocking() -> None:
    if _INTERFACE_DEEP_MODULE not in sys.modules:
        importlib.import_module(_INTERFACE_DEEP_MODULE)


async def _warm_interface_deep_module() -> None:
    """Import interface_deep in a worker thread so listen is not blocked."""
    if _INTERFACE_DEEP_MODULE in sys.modules:
        logger.info("[AgentWebSocketServer] interface_deep_warmup cache=hit elapsed_ms=0.0")
        return
    t0 = time.perf_counter()
    logger.info("[AgentWebSocketServer] interface_deep_warmup cache=miss starting background import")
    await asyncio.to_thread(_import_interface_deep_blocking)
    logger.info(
        "[AgentWebSocketServer] interface_deep_warmup cache=miss ok elapsed_ms=%.1f",
        (time.perf_counter() - t0) * 1000,
    )


async def _startup_warmup_chain() -> None:
    """Background after WS listen: interface_deep import then checkpointer warmup."""
    try:
        await _warm_interface_deep_module()
    except Exception:
        logger.warning(
            "[AgentWebSocketServer] interface_deep_warmup failed",
            exc_info=True,
        )
        return
    try:
        from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
            ensure_persistent_checkpointer,
        )

        await ensure_persistent_checkpointer()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[AgentWebSocketServer] checkpointer 预热失败 (首请求将兜底重试): %s", exc
        )


def _parse_shared_skills_dirs_from_env_dict(env: dict) -> list[str]:
    """Extract shared skill roots from a sync/reload env object."""
    from jiuwenswarm.common.local_env_config import canonical_product_env_key
    from jiuwenswarm.common.utils import (
        JIUWENSWARM_SHARED_SKILLS_DIRS_ENV,
        parse_shared_skills_dirs_raw,
    )

    for key, value in env.items():
        if canonical_product_env_key(str(key)) != JIUWENSWARM_SHARED_SKILLS_DIRS_ENV:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        return [str(path) for path in parse_shared_skills_dirs_raw(text)]
    return []


def _parse_enabled_skills_from_env_dict(env: dict) -> str | None:
    from jiuwenswarm.common.local_env_config import canonical_product_env_key
    from jiuwenswarm.server.runtime.skill.skill_manager import ENABLED_SKILLS_ENV

    for key, value in env.items():
        if canonical_product_env_key(str(key)) != ENABLED_SKILLS_ENV:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _shared_skills_dirs_from_sync_params(params: dict) -> list[str]:
    agents = params.get("agents")
    if not isinstance(agents, list):
        return []
    seen: set[str] = set()
    roots: list[str] = []
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        env = entry.get("env")
        if not isinstance(env, dict):
            continue
        for path in _parse_shared_skills_dirs_from_env_dict(env):
            if path not in seen:
                seen.add(path)
                roots.append(path)
    return roots


def _split_enabled_skills_csv(raw: str) -> list[str]:
    return [
        item.strip()
        for item in str(raw).replace(";", ",").split(",")
        if item.strip()
    ]


def _merge_enabled_skills_from_agents(agents: list) -> str | None:
    """Union allowlists from all sync agents (stable order)."""
    seen: set[str] = set()
    merged: list[str] = []
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        env = entry.get("env")
        if not isinstance(env, dict):
            continue
        enabled = _parse_enabled_skills_from_env_dict(env)
        if enabled is None:
            continue
        for name in _split_enabled_skills_csv(enabled):
            if name in seen:
                continue
            seen.add(name)
            merged.append(name)
    return ",".join(merged) if merged else None


def _enabled_skills_from_sync_params(params: dict) -> str | None:
    """Resolve ENABLED_SKILLS for process-index warmup.

    OfficeAce stamps ``JIUWENSWARM_SHARED_SKILLS_DIRS`` onto every catalog
    agent, each with its own short allowlist. Process warmup for the default
    chat path only needs the general assistant (``agent_id=office``).
    """
    agents = params.get("agents")
    if not isinstance(agents, list):
        return None
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("agent_id", "")).strip().lower() != "office":
            continue
        env = entry.get("env")
        if not isinstance(env, dict):
            continue
        return _parse_enabled_skills_from_env_dict(env)
    return None


def _enabled_skills_from_tip_namespaces() -> str | None:
    """Read ENABLED_SKILLS from synced agent tip bags (prefer default/office)."""
    from jiuwenswarm.common.local_env_config import effective_tip

    candidates: list[tuple[str | None, str | None]] = [
        ("default", "office"),
        ("default", "default"),
        (None, None),
    ]
    try:
        from jiuwenswarm.server.runtime.tenant_catalog_registry import (
            TenantCatalogRegistry,
        )

        registry = TenantCatalogRegistry.get_instance()
        for service_id, agent_id in registry.list_pairs():
            candidates.append((service_id, agent_id))
    except Exception as exc:  # noqa: BLE001 - tip 枚举失败时退回固定候选
        logger.debug(
            "[AgentWebSocketServer] tip catalog list_pairs failed; "
            "fall back to default tip candidates: %s",
            exc,
            exc_info=True,
        )

    for service_id, agent_id in candidates:
        tip = None
        try:
            tip = effective_tip(service_id, agent_id)
        except Exception as tip_exc:  # noqa: BLE001 - 单个 tip 失败则跳过
            logger.debug(
                "[AgentWebSocketServer] effective_tip failed for "
                "service_id=%r agent_id=%r: %s",
                service_id,
                agent_id,
                tip_exc,
                exc_info=True,
            )
        if not isinstance(tip, dict):
            continue
        enabled = _parse_enabled_skills_from_env_dict(tip)
        if enabled is not None:
            return enabled
    return None


def _shared_skills_dirs_from_tip_namespaces() -> list[str]:
    """Read SHARED_SKILLS_DIRS from synced agent tip bags (e.g. default/office)."""
    from jiuwenswarm.common.local_env_config import effective_tip

    seen: set[str] = set()
    roots: list[str] = []
    candidates: list[tuple[str | None, str | None]] = [
        ("default", "office"),
        ("default", "default"),
        (None, None),
    ]
    try:
        from jiuwenswarm.server.runtime.tenant_catalog_registry import (
            TenantCatalogRegistry,
        )

        registry = TenantCatalogRegistry.get_instance()
        for service_id, agent_id in registry.list_pairs():
            candidates.append((service_id, agent_id))
    except Exception as exc:  # noqa: BLE001 - tip 枚举失败时退回固定候选
        logger.debug(
            "[AgentWebSocketServer] tip catalog list_pairs failed; "
            "fall back to default tip candidates: %s",
            exc,
            exc_info=True,
        )

    for service_id, agent_id in candidates:
        tip = None
        try:
            tip = effective_tip(service_id, agent_id)
        except Exception as tip_exc:  # noqa: BLE001 - 单个 tip 失败则跳过
            logger.debug(
                "[AgentWebSocketServer] effective_tip failed for "
                "service_id=%r agent_id=%r: %s",
                service_id,
                agent_id,
                tip_exc,
                exc_info=True,
            )
        if not isinstance(tip, dict):
            continue
        paths = _parse_shared_skills_dirs_from_env_dict(tip)
        for path in paths:
            if path not in seen:
                seen.add(path)
                roots.append(path)
    return roots


def _skill_index_warmup_roots() -> list[str]:
    """Resolve skill roots for process-index warmup (align with SkillUseRail)."""
    if _skill_index_warmup_roots_cache:
        return list(_skill_index_warmup_roots_cache)
    try:
        from jiuwenswarm.common.utils import (
            get_agent_skills_dir,
            get_shared_agent_skills_dirs,
            resolve_agent_registered_skill_dirs,
        )

        seen: set[str] = set()
        roots: list[str] = []
        for source in (
            [str(p) for p in get_shared_agent_skills_dirs()],
            [str(p) for p in _shared_skills_dirs_from_tip_namespaces()],
            [str(p) for p in resolve_agent_registered_skill_dirs()],
        ):
            for path in source:
                if path in seen:
                    continue
                seen.add(path)
                roots.append(path)
        if roots:
            return roots
        return [str(get_agent_skills_dir())]
    except Exception:
        logger.debug(
            "[AgentWebSocketServer] skill_index_warmup root resolve failed",
            exc_info=True,
        )
        return []


def _skill_index_warmup_enabled_skills() -> str | None:
    """Optional ENABLED_SKILLS allowlist from tip/env (same as SkillUseRail)."""
    if _skill_index_warmup_enabled_cache is not None:
        return _skill_index_warmup_enabled_cache
    try:
        tip_enabled = _enabled_skills_from_tip_namespaces()
        if tip_enabled is not None:
            return tip_enabled
        from jiuwenswarm.server.runtime.skill.skill_manager import (
            enabled_skills_from_environ,
        )

        return enabled_skills_from_environ()
    except Exception:
        logger.debug(
            "[AgentWebSocketServer] skill_index_warmup enabled_skills resolve failed",
            exc_info=True,
        )
        return None


async def _warm_skill_md_index() -> None:
    """Background: frontmatter-only process skill index after WS listen."""
    t0 = time.perf_counter()
    roots = _skill_index_warmup_roots()
    if not roots:
        logger.info(
            "[AgentWebSocketServer] skill_index_warmup skipped: no skill roots "
            "elapsed_ms=%.1f",
            (time.perf_counter() - t0) * 1000,
        )
        return
    enabled = _skill_index_warmup_enabled_skills()
    try:
        from openjiuwen.harness.rails.skills import warmup_process_skill_index

        stats = await asyncio.to_thread(
            warmup_process_skill_index,
            roots,
            enabled_skills=enabled,
        )
    except Exception:
        logger.warning(
            "[AgentWebSocketServer] skill_index_warmup failed roots=%s elapsed_ms=%.1f",
            roots,
            (time.perf_counter() - t0) * 1000,
            exc_info=True,
        )
        return
    logger.info(
        "[AgentWebSocketServer] skill_index_warmup ok roots=%s filled=%s hits=%s "
        "entries=%s cost_ms=%s wall_ms=%.1f",
        roots,
        stats.get("filled"),
        stats.get("hits"),
        stats.get("entries"),
        stats.get("cost_ms"),
        (time.perf_counter() - t0) * 1000,
    )


def _start_skill_index_warmup(*, force: bool = False) -> None:
    global _skill_index_warmup_task
    if _skill_index_warmup_task is not None and not _skill_index_warmup_task.done():
        if not force:
            return
        _skill_index_warmup_task.cancel()
    _skill_index_warmup_task = asyncio.get_running_loop().create_task(
        _warm_skill_md_index()
    )


def schedule_skill_index_warmup_after_sync(
    *,
    sync_params: dict | None = None,
    force: bool = False,
) -> None:
    """Re-warm skill index once sync_agents_configs has injected shared skill dirs."""
    global _skill_index_warmup_roots_cache, _skill_index_warmup_enabled_cache

    roots: list[str] = []
    if isinstance(sync_params, dict):
        roots = _shared_skills_dirs_from_sync_params(sync_params)
        enabled = _enabled_skills_from_sync_params(sync_params)
        if enabled is not None:
            _skill_index_warmup_enabled_cache = enabled
    if not roots:
        roots = _shared_skills_dirs_from_tip_namespaces()
    if not roots:
        try:
            from jiuwenswarm.common.utils import get_shared_agent_skills_dirs

            roots = [str(path) for path in get_shared_agent_skills_dirs()]
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[AgentWebSocketServer] get_shared_agent_skills_dirs failed: %s",
                exc,
                exc_info=True,
            )
            roots = []
    if not roots:
        logger.info(
            "[AgentWebSocketServer] schedule_skill_index_warmup_after_sync skipped: "
            "no shared skill roots"
        )
        return
    _skill_index_warmup_roots_cache = roots
    _start_skill_index_warmup(force=force)


from jiuwenswarm.server.wire_truncate import (  # noqa: F401  — re-exported for tests / handlers
    _HISTORY_PAGE_SIZE,
    _HISTORY_WIRE_STRING_LIMIT,
    _HISTORY_WIRE_METADATA_STRING_LIMIT,
    _HISTORY_WIRE_LIST_LIMIT,
    _HISTORY_WIRE_DEPTH_LIMIT,
    _HISTORY_WIRE_RECORD_MAX_BYTES,
    _TEAM_HISTORY_DEFAULT_LIMIT,
    _TEAM_HISTORY_MAX_LIMIT,
    _TEAM_HISTORY_DEFAULT_MAX_BYTES,
    _TEAM_HISTORY_MIN_MAX_BYTES,
    _TEAM_HISTORY_MAX_MAX_BYTES,
    _TEAM_HISTORY_FRAME_OVERHEAD_BYTES,
    _WORKFLOW_SNAPSHOT_MAX_BYTES,
    _WORKFLOW_SNAPSHOT_FRAME_OVERHEAD_BYTES,
    _WORKFLOW_SNAPSHOT_MAX_WORKFLOWS,
    _WORKFLOW_LIST_SUMMARY_STRING_LIMIT,
    _WORKFLOW_COLLAPSED_AGENT_TEXT_LIMIT,
    _WORKFLOW_WAITING_HUMAN_PROMPT_MAX_BYTES,
    _HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES,
    _json_wire_size,
    _coerce_int,
    _truncate_string_by_bytes,
    _compact_wire_metadata_value,
    _sanitize_history_wire_value,
    _collapse_oversized_history_record,
    _minimal_history_record_for_wire,
    _sanitize_history_record_for_wire,
    _select_history_record_page,
    _is_waiting_human_agent,
    _extract_waiting_human_prompts,
    _restore_waiting_human_prompts,
    _workflow_agent_for_collapse,
    _collapse_oversized_workflow_snapshot_item,
    _minimal_workflow_snapshot_item_for_wire,
    _minimal_workflow_detail_preserving_waiting_human,
    _sanitize_workflow_snapshot_item_for_wire,
    _fit_workflow_detail_to_budget,
    _workflow_list_summary_phase,
    _workflow_list_summary_item,
    _minimal_workflow_list_item,
    _fit_workflow_list_item_for_budget,
    _build_workflow_list_payload,
    _build_workflow_detail_payload,
    _find_workflow_agent,
    _build_workflow_human_prompt_payload,
    _build_workflow_snapshot_payload,
)


class _GatewayWSPushSink:
    """Gateway WS 连接在 :class:`PushRegistry` 里的推送出口。

    为什么不直接用 :class:`WSSink`
    -----------------------------
    ``PushRegistry.push`` 对**抛异常**的订阅者会当场注销（一条坏掉的 SSE 连接
    不该拖垮其他订阅者）。但 WS 侧要求的语义是**发送失败只记 warning，连接照旧
    留着** —— 直接塞裸 ``WSSink`` 会让一次瞬时发送失败就把 Gateway 踢出推送名单，
    直到重连才恢复。

    本类把异常吃在内部、返回 ``False``，注册表因此永远不会注销它。
    """

    __slots__ = ("_inner",)

    def __init__(self, ws: Any, send_lock: asyncio.Lock) -> None:
        self._inner = WSSink(ws, send_lock)

    async def send_wire(self, wire: dict[str, Any]) -> bool:
        try:
            return await self._inner.send_wire(wire)
        # CancelledError 继承 BaseException，不会被下面的 ``except Exception`` 接住，
        # 无需显式放行（同 push_registry.PushRegistry.push）。
        except Exception as e:  # noqa: BLE001 - 失败只 warning，不注销自己（见类 docstring）
            logger.warning("[AgentWebSocketServer] send_push 失败: %s", e)
            return False

    async def send_unary(self, resp: Any, *, response_id: str | None = None) -> bool:
        return await self._inner.send_unary(resp, response_id=response_id)

    async def send_chunk(self, chunk: Any, *, sequence: int, response_id: str | None = None) -> bool:
        return await self._inner.send_chunk(chunk, sequence=sequence, response_id=response_id)

    async def send_error(
        self, request_id: str, message: str, *, code: str = "INTERNAL_ERROR", channel_id: str = ""
    ) -> bool:
        return await self._inner.send_error(request_id, message, code=code, channel_id=channel_id)


class AgentWebSocketServer:
    """Gateway 与 AgentServer 之间的 WebSocket 服务端（单例）.

    监听来自 Gateway (WebSocketAgentServerClient) 的连接，按协议约定处理请求：
    - 收到 JSON：E2AEnvelope（或过渡期 legacy + 兜底信封）
    - is_stream=False：``process_message`` → 一条 **E2AResponse** JSON（``jiuwenswarm.e2a.wire_codec``）
    - is_stream=True：逐条 **E2AResponse** JSON（chunk/complete/error）
    - 例外：首帧 ``connection.ack`` 仍为 ``type/event`` 事件帧

    支持 send_push：推送帧亦为 E2AResponse 线格式（由 chunk 编码）。
    """

    _instance: ClassVar[AgentWebSocketServer | None] = None

    def __init__(
            self,
            host: str = "127.0.0.1",
            port: int = 18000,
            *,
            ping_interval: float | None = 30.0,
            ping_timeout: float | None = 300.0,
    ) -> None:
        self._host = host
        self._port = port
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server: Any = None
        # send_push的推送订阅者统一由PushRegistry持有，本类不持有当前连接；
        # WS侧以固定id：`WS_PUSH_SUBSCRIBER_ID`注册。
        # key是``_ws_capabilities_key``返回的str(id(ws))，与RequestContext.connection_id
        self._acp_client_capabilities_by_ws: dict[str, dict[str, Any]] = {}
        # AgentManager 实例（企业多租户入口见 TenantAgentPool，按 AGENT_RUNTIME 使用）
        self._agent_manager = AgentManager()
        # skills.* 等无状态 RPC：AgentManager 未缓存 agent 时复用的轻量 JiuWenSwarm，
        # 避免每次 cache miss 都 new 导致 SkillNet 异步安装等实例态断裂。
        self._stateless_fallback_agents: dict[str, Any] = {}
        # session_id → all live stream tasks. This is host lifecycle tracking
        # for interrupt/connection cleanup only; it never decides interaction
        # output ownership.
        self._session_stream_tasks: dict[str, dict[asyncio.Task, asyncio.Event]] = {}
        # Scheduler service instance (for scheduled auto_harness tasks)
        self._scheduler_service: Optional[AutoHarnessService] = None
        self._scheduler_agent: Any = None
        # Model cache for scheduled task execution (same approach as interface_deep)
        self._model_cache: dict[str, Any] = {}
        self._default_model: Optional[Any] = None
        # 本地 jiuwenbox 子进程管理器 (lazy 启动, 在 /sandbox enable 时 ensure_running)
        self._jiuwenbox_runner = JiuwenBoxRunner.instance()
        # checkpointer 后台预热任务 (start() 里 fire-and-forget, stop() 时 cancel)
        self._checkpointer_warmup_task: Optional[asyncio.Task] = None
        # Proactive recommendation engine (set by app_agentserver for debug trigger)
        self._proactive_engine: Any = None
        get_acp_output_manager().set_send_push_callback(
            lambda msg: asyncio.create_task(self.send_push(msg))
        )

    def set_proactive_engine(self, engine: Any) -> None:
        """Store the proactive engine instance for debug trigger interface."""
        self._proactive_engine = engine

    @staticmethod
    def _ws_capabilities_key(ws: Any) -> str:
        """连接标识。
        返回``str(id(ws))``而非``id(ws)``，与``RequestContext.connection_id``
        对齐 —— 业务层用``ctx.connection_id``写入、传输层用``ws``读取.
        """
        return str(id(ws))

    def _set_acp_client_capabilities(
        self, connection_id: str, capabilities: dict[str, Any] | None
    ) -> None:
        if isinstance(capabilities, dict):
            self._acp_client_capabilities_by_ws[connection_id] = dict(capabilities)
        else:
            self._acp_client_capabilities_by_ws.pop(connection_id, None)

    def _get_acp_client_capabilities(self, connection_id: str) -> dict[str, Any]:
        caps = self._acp_client_capabilities_by_ws.get(connection_id)
        return dict(caps) if isinstance(caps, dict) else {}

    def _set_ws_acp_client_capabilities(self, ws: Any, capabilities: dict[str, Any] | None) -> None:
        self._set_acp_client_capabilities(self._ws_capabilities_key(ws), capabilities)

    def _get_ws_acp_client_capabilities(self, ws: Any) -> dict[str, Any]:
        return self._get_acp_client_capabilities(self._ws_capabilities_key(ws))

    def _clear_ws_acp_client_capabilities(self, ws: Any) -> None:
        self._acp_client_capabilities_by_ws.pop(self._ws_capabilities_key(ws), None)

    @classmethod
    def get_instance(
            cls,
            *,
            host: str = "127.0.0.1",
            port: int = 18000,
            ping_interval: float | None = 30.0,
            ping_timeout: float | None = 300.0,
    ) -> "AgentWebSocketServer":
        """返回单例实例。

        首次调用时创建实例，后续调用返回已存在的实例。
        """
        if cls._instance is not None:
            return cls._instance
        cls._instance = cls(
            host=host,
            port=port,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）。"""
        cls._instance = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """启动 WebSocket 服务端，开始监听连接。优先使用 legacy.server.serve 以与 Gateway 的 legacy client 握手兼容.

        注: persistent checkpointer 的初始化历史在 ``legacy_serve`` 之前同步 await,
        首次约耗时 ~14s (sqlite 文件 + openjiuwen 工厂反射), 期间 WS 端口未 listen,
        是 Gateway connect 重试 (头两次必失败, 白等 ~6s) 的元凶。现改为 ``legacy_serve``
        之后后台预热 (fire-and-forget), 让端口尽快开放; 首条 chat 请求若赶在预热完成前
        到达, 走 ``_ensure_persistent_checkpointer_response`` 兜底等待, 不影响握手.
        """
        await self._trigger_before_ws_server_start_hook()
        if self._server is not None:
            logger.warning("[AgentWebSocketServer] 服务端已在运行")
            return

        # Reset harness package state to native on service startup
        reset_harness_packages_state()

        try:
            from websockets.legacy.server import serve as legacy_serve
            self._server = await legacy_serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
                max_size=AGENT_WS_MAX_MESSAGE_BYTES,
            )
        except ImportError:
            import websockets
            self._server = await websockets.serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
                max_size=AGENT_WS_MAX_MESSAGE_BYTES,
            )
        logger.info(
            "[AgentWebSocketServer] 已启动: ws://%s:%s", self._host, self._port
        )
        _start_skill_index_warmup()
        # 端口已 listen: interface_deep 在 thread pool import，再预热 checkpointer.
        # _checkpointer_warmup_task 供 shutdown 时 cancel, 避免任务悬挂.
        self._checkpointer_warmup_task = asyncio.create_task(
            _startup_warmup_chain(), name="startup-warmup"
        )
        # WS 监听已经开放, 现在按 config.yaml::sandbox 的 runtime.enabled +
        # startup_mode 决定要不要自动把 jiuwenbox 子进程也拉起来。失败不阻塞
        # 启动 (用户依然可以在 TUI 里跑 /sandbox enable 重试)。
        await self._bootstrap_internal_jiuwenbox()

    async def _bootstrap_internal_jiuwenbox(self) -> None:
        """启动时按 ``config.yaml::sandbox`` 自动拉起 jiuwenbox 子进程。

        触发条件: ``config.yaml::sandbox.startup_mode`` **显式**写为 ``internal``。
        这里刻意走 :func:`get_sandbox_startup_mode_explicit` 而不是
        :func:`get_sandbox_startup_mode` —— 后者在字段缺失时默认回落到
        ``internal``, 会让没在用沙箱的用户升级版本后突然多出 jiuwenbox 进程;
        boot 阶段必须严格区分 "用户写过 internal" 和 "走默认值"。

        不再单独依赖 ``sandbox.enabled``:
        - 老逻辑要 ``enabled=True`` AND ``startup_mode=internal`` 才拉, 但
          ``enabled`` 是 ``/sandbox`` 命令的产物, 用户手改 yaml 设了 ``internal``
          的话很容易漏配 ``enabled`` → boot 时一声不吭跳过, 体验差。
        - 现在: 只要 ``startup_mode=internal`` 就拉; 成功后顺手把
          ``sandbox.enabled`` 同步成 ``True``, ``/sandbox status`` 显示与实际
          运行的 jiuwenbox 一致。
        - ``/sandbox disable`` 仍然会停 jiuwenbox 并把 ``enabled`` 置 ``False``,
          但**重启后会被本方法重新拉起** (因为 ``startup_mode`` 没改)。要让
          disable 跨重启生效, 把 ``startup_mode`` 改为 ``external`` 或从 yaml
          里删掉该字段即可。

        与 :meth:`_handle_sandbox_enable` 的其余差别:
        - 不调用 ``agent_manager.recreate_agent``: 启动阶段还没有任何会话/agent
          实例, 没东西需要重建; 后续会话首次进入时按现有 ``sandbox.url`` 直接装载。
        - 严格 best-effort: 任何失败 (policy 缺失 / 端口/spawn 失败) 一律记
          warning, 绝不让 agent-server 自身启动失败 (否则运维误配 yaml 会让整
          产品起不来, 也无从修复)。
        """
        try:
            # 非 Linux 平台直接跳过 auto-start: jiuwenbox 依赖 bwrap / Landlock /
            # 命名空间, Windows / macOS 起不来; 即便 spawn 成功后续 /sandbox 命
            # 令也会被 :func:`_require_sandbox_supported` 拒掉, 留着只会浪费一
            # 次失败的子进程启动。
            if not sys.platform.startswith("linux"):
                logger.info(
                    "[AgentWebSocketServer] skipping jiuwenbox auto-start: "
                    "/sandbox is only supported on Linux (current platform: %r)",
                    sys.platform,
                )
                return
            explicit_mode = get_sandbox_startup_mode_explicit()
            if explicit_mode is None:
                logger.info(
                    "[AgentWebSocketServer] sandbox.startup_mode 未在 config.yaml "
                    "中显式配置, skipping jiuwenbox auto-start (走默认 host 模式; "
                    "如需 agent-server 自动拉起 jiuwenbox 子进程, 设置 "
                    "sandbox.startup_mode: internal)"
                )
                return
            if explicit_mode != "internal":
                logger.info(
                    "[AgentWebSocketServer] sandbox.startup_mode=%r, skipping "
                    "jiuwenbox auto-start (external 模式由用户自行拉起 "
                    "jiuwenbox-server)",
                    explicit_mode,
                )
                return

            # startup_mode=internal 已经定下来; 其余字段从归一后的 endpoint
            # 取, 缺啥用默认。
            endpoint = get_sandbox_endpoint()
            url = endpoint.get("url") or "http://127.0.0.1:8321"
            sandbox_type = endpoint.get("type") or "jiuwenbox"
            # yuanrong 不需要本机 jiuwenbox 进程; 仅通过 config 启用 SysOperation。
            if str(sandbox_type).strip().lower() == "yuanrong":
                logger.info(
                    "[AgentWebSocketServer] sandbox.type=yuanrong, skipping "
                    "jiuwenbox auto-start (YuanRong uses YR_* env + yr.init)"
                )
                return
            raw_policy = endpoint.get("policy_file") or ""
            effective_policy_file = raw_policy or DEFAULT_SANDBOX_POLICY_FILE
            policy_path = resolve_sandbox_policy_path(effective_policy_file)
            if policy_path is None or not policy_path.is_file():
                logger.warning(
                    "[AgentWebSocketServer] sandbox auto-start skipped: "
                    "policy_file=%r 无法解析到一个存在的文件 "
                    "(resolved=%s). 进 TUI 跑 /sandbox enable 重试或修复 "
                    "config.yaml::sandbox.policy_file。",
                    effective_policy_file,
                    policy_path,
                )
                return

            from jiuwenswarm.server.handlers.sandbox import (
                allocate_internal_jiuwenbox_port,
                parse_sandbox_host_port,
            )

            host, preferred_port = parse_sandbox_host_port(url)
            port = allocate_internal_jiuwenbox_port(
                AgentServerServices(self), host, preferred_port
            )
            if port != preferred_port:
                url = f"http://{host}:{port}"
                logger.info(
                    "[AgentWebSocketServer] jiuwenbox auto-start: "
                    "preferred port %d busy, using %d",
                    preferred_port,
                    port,
                )

            ok = await self._jiuwenbox_runner.ensure_running(
                host=host,
                port=port,
                startup_mode="internal",
                policy_path=policy_path,
            )
            if not ok:
                stderr_tail = self._jiuwenbox_runner.get_stderr_tail(10)
                logger.warning(
                    "[AgentWebSocketServer] jiuwenbox auto-start failed at "
                    "%s:%d (policy=%s); 进 TUI 跑 /sandbox enable 重试。"
                    " stderr tail:\n%s",
                    host,
                    port,
                    policy_path,
                    stderr_tail or "(empty)",
                )
                return

            # 端口可能在 _allocate_internal_jiuwenbox_port 里换过, 把最终生效
            # 的 url 落盘, 这样 (a) 后续会话/agent 重建直接读到正确端点,
            # (b) /sandbox status 显示也是真实值, 不再是 config 里旧的 8321。
            try:
                update_sandbox_endpoint(
                    url,
                    sandbox_type,
                    startup_mode="internal",
                    policy_file=effective_policy_file,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[AgentWebSocketServer] persist sandbox endpoint failed "
                    "after auto-start: %s",
                    exc,
                )

            # auto-start 成功 → ``runtime.enabled`` 同步为 True, 这样 /sandbox
            # status / TUI 显示的状态跟真实运行的 jiuwenbox 对齐。如果用户上次
            # /sandbox disable 留下了 False, 这里会被覆盖 —— 这是已知的、属于
            # 上面 docstring 提到的 "disable 不跨重启" 语义的一部分。
            try:
                update_sandbox_runtime({"enabled": True})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[AgentWebSocketServer] persist sandbox.enabled=True "
                    "failed after auto-start: %s",
                    exc,
                )

            logger.info(
                "[AgentWebSocketServer] jiuwenbox auto-started at %s "
                "(policy=%s)",
                url,
                policy_path,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[AgentWebSocketServer] jiuwenbox auto-start raised an "
                "unexpected error; skipping (用户可在 TUI 里 /sandbox enable 重试)"
            )

    async def _stop_scheduler(self) -> None:
        """Stop the auto_harness scheduler."""
        try:
            if self._scheduler_service is not None:
                await self._scheduler_service.stop_scheduler()
                logger.info("[AgentWebSocketServer] Scheduler stopped")
        except Exception as e:
            logger.warning("[AgentWebSocketServer] Failed to stop scheduler: %s", e)
        finally:
            self._scheduler_service = None
            scheduler_agent = getattr(self, "_scheduler_agent", None)
            if scheduler_agent is not None:
                unpin = getattr(self._agent_manager, "unpin_agent", None)
                if callable(unpin):
                    unpin(scheduler_agent)
            self._scheduler_agent = None

    async def _process_request(self, *args: Any) -> Any:
        """在握手阶段执行 Origin 校验，兼容 legacy/new websockets APIs。"""
        path, request_headers = extract_handshake_request(args)
        origin = get_header_value(request_headers, "Origin")

        enable_origin_check = is_origin_check_enabled()
        if not enable_origin_check:
            logger.info(
                "[AgentWebSocketServer] 握手检查 path=%s origin=%s enable_origin_check=%s allowed=%s",
                path,
                origin,
                enable_origin_check,
                True,
            )
            return None

        allowed = is_allowed_browser_origin(origin)
        logger.info(
            "[AgentWebSocketServer] 握手检查 path=%s origin=%s enable_origin_check=%s allowed=%s",
            path,
            origin,
            enable_origin_check,
            allowed,
        )
        if allowed:
            return None

        logger.warning(
            "[AgentWebSocketServer] 握手拒绝 path=%s origin=%s reason=origin_not_allowed",
            path,
            origin,
        )
        return forbidden_origin_response(args)

    async def stop(self) -> None:
        """停止 WebSocket 服务端."""
        global _skill_index_warmup_task

        async def _cancel_warmup_task(task: asyncio.Task[None] | None, label: str) -> None:
            if task is None or task.done():
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("[AgentWebSocketServer] %s cancel failed: %s", label, exc)

        # 先取消后台预热, 避免在 server 关闭后仍在后台跑.
        warmup = self._checkpointer_warmup_task
        self._checkpointer_warmup_task = None
        await _cancel_warmup_task(warmup, "startup warmup")
        skill_warmup = _skill_index_warmup_task
        _skill_index_warmup_task = None
        await _cancel_warmup_task(skill_warmup, "skill_index warmup")
        had_server = self._server is not None
        if had_server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        from jiuwenswarm.server.runtime.session.kv_cache_product_hooks import (
            cancel_pending_tasks,
        )

        await cancel_pending_tasks()

        if not had_server:
            return
        try:
            await self._jiuwenbox_runner.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AgentWebSocketServer] jiuwenbox_runner.stop failed: %s", exc)
        logger.info("[AgentWebSocketServer] 已停止")

    # ---------- 连接处理 ----------

    async def _connection_handler(self, ws: Any) -> None:
        """处理单个 Gateway WebSocket 连接，同一连接可并发处理多个请求."""
        # 和客户端连接成功后, 触发agentserver启动成功的回调事件
        await self._trigger_agent_server_started_hook()

        remote = ws.remote_address
        logger.info("[AgentWebSocketServer] 新连接: %s", remote)

        send_lock = asyncio.Lock()
        # WS连接注册为推送订阅者，同id重复注册即覆盖，
        # drop_on_stall=False：与 _GatewayWSPushSink「发送失败/变慢都不注销」的契约一致 ——
        # gateway-ws 是固定 id 的单槽位，被摘掉后长连接不会重新注册，只能重启服务恢复。
        get_push_registry().register(
            WS_PUSH_SUBSCRIBER_ID,
            _GatewayWSPushSink(ws, send_lock),
            drop_on_stall=False,
        )

        # 触发身份获取（写入当前连接 context；无 provider 时身份为 null，连接继续）。
        # 其后 asyncio.create_task(_handle_message) 自动 copy_context，身份传播到各请求任务。
        identity_token = None
        try:
            from jiuwenswarm.extensions.identity_provider import IdentityStore
            identity_token = await IdentityStore.fetch_and_store()
            identity = IdentityStore.get_identity()
            if identity is not None:
                logger.info(
                    "[AgentWebSocketServer] 身份信息已获取: user_id=%s domain_id=%s app_id=%s",
                    identity.user_id, identity.domain_id, identity.app_id,
                    extra={"user_visible": "progress"},
                )
            else:
                logger.debug("[AgentWebSocketServer] 未获取到身份信息（无 provider 或获取失败）",
                             extra={"user_visible": "progress"})
        except Exception as e:
            logger.warning("[AgentWebSocketServer] 身份获取异常: %s", e, extra={"user_visible": "progress"})

        # 发送 connection.ack 事件，通知 Gateway 服务端已就绪
        try:
            ack_frame = {
                "type": "event",
                "event": "connection.ack",
                "payload": {"status": "ready"},
            }
            await send_wire_payload(ws, ack_frame)
            logger.info("[AgentWebSocketServer] 已发送 connection.ack: %s", remote)
        except Exception as e:
            logger.warning("[AgentWebSocketServer] 发送 connection.ack 失败: %s", e)

        tasks: set[asyncio.Task] = set()

        try:
            async for raw in ws:
                async def _run(raw=raw):
                    try:
                        await self._handle_message(ws, raw, send_lock)
                    except WebSocketConnectionClosed as e:
                        logger.info("[AgentWebSocketServer] 连接已关闭: %s", e)
                    except Exception:
                        logger.exception("[AgentWebSocketServer] 处理消息失败")

                task = asyncio.create_task(_run())
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketConnectionClosed as e:
            logger.info(
                "[AgentWebSocketServer] 连接关闭: %s",
                format_ws_diagnostics(
                    {
                        "remote": remote,
                        "active_tasks": len(tasks),
                        "session_stream_tasks": len(self._session_stream_tasks),
                        "ping_interval": self._ping_interval,
                        "ping_timeout": self._ping_timeout,
                    },
                    describe_ws_peer(ws),
                    describe_ws_exception(e),
                ),
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] 连接处理异常 (%s): %s", remote, e)
        finally:
            if identity_token is not None:
                try:
                    from jiuwenswarm.extensions.identity_provider import IdentityStore
                    IdentityStore.clear(identity_token)
                except Exception:
                    logger.exception("[AgentWebSocketServer] identity clear failed")
            # **无条件**注销：即使已有新连接接管，也照旧抹掉。
            # 语义对齐重构前的 `self._current_ws = None` / `self._current_send_lock = None`，
            # 是刻意保留的既有行为，**不要顺手改成「仅当仍是自己时才注销」** ——
            # 它的失败方式是静默的（前端收不到推送但不报错），改动前请看
            # `transports/push_registry.py` 模块 docstring 的单槽位语义一节。
            get_push_registry().unregister(WS_PUSH_SUBSCRIBER_ID)
            self._clear_ws_acp_client_capabilities(ws)
            connection_tasks = list(tasks)
            for task in connection_tasks:
                if not task.done():
                    task.cancel()
            # Gateway 进程退出/端口关闭时，必须先取消各 session 内流式生产者（SessionManager）
            # 并中止 DeepAgent 内层循环；否则仅等待 _handle_message 任务结束会一直阻塞到任务自然完成。
            try:
                await self._agent_manager.cancel_all_inflight_work(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] cancel_all_inflight_work failed")
            # Stop scheduler on server shutdown
            try:
                await self._stop_scheduler()
            except Exception:
                logger.exception("[AgentWebSocketServer] scheduler stop failed")
            try:
                from jiuwenswarm.agents.harness.team import cancel_all_team_stream_tasks_across_managers

                await cancel_all_team_stream_tasks_across_managers(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] team stream cancel failed")
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)
            self._session_stream_tasks.clear()

    async def _handle_message(self, ws: Any, raw: str | bytes, send_lock: asyncio.Lock) -> None:
        """解析一条 JSON 请求并交给汇合点处理。

        解析规则在 ``server/wire_parse.py``（两个传输共用）
        """
        result = parse_inbound(raw)
        if not result.ok:
            try:
                async with send_lock:
                    await send_wire_payload(ws, result.error_wire)
            except WebSocketConnectionClosed as send_exc:
                logger.info(
                    "[AgentWebSocketServer] WebSocket 已关闭，解析错误未发送: %s",
                    format_ws_diagnostics(
                        result.log_context or {},
                        describe_ws_peer(ws),
                        describe_ws_exception(send_exc),
                    ),
                )
            return

        request = result.request
        # 汇合点在server/pipeline.py
        ctx = RequestContext(
            request=request,
            sink=WSSink(ws, send_lock),
            connection_id=str(id(ws)),
            services=AgentServerServices(self),
        )
        await dispatch_parsed_request(ctx, request, peer=ws)

    @staticmethod
    async def _trigger_before_ws_server_start_hook() -> None:
        """在首次启动之前触发扩展；未初始化 ExtensionRegistry 时跳过。"""
        from jiuwenswarm.extensions.registry import ExtensionRegistry
        from jiuwenswarm.common.utils import get_agent_skills_dir

        try:
            ctx = AgentWsServerStartHookContext(skills_dir=str(get_agent_skills_dir()))
            await ExtensionRegistry.get_instance().trigger(
                AgentServerHookEvents.BEFORE_WS_SERVER_START, ctx
            )
        except RuntimeError:
            logger.debug(
                "[AgentWebSocketServer] ExtensionRegistry unavailable, skip BEFORE_WS_SERVER_START"
            )

    @staticmethod
    async def _trigger_agent_server_started_hook() -> None:
        """在agentserver启动成功触发扩展；未初始化 ExtensionRegistry 时跳过。"""
        from jiuwenswarm.extensions.registry import ExtensionRegistry
        from jiuwenswarm.common.utils import get_agent_skills_dir

        try:
            registry = ExtensionRegistry.get_instance()
        except RuntimeError:
            return

        ctx = AgentWsServerStartHookContext(skills_dir=str(get_agent_skills_dir()))
        await registry.trigger(AgentServerHookEvents.AGENT_SERVER_STARTED, ctx)

    @staticmethod
    def _resolve_code_language() -> str:
        """Determine the display language for code mode plan approval messages.

        Returns ``"cn"`` or ``"en"`` based on configuration.
        Defaults to ``"cn"`` if the config key is missing.
        """
        try:
            config = get_config()
            return config.get("language", "cn")
        except Exception:
            return "cn"

    async def _prepare_session_switch_owner(
        self,
        *,
        channel_id: str,
        target_session_id: str,
        previous_session_id: str,
        params: dict[str, Any],
        reason: str,
    ) -> tuple[bool, str, Any, Any, Any]:
        """Resolve switch context and run product-owner prepare (team switch).

        Returns:
            ``(target_is_team, resolved_mode, context, team_manager, dispatch_signals)``.
            ``dispatch_signals`` may be ``None`` when KVC hooks are unavailable.
        """
        target_is_team = is_team_params(params)
        _, _, resolved_mode = resolve_agent_request_mode(
            params.get("mode", "agent.plan")
        )
        context = None
        dispatch_signals = None
        try:
            from jiuwenswarm.server.runtime.session.kv_cache_product_hooks import (
                dispatch_session_switch_signals,
                resolve_session_switch_context,
            )

            context = resolve_session_switch_context(
                target_session_id=target_session_id,
                previous_session_id=previous_session_id,
                params=params,
            )
            target_is_team = context.target_is_team
            resolved_mode = context.resolved_mode
            dispatch_signals = dispatch_session_switch_signals
        except Exception as exc:
            logger.warning(
                "[AgentWebSocketServer] session switch KVC context unavailable; "
                "preserving product lifecycle: target_session_id=%s error=%s",
                target_session_id,
                exc,
            )

        previous_is_team = bool(context and context.previous_is_team)
        team_manager = None
        if target_is_team or previous_is_team:
            from jiuwenswarm.agents.harness.team import get_team_manager

            team_manager = get_team_manager(channel_id)
            await team_manager.prepare_session_switch(
                target_session_id,
                previous_session_id=(
                    previous_session_id if previous_is_team else None
                ),
                reason=reason,
            )
        return target_is_team, resolved_mode, context, team_manager, dispatch_signals

    async def _dispatch_session_switch_kvc(
        self,
        *,
        channel_id: str,
        target_session_id: str,
        previous_session_id: str,
        reason: str,
        context: Any,
        team_manager: Any,
        dispatch_signals: Any,
    ) -> None:
        """Optional KVC signals after the product owner has prepared the switch."""
        if context is None or dispatch_signals is None:
            return
        await dispatch_signals(
            context=context,
            agent_manager=self._agent_manager,
            channel_id=channel_id,
            team_manager=team_manager,
            target_session_id=target_session_id,
            previous_session_id=previous_session_id,
            reason=reason,
        )

    async def _ensure_persistent_checkpointer_response(
        self,
        request: AgentRequest,
    ) -> AgentResponse | None:
        """Return an error response when persistent checkpoint storage is unavailable."""
        try:
            from jiuwenswarm.server.runtime.agent_adapter.interface_deep import ensure_persistent_checkpointer

            await ensure_persistent_checkpointer()
            return None
        except Exception as exc:
            logger.exception(
                "[AgentWebSocketServer] persistent checkpointer unavailable: request_id=%s error=%s",
                request.request_id,
                exc,
            )
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": "persistent checkpointer is unavailable",
                    "code": "CHECKPOINT_UNAVAILABLE",
                },
                metadata=request.metadata,
            )

    @staticmethod
    def _resolve_adapter(agent: Any) -> Any:
        """从 JiuwenSwarm 中提取底层 Deep/Code Adapter (持 _sys_operation_card 的实例)."""
        if agent is None:
            return None
        for attr in ("_adapter", "adapter", "_active_adapter"):
            inner = getattr(agent, attr, None)
            if inner is not None and hasattr(inner, "apply_sandbox_runtime_patch"):
                return inner
        # 兜底: agent 本身有相关方法
        if hasattr(agent, "apply_sandbox_runtime_patch"):
            return agent
        return None

    @staticmethod
    def resolve_adapter(agent: Any) -> Any:
        """Public wrapper for :meth:`_resolve_adapter` (避开 protected-access)."""
        return AgentWebSocketServer._resolve_adapter(agent)

    @staticmethod
    def _is_tcp_port_bindable(host: str, port: int) -> bool:
        """``True`` 表示当前能在 ``host:port`` 上 ``bind`` 成功 (即没有被占用)。

        不去探测 ``/health`` 之类应用层信息——只看四层占用情况, 谁占着、占着的
        是不是 jiuwenbox 都不关心。
        """
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                sock.bind((host, port))
            except OSError:
                return False
            return True
        finally:
            sock.close()

    @staticmethod
    def _pick_free_tcp_port(host: str) -> int:
        """让内核挑一个空闲端口 (``bind`` 到 0); 仅用于绑定测试, 不会真正监听。

        存在 TOCTOU 风险 (返回后端口可能立即被别人抢), 但接下来 uvicorn 起来
        通常足够快; 即便撞上, uvicorn 自己会因 EADDRINUSE 失败, 上游再报错。
        """
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _tenant_pool() -> TenantAgentPool:
        return TenantAgentPool.get_instance()

    async def send_push(self, msg) -> None:
        """AgentServer 主动向 Gateway 推送消息。

        payload 格式与 AgentResponse.payload 一致，
        可含 event_type 等字段供 Gateway 转为 Message 派发到 Channel。

        扇出给 :class:`PushRegistry` 里的全部订阅者：Gateway WS 连接与经
        ``GET /api/v1/events/stream`` 订阅的 HTTP 客户端都在其中，推送只有这一条
        路径。有订阅者即送达，一个都没有则记 warning。
        """
        registry = get_push_registry()
        if registry.subscriber_count() == 0:
            # 一个去处都没有：保持原有告警与早退（连 wire 都不构造）。
            logger.warning(
                "[AgentWebSocketServer] send_push 失败: 无活跃 Gateway 连接"
            )
            return

        try:
            wire = build_server_push_wire(msg)
        except Exception as e:
            logger.warning("[AgentWebSocketServer] send_push 失败: %s", e)
            return

        delivered = await registry.push(wire)

        if delivered == 0:
            # 两种情况都会落到这里：内容过大被降级成错误帧（sink 返回 False），
            # 以及订阅者按 session/channel 过滤后无人匹配。
            logger.warning(
                "[AgentWebSocketServer] send_push 无订阅者成功接收"
                "（内容过大降级为错误帧，或订阅者过滤后无人匹配）: channel_id=%s",
                msg.get("channel_id", ""),
            )
            return

        response_kind = str(msg.get("response_kind") or "").strip()
        if response_kind:
            logger.info(
                "[AgentWebSocketServer] send_push response_kind wire sent: "
                "channel_id=%s kind=%s delivered=%d",
                msg.get("channel_id", ""),
                response_kind,
                delivered,
            )
        else:
            logger.info(
                "[AgentWebSocketServer] send_push 已发送(E2A wire): channel_id=%s delivered=%d",
                msg.get("channel_id", ""),
                delivered,
            )

    def get_agent(self):
        """获取 default agent 实例（向后兼容）."""
        return self._agent_manager.get_agent_nowait()

    def get_agent_manager(self) -> AgentManager:
        """获取 AgentManager 实例."""
        return self._agent_manager

    async def handle_acp_tool_response_for_test(
            self,
            ws: Any,
            request: AgentRequest,
            send_lock: asyncio.Lock,
    ) -> None:
        """Public test helper that delegates to ACP tool-response handling."""
        # 只有 handlers.bootstrap 需要延迟导入（模块级导入会成环）；
        # RequestContext / AgentServerServices / WSSink 模块级已导入，重复导入会遮蔽外层名字。
        from jiuwenswarm.server.handlers.bootstrap import handle_acp_tool_response

        await handle_acp_tool_response(
            RequestContext(
                request=request,
                sink=WSSink(ws, send_lock),
                connection_id=str(id(ws)),
                services=AgentServerServices(self),
            )
        )

    def is_working(self) -> bool:
        """返回 Agent 是否正在工作.

        用于沙箱保活校验。

        Returns:
            bool: 是否正在工作
        """
        return self._agent_manager.is_working()

    def _build_model_cache(self) -> None:
        """Build model cache from jiuwenswarm config.yaml (reuse interface_deep logic)."""
        from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter

        config = get_config()

        # Use the same model building method as interface_deep
        build_model_from_entry = getattr(JiuWenSwarmDeepAdapter, '_build_model_from_entry')

        # Build from models.defaults list
        for entry in get_default_models(config):
            mcc = entry.get("model_client_config") or {}
            model_name = mcc.get("model_name")
            if not model_name:
                continue
            mco = entry.get("model_config_obj") or {}
            self._model_cache[model_name] = build_model_from_entry(mcc, mco)

        # Fallback to legacy format if needed (same as interface_deep._build_model_cache_legacy)
        if not self._model_cache:
            default_model_config = config.get("models", {}).get("default", {})
            react_config = config.get("react", {})
            mcc = dict(
                default_model_config.get("model_client_config")
                or react_config.get("model_client_config")
                or {}
            )
            model_name = mcc.get("model_name") or react_config.get("model_name") or "gpt-4"
            if "model_name" not in mcc:
                mcc["model_name"] = model_name
            mco = (
                default_model_config.get("model_config_obj")
                or react_config.get("model_config_obj")
                or {}
            )
            self._model_cache[model_name] = build_model_from_entry(mcc, mco)

        # Set default model (first one)
        if self._model_cache:
            first_name = next(iter(self._model_cache))
            self._default_model = self._model_cache[first_name]
            logger.info(
                "[AgentServer] Built model cache with %d models, default=%s",
                len(self._model_cache), first_name
            )

