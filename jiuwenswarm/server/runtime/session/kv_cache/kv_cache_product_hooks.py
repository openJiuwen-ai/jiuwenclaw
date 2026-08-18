# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Product-session adapters for optional KV cache affinity lifecycle hooks."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from jiuwenswarm.server.runtime.session import session_history, session_metadata
from jiuwenswarm.server.utils.utils import is_team_params

logger = logging.getLogger(__name__)
_PRODUCT_GUARD_TASKS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class SessionSwitchContext:
    """Facts needed by the product owner and its optional KVC hooks."""

    target_is_team: bool
    previous_is_team: bool
    resolved_mode: str
    affinity_enabled: bool


async def cancel_pending_tasks() -> None:
    """Best-effort cleanup for all Agent-side KVC signal registries."""
    cleanup_callbacks = []
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
            cancel_pending_kv_cache_lifecycle_tasks,
        )

        cleanup_callbacks.append(cancel_pending_kv_cache_lifecycle_tasks)
    except Exception as exc:
        logger.warning("[ProductKVCacheHooks] root cleanup unavailable: %s", exc)
    try:
        from openjiuwen.core.foundation.kv_cache import (
            cancel_pending_session_kv_cache_signals,
        )

        cleanup_callbacks.append(cancel_pending_session_kv_cache_signals)
    except Exception as exc:
        logger.warning("[ProductKVCacheHooks] Plan cleanup unavailable: %s", exc)
    try:
        from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
            cancel_pending_signal_tasks,
        )

        cleanup_callbacks.append(cancel_pending_signal_tasks)
    except Exception as exc:
        logger.warning("[ProductKVCacheHooks] Team cleanup unavailable: %s", exc)

    for cleanup in cleanup_callbacks:
        try:
            await cleanup()
        except Exception as exc:
            logger.warning(
                "[ProductKVCacheHooks] pending task cleanup failed: cleanup=%s error=%s",
                getattr(cleanup, "__name__", type(cleanup).__name__),
                exc,
            )
    tasks = tuple(_PRODUCT_GUARD_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_task_guard import (
            get_session_kv_cache_task_guard,
        )

        get_session_kv_cache_task_guard().clear()
    except Exception as exc:
        logger.warning("[ProductKVCacheHooks] task guard cleanup failed: %s", exc)


async def evict_plan_session(
    *,
    session_id: str,
    agent: Any = None,
    agent_manager: Any = None,
    channel_id: str | None = None,
) -> bool:
    """Best-effort evict for a permanently deleted non-Team session."""
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
            evict_session_kv_cache,
            is_kv_cache_affinity_enabled,
        )

        if not is_kv_cache_affinity_enabled():
            return False
        if agent is None and agent_manager is not None:
            try:
                agent = agent_manager.get_agent_nowait(channel_id)
            except Exception as exc:
                logger.warning(
                    "[ProductKVCacheHooks] live Plan agent unavailable for delete; "
                    "falling back to configured model: channel_id=%s error=%s",
                    channel_id,
                    exc,
                )
        result = await evict_session_kv_cache(
            session_id=session_id,
            parent_session_id=session_id,
            agent=agent,
        )
        return result.ok
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] Plan session evict failed; preserving delete: "
            "session_id=%s error=%s",
            session_id,
            exc,
        )
        return False


def resolve_session_switch_context(
    *,
    target_session_id: str,
    previous_session_id: str,
    params: dict[str, Any],
) -> SessionSwitchContext:
    """Resolve switch facts without changing the product runtime."""
    from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
        is_kv_cache_affinity_enabled,
    )

    try:
        affinity_enabled = is_kv_cache_affinity_enabled()
    except Exception as exc:
        affinity_enabled = False
        logger.warning(
            "[ProductKVCacheHooks] affinity gate failed; KVC actions skipped: "
            "session_id=%s error=%s",
            target_session_id,
            exc,
        )

    target_mode_params = {"mode": params.get("mode"), "team": params.get("team")}
    previous_mode_params = {"mode": params.get("previous_mode")}
    target_metadata: dict[str, Any] = {}
    previous_metadata: dict[str, Any] = {}
    try:
        target_metadata = session_metadata.get_session_metadata(target_session_id)
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] target metadata unavailable; "
            "using request mode: session_id=%s error=%s",
            target_session_id,
            exc,
        )

    if (
        previous_session_id
        and previous_session_id not in {"new", target_session_id}
    ):
        try:
            previous_metadata = session_metadata.get_session_metadata(previous_session_id)
        except Exception as exc:
            logger.warning(
                "[ProductKVCacheHooks] previous metadata unavailable; "
                "using request mode: session_id=%s error=%s",
                previous_session_id,
                exc,
            )

    target_is_team = is_team_params(target_mode_params) or is_team_params(target_metadata)
    previous_is_team = (
        is_team_params(previous_mode_params) or is_team_params(previous_metadata)
    )

    resolved_mode = "team" if target_is_team else str(
        target_metadata.get("mode") or params.get("mode") or "agent.plan"
    )
    return SessionSwitchContext(
        target_is_team=target_is_team,
        previous_is_team=previous_is_team,
        resolved_mode=resolved_mode,
        affinity_enabled=affinity_enabled,
    )


async def dispatch_session_switch_signals(
    *,
    context: SessionSwitchContext,
    agent_manager: Any,
    channel_id: str,
    team_manager: Any,
    target_session_id: str,
    previous_session_id: str,
    reason: str,
    view_id: str = "default-view",
) -> None:
    """Record a real foreground transition without directly switching KVC."""
    if not context.affinity_enabled:
        return
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_task_guard import (
            get_session_kv_cache_task_guard,
        )

        guard = get_session_kv_cache_task_guard()
        if (
            previous_session_id
            and previous_session_id not in {"new", target_session_id}
        ):
            action = guard.set_foreground(
                session_id=previous_session_id,
                view_id=view_id,
                visible=False,
                channel_id=channel_id,
                is_team=context.previous_is_team,
                has_history=session_history.history_exists(previous_session_id),
            )
            _dispatch_guard_action(
                action,
                agent_manager=agent_manager,
                team_manager=team_manager,
            )

        guard.set_foreground(
            session_id=target_session_id,
            view_id=view_id,
            visible=True,
            channel_id=channel_id,
            is_team=context.target_is_team,
            has_history=session_history.history_exists(target_session_id),
        )
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] session foreground update failed; continuing: "
            "target_session_id=%s previous_session_id=%s error=%s",
            target_session_id,
            previous_session_id,
            exc,
        )


async def record_chat_started(
    *,
    session_id: str,
    params: dict[str, Any],
    channel_id: str,
    agent_manager: Any,
) -> None:
    """Record one top-level task start; never wait for prefetch/offload."""
    if not session_id:
        return
    from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
        is_kv_cache_affinity_enabled,
        wait_for_session_kv_cache_evict,
    )

    if not is_kv_cache_affinity_enabled():
        return
    # This is the only management-vs-inference barrier: destructive evict for
    # the same root Session.  Offload and prefetch are deliberately excluded.
    await wait_for_session_kv_cache_evict(session_id)
    is_team = _resolve_session_is_team(session_id, params)
    team_manager = _resolve_team_manager(channel_id) if is_team else None
    from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_task_guard import (
        get_session_kv_cache_task_guard,
    )

    action = get_session_kv_cache_task_guard().task_started(
        session_id=session_id,
        channel_id=channel_id,
        is_team=is_team,
        has_history=session_history.history_exists(session_id),
    )
    _dispatch_guard_action(
        action,
        agent_manager=agent_manager,
        team_manager=team_manager,
    )


def record_chat_finished(
    *,
    session_id: str,
    succeeded: bool,
    agent_manager: Any,
) -> None:
    """Record authoritative top-level completion and offload if background."""
    if not session_id:
        return
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
            is_kv_cache_affinity_enabled,
        )
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_task_guard import (
            get_session_kv_cache_task_guard,
        )

        if not is_kv_cache_affinity_enabled():
            return
        action = get_session_kv_cache_task_guard().task_finished(
            session_id=session_id,
            succeeded=succeeded,
        )
        team_manager = _resolve_team_manager(action.channel_id) if action and action.is_team else None
        _dispatch_guard_action(
            action,
            agent_manager=agent_manager,
            team_manager=team_manager,
        )
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] chat completion update failed: session_id=%s error=%s",
            session_id,
            exc,
        )


def record_session_prepare(
    *,
    session_id: str,
    intent_id: str,
    channel_id: str,
    params: dict[str, Any],
    agent_manager: Any,
) -> Literal["scheduled", "not_needed", "disabled", "failed"]:
    """Record typing intent and report whether it scheduled a prefetch."""
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
            is_kv_cache_affinity_enabled,
        )
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_task_guard import (
            get_session_kv_cache_task_guard,
        )

        if not is_kv_cache_affinity_enabled():
            return "disabled"
        is_team = _resolve_session_is_team(session_id, params)
        team_manager = _resolve_team_manager(channel_id) if is_team else None
        guard = get_session_kv_cache_task_guard()
        guard.set_foreground(
            session_id=session_id,
            view_id=str(params.get("view_id") or "default-view"),
            visible=True,
            channel_id=channel_id,
            is_team=is_team,
            has_history=session_history.history_exists(session_id),
        )
        action = guard.prepare(
            session_id=session_id,
            intent_id=intent_id,
            channel_id=channel_id,
            is_team=is_team,
            has_history=session_history.history_exists(session_id),
        )
        _dispatch_guard_action(
            action,
            agent_manager=agent_manager,
            team_manager=team_manager,
        )
        return "scheduled" if action is not None else "not_needed"
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] input intent update failed: session_id=%s error=%s",
            session_id,
            exc,
        )
        return "failed"


def mark_session_deleted(
    *,
    session_id: str,
    channel_id: str,
    is_team: bool,
) -> None:
    """Tombstone only in process memory; the existing delete owner runs evict."""
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
            is_kv_cache_affinity_enabled,
        )

        if not is_kv_cache_affinity_enabled():
            return
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_task_guard import (
            get_session_kv_cache_task_guard,
        )

        get_session_kv_cache_task_guard().delete(
            session_id=session_id,
            channel_id=channel_id,
            is_team=is_team,
        )
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] delete tombstone failed: session_id=%s error=%s",
            session_id,
            exc,
        )


def restore_session_after_failed_delete(session_id: str) -> None:
    """Restore KVC facts when the authoritative product delete did not commit."""
    try:
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
            is_kv_cache_affinity_enabled,
        )

        if not is_kv_cache_affinity_enabled():
            return
        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_task_guard import (
            get_session_kv_cache_task_guard,
        )

        get_session_kv_cache_task_guard().restore_after_failed_delete(session_id)
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] failed-delete rollback failed: "
            "session_id=%s error=%s",
            session_id,
            exc,
        )


def _resolve_session_is_team(session_id: str, params: dict[str, Any]) -> bool:
    metadata: dict[str, Any] = {}
    try:
        metadata = session_metadata.get_session_metadata(session_id)
    except Exception as exc:
        logger.warning(
            "[ProductKVCacheHooks] failed to resolve session metadata: "
            "session_id=%s error=%s",
            session_id,
            exc,
        )
    return is_team_params({"mode": params.get("mode"), "team": params.get("team")}) or is_team_params(metadata)


def _resolve_team_manager(channel_id: str) -> Any:
    from jiuwenswarm.agents.harness.team import get_team_manager

    return get_team_manager(channel_id)


def _dispatch_guard_action(
    action: Any,
    *,
    agent_manager: Any,
    team_manager: Any,
) -> None:
    if action is None or action.action not in {"offload", "prefetch"}:
        return
    if action.is_team:
        if team_manager is None:
            return

        async def _dispatch_team() -> None:
            method = getattr(team_manager, f"{action.action}_session_kv_cache")
            await method(action.session_id, reason=f"task-guard:{action.reason}: ")

        task = asyncio.create_task(
            _dispatch_team(),
            name=f"product-kvc-{action.action}[{action.session_id}]",
        )
        _PRODUCT_GUARD_TASKS.add(task)
        task.add_done_callback(_PRODUCT_GUARD_TASKS.discard)
        task.add_done_callback(_log_product_guard_task)
        return

    from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
        dispatch_offload_session_kv_cache,
        dispatch_prefetch_session_kv_cache,
    )

    agent = None
    try:
        agent = agent_manager.get_agent_nowait(action.channel_id)
    except Exception:
        # Root lifecycle falls back to the configured model.
        pass
    dispatch = (
        dispatch_offload_session_kv_cache
        if action.action == "offload"
        else dispatch_prefetch_session_kv_cache
    )
    dispatch(
        session_id=action.session_id,
        parent_session_id=action.session_id,
        agent=agent,
    )


def _log_product_guard_task(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("[ProductKVCacheHooks] Team task-guard action failed: %s", exc)
