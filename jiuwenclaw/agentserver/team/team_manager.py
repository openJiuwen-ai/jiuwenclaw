# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# coding: utf-8

"""Team lifecycle manager."""

from __future__ import annotations

import asyncio
import copy
import logging
import hashlib
import re
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.runtime.pool import RuntimeState
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.runner import Runner
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.session_metadata import get_session_metadata
from jiuwenclaw.agentserver.team import kv_cache_hooks
from jiuwenclaw.agentserver.team.bootstrap import configure_agent_teams_home
from jiuwenclaw.agentserver.team.config_loader import (
    load_team_spec_dict,
)
from jiuwenclaw.agentserver.team.distributed_runtime import (
    ensure_postgresql_for_leader,
    extract_pg_endpoint,
    fallback_distributed_to_local,
    is_distributed_mode,
    missing_distributed_dependencies,
    is_pg_available,
    is_postgresql_storage,
    normalize_distributed_transport_fields,
    parse_port,
    run_command,
    runtime_member_name,
    runtime_role,
    try_start_pg_cluster,
)
from jiuwenclaw.agentserver.team.handlers.team_monitor_handler import TeamMonitorHandler
from jiuwenclaw.agentserver.team.remote_member_bootstrap import release_a2x_reservations_for_session
from jiuwenclaw.agentserver.team.prompt_skill_mount import (
    PromptSkillMountResult,
    mount_leader_prompt_skills,
)
from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
    MemberInfo,
    RuntimeInfo,
    TeamWorkspaceInfo,
)
from jiuwenclaw.agentserver.team.team_skill_links import (
    ensure_skill_dir_links,
    sync_skill_dir_links,
)
from jiuwenclaw.config import (
    get_config,
    get_default_models,
    hydrate_model_client_config_from_tip,
)
from jiuwenclaw.utils import (
    get_agent_skills_dir,
    get_shared_agent_skills_dirs,
    get_user_workspace_dir,
)

configure_agent_teams_home()


logger = logging.getLogger(__name__)

# Wall-clock cap for a single external command (pg_isready, systemctl, etc.).
_SUBPROCESS_TIMEOUT_SEC = 120.0
# After pg_ctlcluster/systemd reports start, the server may still be initializing.
_PG_POST_START_READY_MAX_SEC = 30.0
_PG_POST_START_READY_INIT_SLEEP = 0.4
_PG_POST_START_READY_MAX_SLEEP = 2.0
_PG_POST_START_READY_BACKOFF = 1.45
_PG_POST_START_LOG_EVERY_SEC = 5.0
_TEAM_STREAM_EXIT_GRACE_TIMEOUT_SEC = 1.5
# ── Team Observability ──────────────────────────────────────
# Tracks whether observability is currently active so we can
# detect config toggles (enabled → disabled or vice-versa)
# and init / shutdown accordingly on each team request.
_observability_active: bool = False


def sync_team_observability() -> None:
    """Synchronize observability state with current config.

    Called before each ``Runner.run_agent_team_streaming`` so that
    hot-reloading the ``team_observability.enabled`` flag takes
    effect immediately:

    * disabled → enabled : ``init_observability()``
    * enabled → disabled : ``shutdown_observability()``
    * unchanged          : no-op
    """
    global _observability_active
    cfg = get_config().get("team_observability", {}) or {}
    want_enabled = bool(cfg.get("enabled", False))

    if want_enabled and not _observability_active:
        try:
            from openjiuwen.agent_teams.observability import (
                ObservabilityConfig,
                init_observability,
                is_initialized,
            )
            if is_initialized():
                _observability_active = True
                return
            obs_cfg = ObservabilityConfig(
                enabled=True,
                service_name=cfg.get("service_name", "jiuwenclaw"),
                exporter=cfg.get("exporter", "otlp_grpc"),
                endpoint=cfg.get("endpoint", "http://localhost:4317"),
                sample_rate=cfg.get("sample_rate", 1.0),
                attribute_value_max_length=cfg.get("attribute_value_max_length", 10240),
                redact_prompts=cfg.get("redact_prompts", False),
                redact_completions=cfg.get("redact_completions", False),
                langfuse_public_key=cfg.get("langfuse_public_key", ""),
                langfuse_secret_key=cfg.get("langfuse_secret_key", ""),
                traces_dir=cfg.get("traces_dir") or str(get_user_workspace_dir() / ".trace"),
                file_retention_days=cfg.get("file_retention_days", 7),
            )
            init_observability(obs_cfg)
            _observability_active = True
            if obs_cfg.exporter == "file":
                logger.info(
                    "[TeamObservability] enabled: exporter=%s traces_dir=%s",
                    obs_cfg.exporter, obs_cfg.traces_dir,
                )
            else:
                logger.info(
                    "[TeamObservability] enabled: exporter=%s endpoint=%s",
                    obs_cfg.exporter, obs_cfg.endpoint,
                )
        except Exception as exc:
            logger.warning("[TeamObservability] init failed: %s", exc)

    elif not want_enabled and _observability_active:
        shutdown_team_observability()


def shutdown_team_observability() -> None:
    """Shutdown team observability (called on disable or process exit)."""
    global _observability_active
    if not _observability_active:
        return
    try:
        from openjiuwen.agent_teams.observability import shutdown_observability
        shutdown_observability()
        _observability_active = False
        logger.info("[TeamObservability] disabled")
    except Exception as exc:
        logger.warning("[TeamObservability] shutdown failed: %s", exc)


@dataclass
class TeamRailMountContext:
    """Context needed to rebuild team rails after a hot config toggle."""

    agent: Any
    member_info: MemberInfo
    runtime: RuntimeInfo
    team_workspace: TeamWorkspaceInfo


async def _stop_team_messager(team_agent: Any, *, session_id: str) -> None:
    """Stop a team's mailbox transport so per-team ZMQ sockets release their ports."""
    infra = getattr(team_agent, "infra", None)
    messager = getattr(infra, "messager", None) if infra is not None else None
    stop = getattr(messager, "stop", None)
    if not callable(stop):
        return
    try:
        await stop()
        logger.info("[TeamManager] team messager stopped: session_id=%s", session_id)
    except Exception as exc:
        logger.warning("[TeamManager] team messager stop failed: session_id=%s error=%s", session_id, exc)


def _runner_team_runtime_manager(runner: Any) -> Any:
    """Return Runner's team runtime manager without calling its protected method."""
    attr_name = "_team_runtime_manager"
    manager = vars(runner).get(attr_name)
    if manager is None:
        from openjiuwen.agent_teams.runtime import TeamRuntimeManager

        manager = TeamRuntimeManager()
        setattr(runner, attr_name, manager)
    return manager


class TeamManager:
    """Manage team instances across sessions."""

    def __init__(self):
        # These TeamAgent objects are auxiliary runtimes used only by the
        # distributed teammate bootstrap path. Local leader execution is owned
        # by Runner's TeamRuntimePool instead.
        self._team_agents: dict[str, TeamAgent] = {}
        self._runner_team_agents: dict[str, TeamAgent] = {}
        self._team_monitors: dict[str, TeamMonitorHandler] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        # Last non-resume user query per session — rewrite reconnect/continue phrases.
        self._last_user_queries: dict[str, str] = {}
        self._bootstrap_lock = asyncio.Lock()
        self._distributed_switch_lock = asyncio.Lock()
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        # 当 cancel 请求到达时设置，通知正在执行的 pause 操作中止自身并让 cancel 执行
        self._cancel_requested: dict[str, bool] = {}
        # 追踪当前正在执行的 pause 任务，供 cancel 抢占取消
        self._active_pause_tasks: dict[str, asyncio.Task] = {}
        self._active_team_names: dict[str, str] = {}
        self._pending_team_names: dict[str, str] = {}
        # Survives clear_active_runtime after pause so continue can restore /
        # open RESUME_FROM_PAUSE even while another session is active.
        self._paused_team_names: dict[str, str] = {}
        # session_id → list of (request_id, asyncio.Queue) waiters
        self._pending_waiters: dict[str, list[tuple[str, asyncio.Queue]]] = {}
        # session_id → cron team round completion state. Lifetime-coupled to
        # _pending_waiters: set by _try_finish_cron_team_stream, popped by the
        # finisher coroutines once the cron stream ends.
        self._cron_team_completion: dict[str, dict[str, Any]] = {}
        # Sessions that have successfully initialized a team runtime at least once.
        # Persists across stream lifecycles so follow-up requests are not
        # misidentified as first requests.
        self._initialized_sessions: set[str] = set()
        # Team skill self-evolution is not enabled; registries stay empty stubs.
        self._team_skill_rails: dict[str, Any] = {}
        self._team_member_skill_evolution_rails: dict[str, list[Any]] = {}
        self._team_skill_create_rails: dict[str, Any] = {}
        # session_id → context used to rebuild team rails on config enable
        self._team_rail_contexts: dict[str, TeamRailMountContext] = {}
        # session_id → live rails and owning DeepAgent, for hot-unregister
        self._team_live_rails: dict[str, list[tuple[Any, Any]]] = {}
        # session_id → evolution watcher task
        self._team_evolution_watchers: dict[str, asyncio.Task] = {}
        # session_id → runtime_ready requested a watcher before the rail registered
        self._pending_team_evolution_watcher_sessions: set[str] = set()
        # session_id -> team workspace skills directory used as the shared link view.
        self._team_shared_skill_link_targets: dict[str, Path] = {}
        # session_id → workflow handler instance
        self._workflow_handlers: dict[str, Any] = {}
        # session_id → True once a team-building event (team.member,
        # team.task, workflow.updated) has been broadcast in the current
        # round.  Reset when a new round starts.
        self._seen_team_events: dict[str, bool] = {}
        # session_id → True after workflow.updated(status=completed/…)
        # is received.  When True, chat.final is no longer suppressed
        # even if seen_team_events is True.
        self._workflow_completed: dict[str, bool] = {}

    def has_stream_task(self, session_id: str) -> bool:
        return session_id in self._stream_tasks

    def pop_stream_task(self, session_id: str) -> asyncio.Task | None:
        return self._stream_tasks.pop(session_id, None)

    def is_session_initialized(self, session_id: str) -> bool:
        """Return whether the session has ever initialized a team runtime."""
        return session_id in self._initialized_sessions

    def clear_session_initialized(self, session_id: str) -> None:
        """Clear the initialized marker for a session (e.g. on stream end)."""
        self._initialized_sessions.discard(session_id)

    def has_waiters(self, session_id: str) -> bool:
        """Return whether there are pending waiters for the given session."""
        return bool(self._pending_waiters.get(session_id))

    def add_waiter(self, session_id: str, request_id: str, queue: asyncio.Queue) -> None:
        """Register a waiter queue for a session's event stream."""
        self._pending_waiters.setdefault(session_id, []).append((request_id, queue))

    def remove_waiter(self, session_id: str, request_id: str) -> None:
        """Remove a waiter by request_id; clean up empty lists."""
        waiters = self._pending_waiters.get(session_id)
        if waiters is None:
            return
        remaining = [(rid, q) for rid, q in waiters if rid != request_id]
        if remaining:
            self._pending_waiters[session_id] = remaining
        else:
            self._pending_waiters.pop(session_id, None)

    def broadcast_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Broadcast an event to all request queues waiting on the same session."""
        waiters = self._pending_waiters.get(session_id)
        if waiters:
            for request_id, queue in waiters:
                try:
                    queue.put_nowait(dict(event))
                except Exception:
                    logger.debug(
                        "[TeamManager] broadcast failed: session_id=%s request_id=%s",
                        session_id,
                        request_id,
                    )

    # --- seen_team_events tracking ---
    # A session enters "team" mode once any team-building event (team.member,
    # team.task, workflow.updated, team.runtime_ready) is broadcast.  While
    # the flag is set, chat.final must NOT be forwarded to the frontend
    # because the team may still be running; only team.completed (via
    # chat.processing_status is_complete=True) should finalize the round.

    def mark_seen_team_events(self, session_id: str) -> None:
        """Record that a team-building event has been broadcast for this session."""
        self._seen_team_events[session_id] = True

    def has_seen_team_events(self, session_id: str) -> bool:
        """Return whether any team-building event has been broadcast in this round."""
        return self._seen_team_events.get(session_id, False)

    def reset_seen_team_events(self, session_id: str) -> None:
        """Reset the flag at the start of a new conversation round."""
        self._seen_team_events.pop(session_id, None)

    def mark_workflow_completed(self, session_id: str) -> None:
        """Mark that the workflow has reached a terminal status."""
        self._workflow_completed[session_id] = True

    def is_workflow_completed(self, session_id: str) -> bool:
        return self._workflow_completed.get(session_id, False)

    def reset_workflow_completed(self, session_id: str) -> None:
        self._workflow_completed.pop(session_id, None)

    def get_waiters(self, session_id: str) -> list[tuple[str, asyncio.Queue]]:
        """Return the (request_id, queue) pairs waiting on the given session."""
        return self._pending_waiters.get(session_id, [])

    def get_cron_completion(self, session_id: str) -> dict[str, Any] | None:
        """Return the cron team round completion state for the session, if any."""
        return self._cron_team_completion.get(session_id)

    def setdefault_cron_completion(
        self, session_id: str, default: dict[str, Any]
    ) -> dict[str, Any]:
        """Get or create the cron team round completion state for the session."""
        return self._cron_team_completion.setdefault(session_id, default)

    def pop_cron_completion(self, session_id: str) -> dict[str, Any] | None:
        """Drop the cron team round completion state for the session."""
        return self._cron_team_completion.pop(session_id, None)

    def is_runtime_active(self, session_id: str) -> bool:
        """Return whether a Runner-owned runtime is active for the session."""
        return session_id in self._active_team_names

    def is_runtime_pending(self, session_id: str) -> bool:
        """Return whether runtime activation is pending for the session."""
        return session_id in self._pending_team_names

    def get_active_team_name(self, session_id: str) -> str | None:
        """Return the active Runner-owned team name for the session."""
        return self._active_team_names.get(session_id)

    def _get_lifecycle_lock(self, session_id: str) -> asyncio.Lock:
        """Return the lock that serializes lifecycle ops for one session.

        Always per-session. Cross-session isolation must not share a global
        lock: holding pause on session A must not block create/pause on B.
        Distributed bootstrap / single-session switch use ``_bootstrap_lock``
        and ``_distributed_switch_lock`` at those call sites explicitly.
        """
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    def get_monitor(self, session_id: str) -> TeamMonitorHandler | None:
        return self._team_monitors.get(session_id)

    def get_team_evolution_watcher(self, session_id: str) -> asyncio.Task | None:
        return self._team_evolution_watchers.get(session_id)

    def register_team_evolution_watcher(self, session_id: str, task: asyncio.Task) -> None:
        self._team_evolution_watchers[session_id] = task

    def pop_team_evolution_watcher(self, session_id: str) -> asyncio.Task | None:
        return self._team_evolution_watchers.pop(session_id, None)

    def mark_team_evolution_watcher_deferred(self, session_id: str) -> None:
        self._pending_team_evolution_watcher_sessions.add(session_id)

    def consume_team_evolution_watcher_deferred(self, session_id: str) -> bool:
        if session_id not in self._pending_team_evolution_watcher_sessions:
            return False
        self._pending_team_evolution_watcher_sessions.discard(session_id)
        return True

    @staticmethod
    def _is_distributed_mode(config_base: dict[str, Any]) -> bool:
        return is_distributed_mode(config_base)

    @staticmethod
    def _runtime_role(config_base: dict[str, Any]) -> str:
        return runtime_role(config_base)

    @staticmethod
    def _runtime_member_name(config_base: dict[str, Any], team_cfg: dict[str, Any]) -> str | None:
        return runtime_member_name(config_base, team_cfg)

    @staticmethod
    def _normalize_distributed_transport_fields(
        config_base: dict[str, Any],
        team_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        return normalize_distributed_transport_fields(config_base, team_cfg)

    @staticmethod
    def normalize_distributed_transport_fields(
        config_base: dict[str, Any],
        team_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Public wrapper for distributed transport normalization."""
        return TeamManager._normalize_distributed_transport_fields(config_base, team_cfg)

    @staticmethod
    def _parse_port(value: Any, default: int, field_name: str) -> int:
        return parse_port(value, default, field_name)

    @staticmethod
    def parse_port(value: Any, default: int, field_name: str) -> int:
        """Public wrapper for validated port parsing."""
        return TeamManager._parse_port(value, default, field_name)

    @staticmethod
    def _normalize_team_identity_fields(team_cfg: dict[str, Any]) -> dict[str, Any]:
        normalized_cfg = copy.deepcopy(team_cfg)
        leader_cfg = normalized_cfg.get("leader", {})
        if isinstance(leader_cfg, dict):
            display_name = str(leader_cfg.get("display_name", "")).strip()
            name = str(leader_cfg.get("name", "")).strip()
            if display_name and not name:
                leader_cfg["name"] = display_name
            elif name and not display_name:
                leader_cfg["display_name"] = name

        members = normalized_cfg.get("predefined_members", [])
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                display_name = str(member.get("display_name", "")).strip()
                name = str(member.get("name", "")).strip()
                if display_name and not name:
                    member["name"] = display_name
                elif name and not display_name:
                    member["display_name"] = name
        return normalized_cfg

    @staticmethod
    def build_session_scoped_team_name(team_name: str, session_id: str) -> str:
        """构造 session 作用域的 team_name（对外公开 API）。

        供网关等外部模块做 team/session 一致性校验时复用，避免直接访问受保护成员。
        """
        base_name = str(team_name or "").strip() or "team"
        session_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "").strip())
        session_suffix = session_suffix.strip("._-")
        if not session_suffix:
            return base_name
        if base_name.endswith(f"_{session_suffix}"):
            return base_name
        return f"{base_name}_{session_suffix}"

    @staticmethod
    def _apply_session_scoped_team_name(
        spec: TeamAgentSpec,
        *,
        session_id: str,
    ) -> None:
        spec.team_name = TeamManager.build_session_scoped_team_name(
            spec.team_name,
            session_id,
        )

    @staticmethod
    def _load_team_spec(
        session_id: str,
        *,
        requested_model_name: str | None = None,
        template_id: str | None = None,
    ) -> TeamAgentSpec:
        config_base = get_config()
        # Keep dependency checks scoped to distributed mode to make the
        # control flow explicit at the call site (local mode bypasses checks).
        if TeamManager._is_distributed_mode(config_base):
            missing = missing_distributed_dependencies(config_base)
            if missing:
                missing_list = ", ".join(missing)
                logger.warning(
                    "[TeamManager][MISSING_DISTRIBUTE_DEPS] missing=%s",
                    missing_list,
                )
                logger.error(
                    "[TeamManager][FALLBACK_TO_LOCAL] "
                    "distributed runtime is not available; downgraded to local mode "
                    "for current process"
                )
                logger.warning(
                    "[TeamManager][ACTION] install via: "
                    "pip install -e \".[distribute]\" or uv sync --extra distribute"
                )
                config_base = fallback_distributed_to_local(config_base)

        spec_dict = load_team_spec_dict(
            config_base=config_base,
            requested_model_name=requested_model_name,
            template_id=template_id,
        )
        spec_dict = TeamManager._normalize_team_identity_fields(spec_dict)
        if TeamManager._is_distributed_mode(config_base):
            spec_dict = TeamManager._normalize_distributed_transport_fields(config_base, spec_dict)

        # When models.defaults has more than one entry, populate model_pool
        # and set model_pool_strategy to by_model_name so team members
        # can be assigned different model endpoints from the pool.
        default_models = get_default_models(config_base)
        if len(default_models) > 1:
            from openjiuwen.agent_teams.schema.team import ModelPoolEntry

            pool_entries: list[dict] = []
            for entry in default_models:
                mcc = entry.get("model_client_config") or {}
                mco = entry.get("model_config_obj") or {}
                if not isinstance(mcc, dict):
                    continue
                # Tip-only: fill pool credentials from memory tip, not ${API_*}.
                mcc = hydrate_model_client_config_from_tip(mcc)
                if not mcc.get("model_name"):
                    continue
                # Drop internal-only hints before they reach ModelRequestConfig.
                request_config = dict(mco) if isinstance(mco, dict) else {}
                request_config.pop("reasoning_level", None)
                request_config.pop("model", None)
                request_config.pop("model_name", None)
                pool_entry = ModelPoolEntry(
                    model_name=mcc["model_name"],
                    api_key=mcc.get("api_key", ""),
                    api_base_url=mcc.get("api_base", ""),
                    api_provider=mcc.get("client_provider", ""),
                    metadata={
                        "client": {
                            k: v for k, v in mcc.items()
                            if k not in ("model_name", "api_key", "api_base", "client_provider") and v is not None
                        },
                        "request": request_config,
                    },
                )
                pool_entries.append(pool_entry.model_dump())

            if pool_entries:
                spec_dict["model_pool"] = pool_entries
                spec_dict["model_pool_strategy"] = "by_model_name"

        return TeamAgentSpec.model_validate(spec_dict)

    async def get_swarm_enriched_team_spec(
        self,
        session_id: str,
        *,
        mode: str,
        project_dir: str | None = None,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
        requested_model_name: str | None = None,
        template_id: str | None = None,
    ) -> TeamAgentSpec:
        """Build a team spec via provider-based assembly (no parent DeepAgent).

        Sources every member capability from the shared config source through
        ``enrich_team_spec_for_swarm`` instead of inheriting from a pre-built
        single agent, so creating a team never requires constructing one first.

        Args:
            session_id: Active session id.
            mode: Request mode (e.g. "team").
            project_dir: Resolved project directory, if any.
            request_id: Originating request id, if any.
            channel_id: Raw channel id from the request, if any.
            request_metadata: Request metadata mapping.
            template_id: Bound ``modes.team`` key / chat.send ``team_name``.

        Returns:
            The enriched ``TeamAgentSpec`` ready to build (``build_context`` set;
            assembly is fully declarative, no imperative post-processing).
        """
        config_base = get_config()
        await self._ensure_postgresql_for_leader(config_base)
        spec = self._load_team_spec(
            session_id,
            requested_model_name=requested_model_name,
            template_id=template_id,
        )
        self._apply_session_scoped_team_name(spec, session_id=session_id)
        self.apply_team_plan_mode(spec, request_metadata=request_metadata)
        from jiuwenclaw.agentserver.swarm import enrich_team_spec_for_swarm

        enrich_team_spec_for_swarm(
            spec,
            session_id=session_id,
            mode=mode,
            project_dir=project_dir,
            request_id=request_id,
            channel_id=channel_id,
            request_metadata=request_metadata,
        )
        return spec

    @staticmethod
    def apply_team_plan_mode(
        spec: TeamAgentSpec,
        *,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        mode = str((request_metadata or {}).get("mode") or "").strip().lower()
        if mode == "team.plan":
            try:
                spec.enable_team_plan = True
            except (AttributeError, ValueError):
                object.__setattr__(spec, "enable_team_plan", True)

    async def prepare_runtime_activation(self, session_id: str, team_name: str) -> None:
        if self._is_distributed_mode(get_config()):
            async with self._distributed_switch_lock:
                await self._wait_same_session_runner_runtime_released(session_id)
                await self._stop_stale_distributed_sessions(
                    session_id,
                    reason="switch runtime: ",
                )
                self._pending_team_names[session_id] = team_name
            return

        self._pending_team_names[session_id] = team_name

    async def _wait_same_session_runner_runtime_released(
        self,
        session_id: str,
        *,
        timeout_sec: float = 5.0,
        poll_interval_sec: float = 0.1,
    ) -> None:
        """Before same-session rebuild, wait old Runner runtime/messager to stop."""
        if not self._is_distributed_mode(get_config()):
            return
        if not self.is_runtime_active(session_id):
            return
        if self.has_stream_task(session_id):
            return

        # Best-effort eager stop for the cached Runner-owned team agent transport.
        await self._stop_runner_team_agent_transport(session_id)

        team_name = self._resolve_session_team_name(session_id)
        if not team_name:
            return

        from openjiuwen.core.runner.runner import GLOBAL_RUNNER

        runtime_mgr = _runner_team_runtime_manager(GLOBAL_RUNNER)
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while time.monotonic() < deadline:
            active_team = await runtime_mgr.pool.get(team_name)
            if active_team is None:
                logger.info(
                    "[TeamManager] same-session runtime released before rebuild: "
                    "session_id=%s team_name=%s",
                    session_id,
                    team_name,
                )
                return
            await asyncio.sleep(max(0.02, poll_interval_sec))

        logger.warning(
            "[TeamManager] same-session runtime still active before rebuild timeout: "
            "session_id=%s team_name=%s timeout=%.1fs",
            session_id,
            team_name,
            timeout_sec,
        )

    async def prepare_session_switch(
        self,
        target_session_id: str,
        reason: str = "",
        previous_session_id: str | None = None,
    ) -> None:
        """Enforce the distributed runtime's single-session switch policy.

        Local Runner-owned teams use session-scoped team names and may stay
        active concurrently. Distributed deployments retain the existing
        single-session behavior because their bootstrap resources are scoped to
        one active session per channel.
        """
        normalized_previous = str(previous_session_id or "").strip()
        pre_signaled_session_ids: set[str] = set()
        if normalized_previous and normalized_previous != target_session_id:
            await self.offload_session_kv_cache(
                normalized_previous,
                reason=f"{reason}session-switch",
            )
            pre_signaled_session_ids.add(normalized_previous)

        if not self._is_distributed_mode(get_config()):
            logger.info(
                "[TeamManager] %sprepare_session_switch skipped for local runtime target=%s",
                reason,
                target_session_id,
            )
            return

        async with self._distributed_switch_lock:
            await self._stop_stale_distributed_sessions(
                target_session_id,
                reason=reason,
                pre_signaled_session_ids=pre_signaled_session_ids,
            )

    async def offload_session_kv_cache(self, session_id: str, reason: str = "") -> bool:
        """Dispatch KVC offload for a Team session without changing runtime state."""
        return await kv_cache_hooks.dispatch_for_session(
            "offload",
            session_id=session_id,
            reason=reason,
            resolve_team_name=self._lookup_session_team_name,
        )

    async def prefetch_session_kv_cache(self, session_id: str, reason: str = "") -> bool:
        """Dispatch KVC prefetch for a historical Team session without resuming it."""
        return await kv_cache_hooks.dispatch_for_session(
            "prefetch",
            session_id=session_id,
            reason=f"{reason}history-resume",
            resolve_team_name=self._lookup_session_team_name,
        )

    async def _stop_stale_distributed_sessions(
        self,
        target_session_id: str,
        *,
        reason: str,
        pre_signaled_session_ids: set[str] | None = None,
    ) -> None:
        """Stop active or pending distributed sessions except the target."""
        stale_sessions = [
            session_id
            for session_id in self._active_team_names
            if session_id != target_session_id
        ]
        stale_sessions.extend(
            session_id
            for session_id in self._pending_team_names
            if session_id != target_session_id
        )
        logger.info(
            "[TeamManager] %sprepare_session_switch target=%s active=%s pending=%s stale=%s",
            reason,
            target_session_id,
            list(self._active_team_names),
            list(self._pending_team_names),
            list(dict.fromkeys(stale_sessions)),
        )

        already_signaled = pre_signaled_session_ids or set()
        for stale_session_id in dict.fromkeys(stale_sessions):
            if stale_session_id not in already_signaled:
                await self.offload_session_kv_cache(
                    stale_session_id,
                    reason=f"{reason}session-switch",
                )
            await self.stop_session_runtime(
                stale_session_id,
                reason=reason,
            )

    def commit_runtime_ready(self, session_id: str, team_name: str) -> None:
        self._active_team_names[session_id] = team_name
        self._pending_team_names.pop(session_id, None)
        self._paused_team_names.pop(session_id, None)
        self._initialized_sessions.add(session_id)
        logger.info(
            "[TeamManager] commit_runtime_ready session_id=%s team_name=%s active=%s pending=%s",
            session_id,
            team_name,
            list(self._active_team_names),
            list(self._pending_team_names),
        )

    def clear_pending_runtime(self, session_id: str) -> None:
        self._pending_team_names.pop(session_id, None)

    def clear_active_runtime(self, session_id: str, *, bookmark_paused: bool = False) -> None:
        removed = self._active_team_names.pop(session_id, None)
        if removed is not None:
            if bookmark_paused:
                # Keep a pause bookmark so interact/continue can resolve the team
                # after active is cleared (multi-session: another team may be active).
                self._paused_team_names[session_id] = removed
            else:
                self._paused_team_names.pop(session_id, None)
            logger.info(
                "[TeamManager] clear_active_runtime: session_id=%s team_name=%s "
                "remaining_active=%s paused=%s bookmark_paused=%s",
                session_id, removed, list(self._active_team_names),
                list(self._paused_team_names), bookmark_paused,
            )

    def clear_paused_runtime(self, session_id: str) -> None:
        self._paused_team_names.pop(session_id, None)

    def get_paused_team_name(self, session_id: str) -> str | None:
        return self._paused_team_names.get(session_id)

    def _lookup_session_team_name(self, session_id: str) -> str | None:
        active_team_name = self._active_team_names.get(session_id)
        if active_team_name:
            return active_team_name
        pending_team_name = self._pending_team_names.get(session_id)
        if pending_team_name:
            return pending_team_name
        paused_team_name = self._paused_team_names.get(session_id)
        if paused_team_name:
            return paused_team_name

        metadata = get_session_metadata(session_id)
        team_name = str(metadata.get("team_name") or "").strip()
        return team_name or None

    def _resolve_session_team_name(self, session_id: str) -> str | None:
        team_name = self._lookup_session_team_name(session_id)
        if team_name:
            return team_name

        logger.warning(
            "[TeamManager] failed to resolve team_name from active/pending/metadata: session_id=%s",
            session_id,
        )
        return None

    async def _resolve_resumable_runner_entry(self, session_id: str) -> tuple[str, Any] | None:
        """Return a same-session paused/running Runner pool entry when resumable."""
        from openjiuwen.core.runner.runner import GLOBAL_RUNNER

        runtime_mgr = _runner_team_runtime_manager(GLOBAL_RUNNER)

        async def _entry_for(team_name: str) -> tuple[str, Any] | None:
            entry = await runtime_mgr.pool.get(team_name)
            if entry is None or getattr(entry, "current_session_id", None) != session_id:
                return None
            # Trust the Runner pool over claw-local active/pending markers here.
            # The local markers can be stale after a team.plan round pauses on
            # exit_plan_mode, but the pool still owns the resumable runtime.
            if getattr(entry, "state", None) not in {RuntimeState.PAUSED, RuntimeState.RUNNING}:
                return None
            return team_name, entry

        team_name = self._lookup_session_team_name(session_id)
        if team_name:
            resolved = await _entry_for(team_name)
            if resolved is not None:
                return resolved

        # Multi-session race: active markers cleared after pause while another
        # session is running. Scan the pool by session_id.
        try:
            for entry in await runtime_mgr.pool.teams_for_session(session_id):
                if getattr(entry, "state", None) not in {RuntimeState.PAUSED, RuntimeState.RUNNING}:
                    continue
                name = getattr(entry, "team_name", None)
                if name:
                    return str(name), entry
        except Exception:
            logger.debug(
                "[TeamManager] pool scan for resumable session failed: session_id=%s",
                session_id,
                exc_info=True,
            )
        return None

    async def has_resumable_runtime(self, session_id: str) -> bool:
        return await self._resolve_resumable_runner_entry(session_id) is not None

    async def has_paused_runtime(self, session_id: str) -> bool:
        """True only when the same-session Runner pool entry is PAUSED.

        Unlike ``has_resumable_runtime`` (PAUSED|RUNNING), this is the hard
        pause→continue signal: live follow-up interact must not be skipped.
        """
        if self._paused_team_names.get(session_id):
            return True
        resolved = await self._resolve_resumable_runner_entry(session_id)
        if resolved is None:
            return False
        _team_name, entry = resolved
        return getattr(entry, "state", None) is RuntimeState.PAUSED

    async def session_has_runtime(self, session_id: str) -> bool:
        return (
            self.is_runtime_active(session_id)
            or self.is_runtime_pending(session_id)
            or self.has_stream_task(session_id)
            or await self.has_resumable_runtime(session_id)
        )

    def _restore_active_runtime(self, session_id: str, team_name: str) -> None:
        self._active_team_names[session_id] = team_name
        self._pending_team_names.pop(session_id, None)
        self._paused_team_names.pop(session_id, None)
        logger.info(
            "[TeamManager] restored resumable runtime: session_id=%s team_name=%s active=%s pending=%s",
            session_id,
            team_name,
            list(self._active_team_names),
            list(self._pending_team_names),
        )

    async def restore_resumable_runtime(self, session_id: str) -> bool:
        resolved = await self._resolve_resumable_runner_entry(session_id)
        if resolved is None:
            return False
        team_name, _entry = resolved
        self._restore_active_runtime(session_id, team_name)
        return True

    async def wait_for_resumable_runtime(
        self,
        session_id: str,
        *,
        timeout_sec: float = 1.0,
        poll_interval_sec: float = 0.05,
    ) -> bool:
        """Best-effort wait for a paused/running Runner pool entry to become resumable."""
        if self.is_runtime_active(session_id):
            return True
        if await self.restore_resumable_runtime(session_id):
            return True

        deadline = time.monotonic() + max(0.0, timeout_sec)
        sleep_sec = max(0.01, poll_interval_sec)
        while time.monotonic() < deadline:
            await asyncio.sleep(sleep_sec)
            if await self.restore_resumable_runtime(session_id):
                logger.info(
                    "[TeamManager] recovered resumable runtime after wait: session_id=%s",
                    session_id,
                )
                return True
        return self.is_runtime_active(session_id)

    @staticmethod
    def _resolve_delete_session_team_name(session_id: str) -> str | None:
        metadata = get_session_metadata(session_id)
        team_name = str(metadata.get("team_name") or "").strip()
        if team_name:
            return team_name

        logger.warning(
            "[TeamManager] failed to resolve delete team_name from metadata: session_id=%s",
            session_id,
        )
        return None

    @staticmethod
    def _is_postgresql_storage(team_cfg: dict[str, Any]) -> bool:
        return is_postgresql_storage(team_cfg)

    @staticmethod
    def _extract_pg_endpoint(team_cfg: dict[str, Any]) -> tuple[str, int]:
        return extract_pg_endpoint(team_cfg)

    @staticmethod
    async def _run_command(*args: str) -> tuple[int, str]:
        return await run_command(*args, subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _is_pg_available(self, host: str, port: int) -> bool:
        return await is_pg_available(host, port, subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _try_start_pg_cluster(self) -> bool:
        return await try_start_pg_cluster(subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _ensure_postgresql_for_leader(self, config_base: dict[str, Any]) -> None:
        await ensure_postgresql_for_leader(
            config_base,
            subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC,
            post_start_ready_max_sec=_PG_POST_START_READY_MAX_SEC,
            post_start_ready_init_sleep=_PG_POST_START_READY_INIT_SLEEP,
            post_start_ready_max_sleep=_PG_POST_START_READY_MAX_SLEEP,
            post_start_ready_backoff=_PG_POST_START_READY_BACKOFF,
            post_start_log_every_sec=_PG_POST_START_LOG_EVERY_SEC,
        )

    @staticmethod
    def _initialize_team_shared_skill_links(spec: TeamAgentSpec) -> None:
        """Initialize team shared skill links from the global skill root + relay shared dirs."""
        global_skills_dir = get_agent_skills_dir()
        global_skills_dir.mkdir(parents=True, exist_ok=True)

        # Relay-configured skill directories (e.g. office-claw-skills) must be
        # linked into the global skills dir before syncing to team-shared,
        # because each member's SkillManager reads get_agent_skills_dir()
        # directly. ensure-only (no prune) to avoid removing skills installed
        # by the user or other teams.
        for shared_dir in get_shared_agent_skills_dirs():
            ensure_skill_dir_links(shared_dir, global_skills_dir)

        # Resolve team workspace path
        ws_config = spec.workspace
        ws_path = ws_config.root_path if ws_config and ws_config.root_path else None
        if not ws_path:
            ws_path = str(team_home(spec.team_name) / "team-workspace")

        team_shared_skills_dir = Path(ws_path) / "skills"

        team_shared_skills_dir.mkdir(parents=True, exist_ok=True)
        sync_skill_dir_links(global_skills_dir, team_shared_skills_dir)

        logger.info("[TeamManager] Initialized team shared skill links: %s", team_shared_skills_dir)

    @staticmethod
    def _resolve_team_shared_skills_dir(spec: TeamAgentSpec) -> Path:
        ws_config = spec.workspace
        ws_path = ws_config.root_path if ws_config and ws_config.root_path else None
        if not ws_path:
            ws_path = str(team_home(spec.team_name) / "team-workspace")
        return Path(ws_path) / "skills"

    @staticmethod
    def ensure_team_shared_skills_initialized(spec: TeamAgentSpec) -> None:
        """Ensure team shared skills are available in the team workspace."""
        TeamManager._initialize_team_shared_skill_links(spec)

    def ensure_team_shared_skills_ready_for_session(self, session_id: str, spec: TeamAgentSpec) -> None:
        """Ensure team shared skills are initialized and registered for refresh."""
        self.ensure_team_shared_skills_initialized(spec)
        self.register_team_shared_skill_link_target(
            session_id,
            self._resolve_team_shared_skills_dir(spec),
        )

    def register_team_shared_skill_link_target(self, session_id: str, target: Path) -> None:
        """Register the team shared skills directory for link refresh."""
        self._team_shared_skill_link_targets[session_id] = target

    @staticmethod
    def ensure_leader_prompt_skills_ready_for_session(
        session_id: str,
        spec: TeamAgentSpec,
        query: str,
    ) -> PromptSkillMountResult:
        """Mount prompt-selected skills into this conversation's Leader-only view."""
        build_context = getattr(spec, "build_context", None)
        target_value = str(getattr(build_context, "leader_skills_dir", "") or "").strip()
        if target_value:
            target = Path(target_value)
        else:
            target = TeamManager._resolve_team_shared_skills_dir(spec).parent / "leader-skills"
        return mount_leader_prompt_skills(
            session_id=session_id,
            query=query,
            target_dir=target,
        )

    def refresh_team_shared_skill_links(self, session_id: str) -> bool:
        """Refresh team shared skill links from global skills + relay shared dirs."""
        target = self._team_shared_skill_link_targets.get(session_id)
        if target is None:
            logger.debug("[TeamManager] no team shared skill link target for session_id=%s", session_id)
            return False
        global_skills_dir = get_agent_skills_dir()
        global_skills_dir.mkdir(parents=True, exist_ok=True)
        for shared_dir in get_shared_agent_skills_dirs():
            ensure_skill_dir_links(shared_dir, global_skills_dir)
        sync_skill_dir_links(global_skills_dir, target)
        logger.info("[TeamManager] Refreshed team shared skill links: session_id=%s target=%s", session_id, target)
        return True

    def refresh_all_team_shared_skill_links(self) -> int:
        """Refresh every registered team shared skill link view."""
        refreshed = 0
        for session_id in list(self._team_shared_skill_link_targets):
            if self.refresh_team_shared_skill_links(session_id):
                refreshed += 1
        return refreshed

    async def create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        """Build an auxiliary TeamAgent for distributed teammate bootstrap.

        Local leader requests are built and owned by Runner's TeamRuntimePool;
        they must not use this cache.
        """
        config_base = get_config()
        await self._ensure_postgresql_for_leader(config_base)
        logger.info("[TeamManager] building TeamAgentSpec: session_id=%s", session_id)
        spec = self._load_team_spec(session_id)
        self._apply_session_scoped_team_name(
            spec,
            session_id=session_id,
        )

        resolved_mode = str((request_metadata or {}).get("mode") or "").strip()
        self.apply_team_plan_mode(spec, request_metadata=request_metadata)
        from jiuwenclaw.agentserver.swarm import enrich_team_spec_for_swarm

        enrich_team_spec_for_swarm(
            spec,
            session_id=session_id,
            mode=resolved_mode or "team",
            project_dir=(
                str((request_metadata or {}).get("effective_project_dir") or "").strip()
                or str((request_metadata or {}).get("project_dir") or "").strip()
                or None
            ),
            request_id=request_id,
            channel_id=channel_id,
            request_metadata=request_metadata,
        )

        logger.info("[TeamManager] TeamAgentSpec ready: team_name=%s", spec.team_name)

        token = set_session_id(session_id)
        try:
            logger.info("[TeamManager] creating TeamAgent from spec")
            team_agent = spec.build()
            team_agent.channel_id = channel_id  # 记录 channel，供 _destroy_other_sessions 按 channel 隔离
            self._team_agents[session_id] = team_agent
            # After build, initialize team shared skill links.
            self.ensure_team_shared_skills_ready_for_session(session_id, spec)

            if self._is_distributed_mode(config_base):
                try:
                    from jiuwenclaw.agentserver.team.remote_member_bootstrap import (
                        attach_build_team_post_tool_registration_hook,
                        attach_clean_team_distributed_teardown_wrapper,
                        attach_distributed_local_spawn_guard,
                        attach_remote_bootstrap_ack_listener,
                        attach_shutdown_member_remote_cleanup_wrapper,
                    )

                    attach_distributed_local_spawn_guard(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_build_team_post_tool_registration_hook(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_shutdown_member_remote_cleanup_wrapper(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_clean_team_distributed_teardown_wrapper(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_remote_bootstrap_ack_listener(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] remote_member_bootstrap wrapper attach failed: %s",
                        exc,
                    )
            logger.info(
                "[TeamManager] Team created: session_id=%s, team_name=%s",
                session_id,
                spec.team_name,
            )
            return team_agent
        finally:
            reset_session_id(token)

    async def get_or_create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        """Return the distributed bootstrap TeamAgent for a session.

        This cache is only used by remote member bootstrap on distributed
        teammate processes. Distributed channels intentionally retain a single
        cached session and destroy the previous auxiliary TeamAgent on switch.
        """
        async with self._bootstrap_lock:
            team_agent = self._team_agents.get(session_id)
            if team_agent is not None:
                return team_agent

            await self._destroy_other_sessions(session_id, channel_id)
            return await self.create_team(
                session_id,
                deep_agent,
                request_id,
                channel_id,
                request_metadata,
            )

    async def interact(self, session_id: str, user_input: Any) -> tuple[bool, str | None]:
        try:
            if not self.is_runtime_active(session_id):
                restored = await self.wait_for_resumable_runtime(session_id)
                if restored:
                    logger.info(
                        "[TeamManager] interact restored paused runtime before delivery: session_id=%s",
                        session_id,
                    )

            team_name = self.get_active_team_name(session_id)
            if not team_name:
                logger.warning(
                    "[TeamManager] interact ignored for non-active team session: "
                    "session_id=%s active_sessions=%s reason=not_active",
                    session_id,
                    list(self._active_team_names),
                )
                return False, "not_active"

            result = await Runner.interact_agent_team(
                user_input,
                team_name=team_name,
                session_id=session_id,
            )
            if not result:
                reason = getattr(result, "reason", None) or "runner_failed"
                logger.warning(
                    "[TeamManager] interact failed against runner runtime: session_id=%s team=%s reason=%s",
                    session_id,
                    team_name,
                    reason,
                )
                return False, reason
            return True, None
        except Exception as exc:
            logger.error("[TeamManager] interact failed: session_id=%s, error=%s", session_id, exc)
            return False, "exception"

    # Team skill self-evolution stubs (not enabled).

    def get_team_skill_rail(self, session_id: str) -> Any | None:
        return self._team_skill_rails.get(session_id)

    def get_team_skill_create_rail(self, session_id: str) -> Any | None:
        return self._team_skill_create_rails.get(session_id)

    @staticmethod
    def find_team_skill_rail_for_request(request_id: str) -> Any | None:
        """Stub: team skill evolution is not enabled."""
        return None

    async def drain_team_skill_events(self, session_id: str) -> list[dict]:
        """Stub: no team skill evolution rail to drain."""
        return []

    @staticmethod
    def register_team_skill_rail(session_id: str, rail: Any) -> None:
        """No-op stub: team skill evolution is not enabled."""

    @staticmethod
    def register_team_member_skill_evolution_rail(session_id: str, rail: Any) -> None:
        """No-op stub: team skill evolution is not enabled."""

    @staticmethod
    def register_team_skill_create_rail(session_id: str, rail: Any) -> None:
        """No-op stub: team skill create is not enabled."""

    @staticmethod
    def register_team_rail_context(session_id: str, context: TeamRailMountContext) -> None:
        """No-op stub: team evolution rail rebuild is not enabled."""

    def get_team_rail_context(self, session_id: str) -> TeamRailMountContext | None:
        """Return the stored leader rail mount context for a session."""
        return self._team_rail_contexts.get(session_id)

    @staticmethod
    def register_team_live_rail(session_id: str, agent: Any, rail: Any) -> None:
        """No-op stub: team evolution live rails are not enabled."""

    def _clear_team_rail_registries(self, session_id: str) -> None:
        self._team_skill_rails.pop(session_id, None)
        self._team_member_skill_evolution_rails.pop(session_id, None)
        self._team_skill_create_rails.pop(session_id, None)
        self._team_rail_contexts.pop(session_id, None)
        self._team_live_rails.pop(session_id, None)
        self._team_shared_skill_link_targets.pop(session_id, None)

    async def _cancel_team_evolution_watcher(self, session_id: str) -> None:
        watcher_task = self._team_evolution_watchers.pop(session_id, None)
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] evolution watcher stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

    @staticmethod
    def _build_and_mount_member_rails_for_context(
        session_id: str,
        context: TeamRailMountContext,
        *,
        mount_team_skill_rail: bool,
        mount_team_skill_create_rail: bool,
        mount_skill_evolution_rail: bool,
    ) -> tuple[Any | None, Any | None]:
        """Stub: team skill self-evolution rails are not enabled."""
        return None, None

    async def update_evolution_config(self, config: dict[str, Any] | None) -> None:
        """No-op stub: team skill self-evolution is not enabled."""

    async def destroy_team(self, session_id: str) -> bool:
        async with self._bootstrap_lock:
            return await self._destroy_team(session_id)

    async def _destroy_other_sessions(self, current_session_id: str, channel_id: str | None = None) -> None:
        """Destroy stale distributed bootstrap TeamAgents on session switch.

        按 channel_id 过滤：singleton 模式下多个 channel 共享同一个 TeamManager，
        只销毁同 channel 的旧 session，不影响其他 channel 正在运行的 team 实例。
        """
        stale_session_ids = []
        for sid, agent in self._team_agents.items():
            if sid != current_session_id and (
                channel_id is None or getattr(agent, 'channel_id', None) == channel_id
            ):
                stale_session_ids.append(sid)
        for stale_session_id in stale_session_ids:
            await self._destroy_team(stale_session_id)

    async def _destroy_team(self, session_id: str) -> bool:
        await self._cleanup_runtime_locals(session_id)

        team_agent = self._team_agents.pop(session_id, None)
        cleaned = False
        try:
            if team_agent is None:
                logger.info("[TeamManager] no in-memory team for session_id=%s", session_id)
                return False

            token = set_session_id(session_id)
            try:
                try:
                    cleaned = await team_agent.destroy_team(force=True)
                finally:
                    await release_a2x_reservations_for_session(session_id, team_agent=team_agent)
                    await _stop_team_messager(team_agent, session_id=session_id)
            finally:
                reset_session_id(token)

            logger.info(
                "[TeamManager] Team cleaned via core API: session_id=%s cleaned=%s",
                session_id,
                cleaned,
            )
        except Exception as exc:
            logger.error(
                "[TeamManager] destroy team failed: session_id=%s error=%s",
                session_id,
                exc,
            )

        return cleaned

    async def cleanup_all(self) -> None:
        async with self._bootstrap_lock:
            session_ids = list(self._team_agents.keys())
            for session_id in session_ids:
                await self._destroy_team(session_id)
            logger.info("[TeamManager] all teams cleaned")

    def get_team_agent(self, session_id: str) -> TeamAgent | None:
        return self._team_agents.get(session_id)

    def get_monitor_handler(self, session_id: str) -> TeamMonitorHandler | None:
        return self._team_monitors.get(session_id)

    def register_monitor(self, session_id: str, handler: TeamMonitorHandler) -> None:
        self._team_monitors[session_id] = handler

    def pop_monitor(self, session_id: str) -> TeamMonitorHandler | None:
        """Remove and return the monitor handler for the session (rebind path)."""
        return self._team_monitors.pop(session_id, None)

    def register_workflow_handler(self, session_id: str, handler: Any) -> None:
        self._workflow_handlers[session_id] = handler

    def get_workflow_handler(self, session_id: str) -> Any | None:
        return self._workflow_handlers.get(session_id)

    def pop_workflow_handler(self, session_id: str) -> Any | None:
        return self._workflow_handlers.pop(session_id, None)

    def register_stream_task(self, session_id: str, task: asyncio.Task) -> None:
        self._stream_tasks[session_id] = task

    def remember_user_query(self, session_id: str, query: str) -> None:
        """Persist the last non-resume user query for interrupt/reconnect continue."""
        text = str(query or "").strip()
        if not text:
            return
        self._last_user_queries[session_id] = text

    def get_last_user_query(self, session_id: str) -> str | None:
        text = self._last_user_queries.get(session_id)
        return text if text else None

    def clear_last_user_query(self, session_id: str) -> None:
        self._last_user_queries.pop(session_id, None)

    def _has_local_team_runtime(self, session_id: str) -> bool:
        """Return whether the session should use the legacy in-memory TeamAgent path."""
        return self._is_distributed_mode(get_config()) and session_id in self._team_agents

    async def attach_distributed_hooks_for_runner_runtime(
        self,
        team_name: str,
        session_id: str,
        channel_id: str | None = None,
    ) -> bool:
        """Attach distributed bootstrap hooks to Runner-owned TeamAgent.

        When team streaming uses Runner.run_agent_team_streaming(), the actual
        TeamAgent is created and cached by openjiuwen TeamRuntimeManager pool,
        not by TeamManager.create_team(). This method retrieves the Runner-owned
        TeamAgent from GLOBAL_RUNNER's pool and attaches distributed hooks.

        Args:
            team_name: Team name to look up in Runner pool.
            session_id: Session identifier for hook context.
            channel_id: Channel identifier for hook context.

        Returns:
            True if hooks attached successfully, False otherwise.
        """
        config_base = get_config()
        if not self._is_distributed_mode(config_base):
            logger.debug(
                "[TeamManager] non-distributed mode; skip Runner runtime hooks "
                "team_name=%s session_id=%s",
                team_name,
                session_id,
            )
            return False

        from openjiuwen.core.runner.runner import GLOBAL_RUNNER

        runtime_mgr = _runner_team_runtime_manager(GLOBAL_RUNNER)
        active_team = await runtime_mgr.pool.get(team_name)
        if active_team is None:
            logger.warning(
                "[TeamManager] Runner pool has no active team for distributed hooks "
                "team_name=%s session_id=%s",
                team_name,
                session_id,
            )
            return False

        team_agent = active_team.agent
        if team_agent is None:
            logger.warning(
                "[TeamManager] ActiveTeam has no agent instance for distributed hooks "
                "team_name=%s session_id=%s",
                team_name,
                session_id,
            )
            return False

        self._runner_team_agents[session_id] = team_agent

        try:
            from jiuwenclaw.agentserver.team.remote_member_bootstrap import (
                attach_build_team_post_tool_registration_hook,
                attach_clean_team_distributed_teardown_wrapper,
                attach_distributed_local_spawn_guard,
                attach_remote_bootstrap_ack_listener,
                attach_shutdown_member_remote_cleanup_wrapper,
            )

            attach_distributed_local_spawn_guard(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_build_team_post_tool_registration_hook(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_shutdown_member_remote_cleanup_wrapper(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_clean_team_distributed_teardown_wrapper(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_remote_bootstrap_ack_listener(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            logger.info(
                "[TeamManager] distributed hooks attached to Runner-owned TeamAgent "
                "team_name=%s session_id=%s channel_id=%s",
                team_name,
                session_id,
                channel_id,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[TeamManager] distributed hooks attach failed for Runner-owned TeamAgent "
                "team_name=%s session_id=%s error=%s",
                team_name,
                session_id,
                exc,
            )
            return False

    async def _stop_local_team_runtime(self, session_id: str, team_agent: TeamAgent) -> bool:
        stopped = False
        stop_coordination = getattr(team_agent, "stop_coordination", None) or getattr(
            team_agent,
            "_stop_coordination",
            None,
        )
        if callable(stop_coordination):
            try:
                await stop_coordination()
                stopped = True
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stop local team coordination failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        try:
            await release_a2x_reservations_for_session(session_id, team_agent=team_agent)
        except Exception as exc:
            logger.warning(
                "[TeamManager] release A2X reservations failed: session_id=%s error=%s",
                session_id,
                exc,
            )
        try:
            await _stop_team_messager(team_agent, session_id=session_id)
        except Exception as exc:
            logger.warning(
                "[TeamManager] stop local team messager failed: session_id=%s error=%s",
                session_id,
                exc,
            )
        return stopped

    async def _stop_runner_team_agent_transport(self, session_id: str) -> None:
        if not self._is_distributed_mode(get_config()):
            self._runner_team_agents.pop(session_id, None)
            return

        team_agent = self._runner_team_agents.pop(session_id, None)
        if team_agent is None:
            return

        stop_coordination = getattr(team_agent, "stop_coordination", None)
        if callable(stop_coordination):
            try:
                await stop_coordination()
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stop Runner-owned team coordination failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        try:
            await _stop_team_messager(team_agent, session_id=session_id)
        except Exception as exc:
            logger.warning(
                "[TeamManager] stop Runner-owned team messager failed: session_id=%s error=%s",
                session_id,
                exc,
            )

    async def _cleanup_runtime_locals(
        self, session_id: str, *, finalize_workflows: bool = True
    ) -> None:
        watcher_task = self._team_evolution_watchers.pop(session_id, None)
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] evolution watcher stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        stream_task = self._stream_tasks.pop(session_id, None)
        if stream_task and not stream_task.done():
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stream stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        monitor_handler = self._team_monitors.pop(session_id, None)
        if monitor_handler is not None:
            try:
                await monitor_handler.stop()
            except Exception as exc:
                logger.warning(
                    "[TeamManager] monitor stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        workflow_handler = self.pop_workflow_handler(session_id)
        if workflow_handler is not None:
            try:
                # On non-resumable teardown the team runtime (and the swarmflow
                # background task it drives) is gone, so no further workflow
                # events can arrive — finalize any still-running run to a
                # terminal status before stopping, otherwise the checkpoint
                # would keep it 'running' forever. Pause keeps the runtime
                # parked and resumable in place, so it opts out.
                if finalize_workflows:
                    workflow_handler.finalize_pending_runs()
                await workflow_handler.stop()
                logger.info(
                    "[WF_DBG cleanup] workflow handler stopped: session_id=%s "
                    "finalized=%s",
                    session_id,
                    finalize_workflows,
                )
            except Exception as exc:
                logger.warning(
                    "[WF_DBG cleanup] workflow handler stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        self._clear_team_rail_registries(session_id)

    async def _stop_runner_team_runtime(
        self, session_id: str, team_name: str, caller: str
    ) -> bool:
        """Stop Runner-owned team runtime, with proper cancellation and error handling.

        Args:
            session_id: The session ID
            team_name: The team name to stop
            caller: Caller identifier for logging (e.g., "cancel", "terminate", "pause")

        Returns:
            True if stop was successful, False otherwise
        """
        try:
            result = await Runner.stop_agent_team(
                team_name=team_name,
                session_id=session_id,
            )
            logger.info(
                "[TeamManager] %s: Runner pool entry removed: "
                "session_id=%s team_name=%s",
                caller,
                session_id,
                team_name,
            )
            return result
        except asyncio.CancelledError:
            logger.warning(
                "[TeamManager] %s: Runner stop cancelled: "
                "session_id=%s team_name=%s",
                caller,
                session_id,
                team_name,
            )
            return False
        except Exception as exc:
            logger.warning(
                "[TeamManager] %s: Runner stop failed: "
                "session_id=%s team_name=%s error=%s",
                caller,
                session_id,
                team_name,
                exc,
            )
            return False

    async def _finalize_runtime_cleanup(self, session_id: str, caller: str) -> None:
        """Finalize runtime cleanup: cleanup locals and clear active/pending registrations."""
        logger.info(
            "[TeamManager] %s: executing cleanup, session_id=%s",
            caller,
            session_id,
        )
        await self._cleanup_runtime_locals(session_id)
        logger.info(
            "[TeamManager] %s: cleanup done, clearing active, session_id=%s",
            caller,
            session_id,
        )
        self.clear_active_runtime(session_id)
        self.clear_pending_runtime(session_id)
        logger.info(
            "[TeamManager] %s: clear done, session_id=%s",
            caller,
            session_id,
        )

    async def _wait_for_stream_task_exit(
        self,
        session_id: str,
        *,
        timeout_sec: float = _TEAM_STREAM_EXIT_GRACE_TIMEOUT_SEC,
    ) -> bool:
        """Wait briefly for a team stream task to finish its own cleanup."""
        stream_task = self._stream_tasks.get(session_id)
        if stream_task is None or stream_task.done():
            return True

        try:
            await asyncio.wait_for(
                asyncio.shield(stream_task),
                timeout=timeout_sec,
            )
            logger.info(
                "[TeamManager] stream task exited within grace timeout: "
                "session_id=%s timeout_sec=%.1f",
                session_id,
                timeout_sec,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "[TeamManager] stream task did not exit within grace timeout: "
                "session_id=%s timeout_sec=%.1f; cancelling during cleanup",
                session_id,
                timeout_sec,
            )
            return False
        except asyncio.CancelledError:
            if stream_task.done() and stream_task.cancelled():
                logger.info(
                    "[TeamManager] stream task was cancelled during grace timeout: "
                    "session_id=%s",
                    session_id,
                )
                return True
            raise
        except Exception as exc:
            logger.warning(
                "[TeamManager] stream task exited with error during grace timeout: "
                "session_id=%s error=%s",
                session_id,
                exc,
            )
            return True

    async def terminate_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Stop-like teardown for the current team session runtime.

        This stops the foreground stream/monitor owned by claw and then asks the
        Runner-owned team runtime to enter the stop state. Used for explicit
        team stop so the same session can resume later.
        """
        async with self._get_lifecycle_lock(session_id):
            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._team_monitors
                or self.is_runtime_active(session_id)
                or self.is_runtime_pending(session_id)
            )
            if not has_stream_task and not has_team_runtime:
                return False
            logger.info(
                "[TeamManager] %s terminate team session runtime: session_id=%s",
                reason,
                session_id,
            )

            # Resolve team_name early before cleanup, from active/pending/metadata
            team_name = self._resolve_session_team_name(session_id)

            await kv_cache_hooks.dispatch_signal(
                "offload",
                session_id=session_id,
                team_name=team_name,
                reason=f"{reason}team-terminate",
            )

            # Stop Runner-owned runtime first before cleaning locals
            # to avoid gate/teardown races
            if team_name:
                await self._stop_runner_team_runtime(session_id, team_name, "terminate")

            if has_local_team_runtime:
                cleaned = await self._destroy_team(session_id)
            else:
                cleaned = False

            await self._finalize_runtime_cleanup(session_id, "terminate")
        logger.info(
            "[TeamManager] %steam session terminated: session_id=%s cleaned=%s",
            reason,
            session_id,
            cleaned,
        )
        return True

    async def cancel_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Cancel the current team session runtime, removing it from Runner pool.

        Unlike pause/terminate, this fully stops the Runner-owned team runtime
        so it is removed from the pool. This prevents subsequent sessions from
        hitting "present in pool but missing from DB" reject_inconsistent errors.

        Used for team cancel intent where the session should not be resumed.
        """
        logger.info(
            "[TeamManager] cancel_session_runtime 入口: session_id=%s reason=%s",
            session_id, reason,
        )
        # 通知正在执行的 pause 操作中止自身，让 cancel 尽快获取 lifecycle lock
        self._cancel_requested[session_id] = True

        # 抢占：主动取消正在执行的 pause 任务，使其抛出 CancelledError 释放锁
        pause_task = self._active_pause_tasks.get(session_id)
        if pause_task and not pause_task.done():
            pause_task.cancel()
            logger.info(
                "[TeamManager] cancel: preempting pause task, session_id=%s",
                session_id,
            )

        # 如果 lifecycle lock 被其他操作（如 pause）持有，先尝试直接停止 Runner
        # 以避免 cancel 被 pause 阻塞长达数分钟
        lock = self._get_lifecycle_lock(session_id)
        if lock.locked():
            team_name = self._resolve_session_team_name(session_id)
            if team_name:
                await self._stop_runner_team_runtime(
                    session_id, team_name, "cancel: forced"
                )

        async with self._get_lifecycle_lock(session_id):
            # 清理 cancel_requested 标志
            self._cancel_requested.pop(session_id, None)

            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._team_monitors
                or self.is_runtime_active(session_id)
                or self.is_runtime_pending(session_id)
            )
            if not has_stream_task and not has_team_runtime:
                return False

            logger.info(
                "[TeamManager] %s cancel team session runtime: session_id=%s",
                reason,
                session_id,
            )

            # Resolve team_name early before cleanup, from active/pending/metadata
            team_name = self._resolve_session_team_name(session_id)

            # Stop Runner-owned runtime first before cancelling stream task
            # to avoid gate/teardown races and ensure pool removal
            runner_stopped = False
            if team_name:
                runner_stopped = await self._stop_runner_team_runtime(
                    session_id, team_name, "cancel"
                )
                await self._stop_runner_team_agent_transport(session_id)

            cleaned = False

            await self._finalize_runtime_cleanup(session_id, "cancel")

        logger.info(
            "[TeamManager] %steam session cancelled: session_id=%s cleaned=%s runner_stopped=%s",
            reason,
            session_id,
            cleaned,
            runner_stopped,
        )
        return True

    async def stop_session_runtime(
        self,
        session_id: str,
        reason: str = "",
        *,
        stop_runner: bool = True,
    ) -> bool:
        """Stop local runtime resources and, by default, the Runner runtime.

        Permanent delete callers leave the Runner runtime alive until
        ``Runner.delete_agent_team(force=True)`` so agent-core can snapshot its
        member bindings before performing the equivalent stop itself.
        """
        async with self._get_lifecycle_lock(session_id):
            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._runner_team_agents
                or session_id in self._team_monitors
                or self.is_runtime_active(session_id)
                or self.is_runtime_pending(session_id)
            )
            if not has_stream_task and not has_team_runtime:
                return False

            logger.info(
                "[TeamManager] %s stop team session runtime: session_id=%s",
                reason,
                session_id,
            )
            team_agent = self._team_agents.pop(session_id, None) if has_local_team_runtime else None
            await self._cleanup_runtime_locals(session_id)

            stopped = False
            if has_local_team_runtime and team_agent is not None:
                stopped = await self._stop_local_team_runtime(session_id, team_agent)

            team_name = self._resolve_session_team_name(session_id)

            if team_name and stop_runner:
                try:
                    runner_stopped = await Runner.stop_agent_team(team_name=team_name, session_id=session_id)
                    stopped = runner_stopped or stopped
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] runner stop failed: session_id=%s team_name=%s error=%s",
                        session_id,
                        team_name,
                        exc,
                    )
            if not has_local_team_runtime:
                try:
                    team_agent = self._runner_team_agents.get(session_id)
                    await release_a2x_reservations_for_session(session_id, team_agent=team_agent)
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] release A2X reservations failed: session_id=%s error=%s",
                        session_id,
                        exc,
                    )
                if stop_runner:
                    await self._stop_runner_team_agent_transport(session_id)
                else:
                    # Runner.delete_agent_team(force=True) still owns the
                    # actual TeamAgent stop; drop only Jiuwenswarm's mirror.
                    self._runner_team_agents.pop(session_id, None)

            self.clear_active_runtime(session_id)
            self.clear_pending_runtime(session_id)

        logger.info(
            "[TeamManager] %steam session stopped: session_id=%s stopped=%s",
            reason,
            session_id,
            stopped,
        )
        return True

    def is_pause_in_progress(self, session_id: str) -> bool:
        """Return whether ``pause_session_runtime`` is still running for session."""
        task = self._active_pause_tasks.get(session_id)
        return task is not None and not task.done()

    async def wait_for_pause_complete(
        self,
        session_id: str,
        *,
        timeout_sec: float | None = None,
    ) -> bool:
        """Await an in-flight pause so continue/chat.send does not race it.

        Protocol stop parks via ``Runner.pause`` first; the foreground stream
        then exits (kernel ``close_stream``). Continue must wait here so it
        does not open ``RESUME_FROM_PAUSE`` against a half-paused pool.

        Default: wait until pause finishes (no wall-clock abandon). Optional
        ``timeout_sec`` is only for callers that explicitly need a bound.
        """
        task = self._active_pause_tasks.get(session_id)
        if task is None or task.done():
            return True
        logger.info(
            "[TeamManager] waiting for in-flight pause: session_id=%s timeout=%s",
            session_id,
            "none" if timeout_sec is None else f"{timeout_sec:.1f}s",
        )
        try:
            if timeout_sec is None:
                await asyncio.shield(task)
                return True
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, timeout_sec))
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "[TeamManager] timed out waiting for pause: session_id=%s after %.1fs",
                session_id,
                timeout_sec,
            )
            return False
        except asyncio.CancelledError:
            # Caller cancelled; pause may still be running independently.
            return not self.is_pause_in_progress(session_id)

    @staticmethod
    def _abort_harness_llm_stream(harness: Any) -> None:
        """Best-effort stop of one harness's in-flight model HTTP stream."""
        if harness is None:
            return
        active = getattr(harness, "active_round", None)
        if active is None:
            return
        try:
            active.pause_requested = True
        except Exception:
            logger.debug(
                "[TeamManager] set pause_requested failed",
                exc_info=True,
            )
        ctx = getattr(active, "model_call_ctx", None)
        if ctx is None:
            return
        request_abort = getattr(ctx, "request_abort_stream", None)
        if callable(request_abort):
            try:
                request_abort()
            except Exception:
                logger.debug(
                    "[TeamManager] request_abort_stream failed",
                    exc_info=True,
                )

    async def _resolve_active_team_for_session(self, session_id: str) -> Any | None:
        """Return the Runner-pooled TeamAgent for this session, if any."""
        team_name = self._resolve_session_team_name(session_id)
        if not team_name:
            return None
        try:
            from openjiuwen.core.runner.runner import GLOBAL_RUNNER

            runtime_mgr = _runner_team_runtime_manager(GLOBAL_RUNNER)
            return await runtime_mgr.pool.get(team_name)
        except Exception as exc:
            logger.warning(
                "[TeamManager] pool lookup failed session_id=%s: %s",
                session_id,
                exc,
            )
            return None

    async def abort_team_llm_streams_before_pause(
        self,
        session_id: str,
        reason: str = "",
    ) -> bool:
        """Cut leader + teammate LLM token burn before any freeze/teardown work.

        Calls the host abort path and each member ``abort_llm_stream`` without
        waiting for harness stop. Must run before ``freeze_leader_qa_before_pause``
        so QA persist cannot delay stopping reasoning. Full park still happens
        in ``Runner.pause_agent_team`` (idempotent).
        """
        active_team = await self._resolve_active_team_for_session(session_id)
        if active_team is None:
            return False

        harness = None
        resources = getattr(active_team, "resources", None)
        if resources is not None:
            harness = getattr(resources, "harness", None)
        self._abort_harness_llm_stream(harness)

        aborted_members = 0
        spawn_manager = getattr(active_team, "spawn_manager", None)
        handles = getattr(spawn_manager, "spawned_handles", None) or {}
        for handle in list(handles.values()):
            abort_fn = getattr(handle, "abort_llm_stream", None)
            if callable(abort_fn):
                try:
                    abort_fn()
                    aborted_members += 1
                except Exception:
                    logger.debug(
                        "[TeamManager] %smember abort_llm_stream failed session_id=%s",
                        reason,
                        session_id,
                        exc_info=True,
                    )

        logger.info(
            "[TeamManager] %saborted team LLM streams before pause: "
            "session_id=%s members=%s",
            reason,
            session_id,
            aborted_members,
        )
        return True

    async def freeze_leader_qa_before_pause(
        self,
        session_id: str,
        reason: str = "",
        *,
        timeout_sec: float = 8.0,
    ) -> bool:
        """Best-effort freeze of the live leader QA block during team pause.

        Persists the current QA block with interrupted status after LLM abort
        so freeze latency cannot keep members streaming. Team interrupt still
        skips DeepAgent ``process_interrupt``. Bound by ``timeout_sec`` so a
        stuck summarizer cannot block park.
        """
        active_team = await self._resolve_active_team_for_session(session_id)
        if active_team is None:
            return False

        team_name = self._resolve_session_team_name(session_id) or "?"
        harness = None
        resources = getattr(active_team, "resources", None)
        if resources is not None:
            harness = getattr(resources, "harness", None)
        if harness is None:
            return False

        freeze_rail = None
        for rail in list(getattr(harness, "_registered_rails", None) or []):
            if callable(getattr(rail, "freeze_current_qa_sync", None)):
                freeze_rail = rail
                break
        if freeze_rail is None:
            return False

        session = None
        try:
            session = getattr(harness, "_session", None) or getattr(harness, "session", None)
        except Exception:
            session = None
        try:
            await asyncio.wait_for(
                freeze_rail.freeze_current_qa_sync(
                    session_id,
                    agent=harness,
                    session=session,
                    status="interrupted",
                    persist_mode="sync",
                ),
                timeout=max(0.1, float(timeout_sec)),
            )
            logger.info(
                "[TeamManager] %sfrozen leader QA before pause: session_id=%s team=%s",
                reason,
                session_id,
                team_name,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "[TeamManager] %sfreeze leader QA timed out (%.1fs); continue pause "
                "session_id=%s",
                reason,
                timeout_sec,
                session_id,
            )
            return False
        except Exception as exc:
            logger.warning(
                "[TeamManager] %sfreeze leader QA failed session_id=%s: %s",
                reason,
                session_id,
                exc,
                exc_info=True,
            )
            return False

    async def pause_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Pause the current team runtime for this session (stop-work, keep-team).

        Behavior:
        1. Abort leader/member LLM streams immediately
        2. Freeze leader QA for cold resume (bounded timeout)
        3. ``Runner.pause_agent_team`` to completion
        4. Wait for the foreground stream to exit after kernel ``close_stream``
        5. Cancel the stream task only if it has not exited after grace

        Does not remove the team from the pool or call ``clean_team``.
        """
        async with self._get_lifecycle_lock(session_id):
            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._team_monitors
                or self.is_runtime_active(session_id)
                or self.is_runtime_pending(session_id)
            )
            if not has_stream_task and not has_team_runtime:
                return False

            if self._cancel_requested.get(session_id):
                logger.info(
                    "[TeamManager] %s pause aborted: cancel requested for session_id=%s",
                    reason, session_id,
                )
                return False

            logger.info(
                "[TeamManager] %s pause team session runtime: session_id=%s",
                reason,
                session_id,
            )

            # Fast-cut first (do not wait for freeze). Then snapshot context
            # while harness is still alive; finally complete kernel park.
            await self.abort_team_llm_streams_before_pause(session_id, reason=reason)
            await self.freeze_leader_qa_before_pause(session_id, reason=reason)

            self._active_pause_tasks[session_id] = asyncio.current_task()
            runner_paused = False
            try:
                team_name = self._resolve_session_team_name(session_id)
                if team_name:
                    try:
                        if self._cancel_requested.get(session_id):
                            logger.info(
                                "[TeamManager] %s pause aborted before Runner.pause: session_id=%s",
                                reason, session_id,
                            )
                            return False

                        runner_paused = await Runner.pause_agent_team(
                            team_name=team_name,
                            session_id=session_id,
                        )
                    except asyncio.CancelledError:
                        logger.info(
                            "[TeamManager] %s pause aborted: cancelled by cancel request, session_id=%s",
                            reason, session_id,
                        )
                        team_name = self._resolve_session_team_name(session_id)
                        if team_name:
                            await self._stop_runner_team_runtime(
                                session_id, team_name, "pause aborted"
                            )
                        cancelled_stream = self._pop_and_cancel_stream_task_unlocked(
                            session_id, f"{reason}pause-aborted: "
                        )
                        await self._await_cancelled_stream_task(
                            cancelled_stream, session_id=session_id
                        )
                        await self._finalize_runtime_cleanup(session_id, "pause aborted")
                        return False
                    except Exception as exc:
                        logger.warning(
                            "[TeamManager] runner pause failed: session_id=%s team_name=%s error=%s",
                            session_id,
                            team_name,
                            exc,
                        )

                # Successful park closes the team stream from inside the kernel.
                # Wait for the consumer task to finish; cancel only if it sticks.
                if runner_paused:
                    exited = await self._wait_for_stream_task_exit(session_id)
                    if not exited:
                        cancelled_stream = self._pop_and_cancel_stream_task_unlocked(
                            session_id, f"{reason}pause-stream-grace: "
                        )
                        await self._await_cancelled_stream_task(
                            cancelled_stream, session_id=session_id
                        )

                await self._cleanup_runtime_locals(session_id, finalize_workflows=False)
                # Bookmark paused runtime so the next first-request can detect
                # RESUME_FROM_PAUSE and wrap the pause-resume protocol.
                self.clear_active_runtime(session_id, bookmark_paused=True)
                self.clear_pending_runtime(session_id)
            finally:
                self._active_pause_tasks.pop(session_id, None)

        logger.info(
            "[TeamManager] %steam session paused: session_id=%s runner_paused=%s",
            reason,
            session_id,
            runner_paused,
        )
        return True

    async def delete_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Delete a team-mode session and its session-scoped team data.

        Jiuwenswarm scopes team names by session id, so deleting a
        team-mode session should delete the corresponding Agent Team
        before the caller removes the local session directory. If the
        team name cannot be resolved from session metadata, fall back to
        releasing only the session checkpoint.
        """
        team_name = self._resolve_delete_session_team_name(session_id)
        await kv_cache_hooks.stop_runtime_before_terminal_delete(
            self.stop_session_runtime,
            session_id=session_id,
            reason=reason,
        )

        try:
            if team_name:
                await Runner.delete_agent_team(
                    team_name=team_name,
                    session_ids=[session_id],
                    force=True,
                )
            else:
                logger.warning(
                    "[TeamManager] delete session runtime fell back to session release: "
                    "session_id=%s reason=missing_team_name",
                    session_id,
                )
                await Runner.release(session_id)
            logger.info(
                "[TeamManager] %steam session deleted: session_id=%s team_name=%s",
                reason,
                session_id,
                team_name,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[TeamManager] failed to delete team session runtime: session_id=%s team_name=%s error=%s",
                session_id,
                team_name,
                exc,
            )
            return False

    async def _cancel_stream_task(self, session_id: str, reason: str) -> None:
        """Cancel one stream task while serializing its lifecycle operations."""
        async with self._get_lifecycle_lock(session_id):
            task = self._pop_and_cancel_stream_task_unlocked(session_id, reason)
        await self._await_cancelled_stream_task(task, session_id=session_id)

    def _pop_and_cancel_stream_task_unlocked(
        self, session_id: str, reason: str
    ) -> asyncio.Task | None:
        """Pop+cancel stream task without taking the lifecycle lock.

        Callers that already hold ``_get_lifecycle_lock(session_id)`` must use
        this helper — ``_cancel_stream_task`` would deadlock on the same lock.
        """
        task = self._stream_tasks.pop(session_id, None)
        if task is None:
            return None
        if not task.done():
            logger.info(
                "[TeamManager] %s cancel stream task session_id=%s",
                reason,
                session_id,
            )
            task.cancel()
        return task

    async def _await_cancelled_stream_task(
        self,
        task: asyncio.Task | None,
        *,
        session_id: str,
        timeout_sec: float = 5.0,
    ) -> None:
        """Await a cancelled stream task with a hard timeout (avoid hang)."""
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(task, timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning(
                "[TeamManager] stream task did not exit after cancel within "
                "%.1fs: session_id=%s",
                timeout_sec,
                session_id,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(
                "[TeamManager] stream task await after cancel failed session_id=%s: %s",
                session_id,
                exc,
            )

    async def cancel_all_stream_tasks(self, reason: str = "") -> None:
        """Cancel Team stream tasks after AgentServer disconnects."""
        session_ids = list(self._stream_tasks)
        await asyncio.gather(
            *(self._cancel_stream_task(session_id, reason) for session_id in session_ids),
        )

    def _iter_disconnect_pause_session_ids(self) -> list[str]:
        """Sessions that may own a live team runtime when the gateway drops."""
        session_ids: set[str] = set()
        session_ids.update(self._stream_tasks)
        session_ids.update(self._active_team_names)
        session_ids.update(self._pending_team_names)
        session_ids.update(self._team_monitors)
        session_ids.update(self._runner_team_agents)
        session_ids.update(self._team_agents)
        return list(session_ids)

    async def pause_all_session_runtimes(self, reason: str = "") -> None:
        """Pause all live team runtimes (gateway disconnect / system fault).

        Stops in-flight work: stop in-flight work but keep the session
        resumable. Uses ``pause_session_runtime`` (not stop/remove-from-pool)
        so a later ``chat.send`` on the same session can continue.
        """
        session_ids = self._iter_disconnect_pause_session_ids()
        if not session_ids:
            return
        logger.info(
            "[TeamManager] %spause all team session runtimes: count=%s",
            reason,
            len(session_ids),
        )
        await asyncio.gather(
            *(self.pause_session_runtime(session_id, reason=reason) for session_id in session_ids),
            return_exceptions=True,
        )


# Singleton TeamManager: all channels share one instance. Team runtime state
# (_stream_tasks / _initialized_sessions / _active_team_names /
# _pending_waiters / _cron_team_completion / rails / watchers) is indexed by
# session_id and is per-session, never per-channel. Sharing the instance lets a
# bridged follow-up request (e.g. a /join member replying from feishu while the
# originating web stream is still
# alive) see the originating channel's runtime markers so it is correctly
# routed through interact() instead of being misidentified as a first request
# and colliding with the Runner team pool.
_team_manager: TeamManager | None = None


def get_team_manager(channel_id: str | None = None) -> TeamManager:
    """Return the singleton TeamManager instance (channel_id is ignored)."""
    global _team_manager
    if _team_manager is None:
        _team_manager = TeamManager()
    return _team_manager


def find_team_skill_rail_across_managers(request_id: str) -> Any | None:
    """Stub: team skill evolution is not enabled."""
    return None


def refresh_team_shared_skill_links_across_managers(session_id: str | None = None) -> bool:
    """Refresh team shared skill links on the singleton manager."""
    tm = get_team_manager()
    if session_id is None:
        return tm.refresh_all_team_shared_skill_links() > 0
    return tm.refresh_team_shared_skill_links(session_id)


async def cancel_all_team_stream_tasks_across_managers(reason: str = "") -> None:
    """Cancel all team stream tasks on the singleton manager."""
    await get_team_manager().cancel_all_stream_tasks(reason=reason)


async def pause_all_team_session_runtimes_across_managers(reason: str = "") -> None:
    """Pause all team session runtimes (disconnect / fault; same-session resumable)."""
    await get_team_manager().pause_all_session_runtimes(reason=reason)


async def stop_team_session_runtime_across_managers(
    session_id: str,
    reason: str = "",
    *,
    stop_runner: bool = True,
) -> bool:
    """Stop a team session runtime on the singleton manager."""
    return await get_team_manager().stop_session_runtime(
        session_id,
        reason=reason,
        stop_runner=stop_runner,
    )


def get_all_team_managers() -> list[TeamManager]:
    """Return the singleton manager wrapped in a list (callers iterate this)."""
    return [get_team_manager()]


def reset_team_manager(channel_id: str | None = None) -> None:
    """Reset the singleton TeamManager (channel_id is ignored)."""
    global _team_manager
    _team_manager = None
