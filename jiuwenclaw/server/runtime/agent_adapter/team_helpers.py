# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team agent streaming helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, AsyncIterator

from openjiuwen.agent_teams.runtime import RunActionKind
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.runner import Runner
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agents.harness.team import get_team_manager
from jiuwenclaw.server.runtime.session.session_metadata import (
    build_server_push_message,
    get_session_metadata,
    update_session_metadata,
)
from jiuwenclaw.agents.harness.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.server.utils.stream_utils import parse_stream_chunk
from jiuwenclaw.common.schema.agent import AgentResponseChunk

logger = logging.getLogger(__name__)

_pending_waiters: dict[tuple[str, str], list[tuple[str, asyncio.Queue]]] = {}
_TEAM_EVOLUTION_IDLE_SLEEP_SEC = 1.0
_TEAM_EVOLUTION_START_STAGE = "collecting"
_TEAM_EVOLUTION_START_MESSAGE = "Running team skill evolution analysis..."
_TEAM_CREATE_KINDS = {
    RunActionKind.CREATE.value,
    RunActionKind.NEW_TEAM_IN_SESSION.value,
}


def _sync_team_identity_metadata(
    *,
    channel_id: str | None,
    session_id: str,
    mode: str,
    ready_team_name: str,
    activation_kind: str | None,
) -> None:
    """Persist team identity only for newly created team sessions."""
    metadata = get_session_metadata(session_id)
    existing_team_name = str(metadata.get("team_name") or "").strip()
    normalized_kind = str(activation_kind or "").strip()
    if normalized_kind not in _TEAM_CREATE_KINDS:
        return

    if existing_team_name and existing_team_name != ready_team_name:
        logger.warning(
            "[TeamHelpers] team session identity mismatch, keep existing metadata: "
            "session_id=%s existing_team_name=%s new_team_name=%s activation_kind=%s",
            session_id,
            existing_team_name,
            ready_team_name,
            normalized_kind,
        )
        return

    update_session_metadata(
        session_id=session_id,
        channel_id=_resolve_channel_id(channel_id),
        mode=mode,
        team_name=ready_team_name,
    )


@dataclass(frozen=True)
class _TeamEvolutionPushContext:
    transport: Any
    channel_id: str | None
    session_id: str


@dataclass(frozen=True)
class _TeamEvolutionStatusUpdate:
    request_id: str
    status: str
    stage: str
    message: str = ""


def _resolve_channel_id(channel_id: str | None) -> str:
    return str(channel_id or "default").strip() or "default"


def _waiter_key(channel_id: str | None, session_id: str) -> tuple[str, str]:
    return _resolve_channel_id(channel_id), session_id


async def _ensure_monitor_for_active_runtime(
    channel_id: str | None,
    session_id: str,
    team_name: str,
) -> None:
    """Attach TeamMonitorHandler using the public Runner team monitor accessor."""
    tm = get_team_manager(channel_id)
    existing = tm.get_monitor(session_id)
    if existing is not None and existing.is_running:
        return

    monitor = await Runner.get_agent_team_monitor(team_name=team_name, session_id=session_id)
    if monitor is None:
        logger.warning(
            "[TeamHelpers] active team monitor unavailable: channel_id=%s session_id=%s team_name=%s",
            _resolve_channel_id(channel_id),
            session_id,
            team_name,
        )
        return

    monitor_handler = TeamMonitorHandler(monitor, session_id)
    try:
        await monitor_handler.start()
        tm.register_monitor(session_id, monitor_handler)
        logger.info(
            "[TeamHelpers] Monitor started from public runner API: channel_id=%s session_id=%s team_name=%s",
            _resolve_channel_id(channel_id),
            session_id,
            team_name,
        )
        if monitor_handler.is_running:
            asyncio.create_task(
                _consume_monitor_events(
                    channel_id,
                    session_id,
                    monitor_handler,
                )
            )
    except Exception as exc:
        logger.warning("[TeamHelpers] Monitor start failed from public runner API: %s", exc)


def _broadcast_event(
    channel_id: str | None, session_id: str, event: dict[str, Any]
) -> None:
    """Broadcast an event to all request queues waiting on the same channel/session."""
    waiter_key = _waiter_key(channel_id, session_id)
    waiters = _pending_waiters.get(waiter_key, [])
    for request_id, queue in waiters:
        try:
            queue.put_nowait(dict(event))
        except Exception:
            logger.debug(
                "[TeamHelpers] broadcast failed: channel_id=%s session_id=%s request_id=%s",
                waiter_key[0],
                session_id,
                request_id,
            )


def _event_payload_dict(evt: Any) -> dict[str, Any]:
    if hasattr(evt, "payload") and isinstance(evt.payload, dict):
        return dict(evt.payload)
    if isinstance(evt, dict):
        return dict(evt)
    return {}


def _event_type(evt: Any) -> str:
    evt_type = getattr(evt, "type", None)
    if isinstance(evt_type, str) and evt_type:
        return evt_type
    payload = _event_payload_dict(evt)
    payload_type = payload.get("event_type")
    return payload_type if isinstance(payload_type, str) else ""


def _is_leader_output(chunk: Any) -> bool:
    """Return whether a team OutputSchema chunk should be shown to claw users."""
    chunk_type = getattr(chunk, "type", None)
    if chunk_type == "team.runtime_ready":
        return True

    role = getattr(chunk, "role", None)
    if role is None:
        return True
    if role == TeamRole.LEADER:
        return True

    role_value = getattr(role, "value", role)
    return str(role_value).strip().lower() == TeamRole.LEADER.value


def _is_team_evolution_approval(evt: Any) -> bool:
    return _event_type(evt) == "chat.ask_user_question"


def _extract_team_evolution_request_id(evt: Any) -> str | None:
    payload = _event_payload_dict(evt)
    request_id = payload.get("request_id")
    if isinstance(request_id, str):
        request_id = request_id.strip()
    return request_id or None


def _team_rail_has_pending_evolution(rail: Any) -> bool:
    checker = getattr(rail, "has_pending_evolution_tasks", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception as exc:
            logger.debug("[TeamHelpers] rail pending-evolution check failed: %s", exc)
    in_progress = getattr(rail, "_evolution_in_progress", None)
    if in_progress is not None:
        try:
            return bool(in_progress)
        except Exception as exc:
            logger.debug("[TeamHelpers] rail evolution-in-progress check failed: %s", exc)
    bg_tasks = getattr(rail, "_bg_tasks", None)
    if not isinstance(bg_tasks, set):
        return False
    return any(not task.done() for task in bg_tasks)


async def _broadcast_team_evolution_progress(
    channel_id: str | None,
    session_id: str,
    events: list[Any],
) -> None:
    for evt in events:
        if _is_team_evolution_approval(evt):
            continue
        parsed = parse_stream_chunk(evt)
        if parsed is not None:
            _broadcast_event(channel_id, session_id, parsed)


def _group_team_evolution_approvals(
    session_id: str,
    events: list[Any],
) -> tuple[dict[str, list[Any]], list[str]]:
    grouped: dict[str, list[Any]] = {}
    fallback_request_ids: list[str] = []
    for index, evt in enumerate(events, start=1):
        if not _is_team_evolution_approval(evt):
            continue
        request_id = _extract_team_evolution_request_id(evt)
        if request_id is None:
            request_id = f"team_evolve_{session_id}_{index}"
            fallback_request_ids.append(request_id)
            logger.warning(
                "[TeamHelpers] team evolution approval missing request_id: session_id=%s fallback=%s",
                session_id,
                request_id,
            )
        grouped.setdefault(request_id, []).append(evt)
    return grouped, fallback_request_ids


def _ensure_team_evolution_watcher(
    channel_id: str | None,
    session_id: str,
) -> None:
    """Launch the per-session team evolution monitor once the team session is ready."""
    tm = get_team_manager(channel_id)
    watcher = tm.get_team_evolution_watcher(session_id)
    if watcher is not None and not watcher.done():
        return

    rail = tm.get_team_skill_rail(session_id)
    if rail is None:
        logger.warning(
            "[TeamHelpers] no TeamSkillRail found, evolution watcher not launched: session_id=%s",
            session_id,
        )
        return

    logger.info(
        "[TeamHelpers] launching evolution monitor: channel_id=%s session_id=%s",
        channel_id,
        session_id,
    )
    task = asyncio.create_task(
        _watch_team_evolution_and_push(channel_id, session_id, rail)
    )
    setattr(task, "_team_channel_id", channel_id)
    setattr(task, "_team_session_id", session_id)
    task.add_done_callback(_on_team_watcher_done)
    tm.register_team_evolution_watcher(session_id, task)


async def _resolve_team_rebuild_followup(
    channel_id: str | None,
    session_id: str,
    query: str,
) -> tuple[str | None, str | None]:
    """Resolve /evolve_rebuild into a followup prompt for the team session."""
    stripped = str(query or "").strip()
    if not stripped.startswith("/evolve_rebuild"):
        return None, None

    tm = get_team_manager(channel_id)
    rail = tm.get_team_skill_rail(session_id)
    if rail is None:
        return None, "团队技能重建不可用：未找到 TeamSkillRail。"

    store = rail.store
    parts = stripped.split(maxsplit=2)
    skill_name = parts[1] if len(parts) > 1 else ""
    user_intent = parts[2] if len(parts) > 2 else None

    if not skill_name:
        return None, "请指定 Skill 名称：`/evolve_rebuild <skill_name> [user_intent]`"

    if not store.skill_exists(skill_name):
        available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
        return None, f"未找到 Skill '{skill_name}'。当前可用：{available}"

    try:
        followup_prompt = await rail.request_rebuild(skill_name, user_intent)
    except Exception as exc:
        logger.warning("[TeamHelpers] evolve_rebuild failed: session_id=%s error=%s", session_id, exc)
        return None, f"团队技能重建分析失败：{exc}"

    if not followup_prompt:
        return None, f"Skill '{skill_name}' 未生成可执行的重建指令。"

    return followup_prompt, None


async def _handle_team_evolve_list_command(
    channel_id: str | None,
    session_id: str,
    query: str,
) -> dict[str, Any] | None:
    """Handle /evolve_list directly against the team skill store."""
    stripped = str(query or "").strip()
    if not stripped.startswith("/evolve_list"):
        return None

    tm = get_team_manager(channel_id)
    rail = tm.get_team_skill_rail(session_id)
    if rail is None:
        return {
            "output": "团队技能演进记录不可用：未找到 TeamSkillRail。",
            "result_type": "error",
        }

    store = rail.store
    parts = stripped.split()
    skill_name = parts[1] if len(parts) > 1 else ""
    if not skill_name or skill_name.startswith("--"):
        return {
            "output": "请指定 Skill 名称：`/evolve_list <skill_name>`",
            "result_type": "error",
        }

    if not store.skill_exists(skill_name):
        available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
        return {
            "output": f"未找到 Skill '{skill_name}'。当前可用：{available}",
            "result_type": "error",
        }

    records = await store.get_records_by_score(skill_name)
    if not records:
        return {
            "output": f"Skill '{skill_name}' 暂无演进经验。",
            "result_type": "answer",
        }

    avg_score = sum(r.score for r in records) / len(records)
    lines = [
        f'📊 Skill "{skill_name}" — 经验库摘要\n',
        f"共 {len(records)} 条经验 | 平均分：{avg_score:.2f}\n",
        "| # | Score | Used | Effect | Section | Content (preview) |",
        "|---|---:|---|---|---|---|",
    ]
    for i, record in enumerate(records, 1):
        stats = record.usage_stats
        if stats:
            used_str = (
                f"{stats.times_used}/{stats.times_presented}"
                if stats.times_presented
                else "0/0"
            )
            effect_str = f"+{stats.times_positive}/-{stats.times_negative}"
        else:
            used_str = "0/0"
            effect_str = "+0/-0"
        section = str(record.change.section).replace("|", "\\|")
        preview = record.change.content.split("\n")[0][:40].replace("|", "\\|")
        lines.append(
            f"| {i} | {record.score:.2f} | {used_str} | {effect_str} | {section} | {preview} |"
        )

    lines.append(f"\n提示：使用 /evolve_simplify {skill_name} 执行智能整理")
    return {
        "output": "\n".join(lines),
        "result_type": "answer",
    }


async def _handle_team_slash_command(
    channel_id: str | None,
    session_id: str,
    query: str,
) -> dict[str, Any] | None:
    """Handle team-only slash commands before entering the team stream."""
    evolve_list_result = await _handle_team_evolve_list_command(channel_id, session_id, query)
    if evolve_list_result is not None:
        return evolve_list_result

    stripped = str(query or "").strip()
    if not (
        stripped.startswith("/evolve_simplify")
        or stripped == "/evolve"
        or stripped.startswith("/evolve ")
    ):
        return None

    tm = get_team_manager(channel_id)
    rail = tm.get_team_skill_rail(session_id)
    if rail is None:
        return {
            "output": "团队技能演进不可用：未找到 TeamSkillRail。",
            "result_type": "error",
        }

    store = rail.store

    if stripped.startswith("/evolve_simplify"):
        parts = stripped.split(maxsplit=2)
        skill_name = parts[1] if len(parts) > 1 else ""
        user_intent = parts[2] if len(parts) > 2 else None

        if not skill_name:
            return {
                "output": "请指定 Skill 名称：`/evolve_simplify <skill_name> [user_intent]`",
                "result_type": "error",
            }

        if not store.skill_exists(skill_name):
            available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
            return {
                "output": f"未找到 Skill '{skill_name}'。当前可用：{available}",
                "result_type": "error",
            }

        try:
            simplify_result = await rail.request_simplify(skill_name, user_intent)
        except Exception as exc:
            logger.warning(
                "[TeamHelpers] evolve_simplify failed: session_id=%s error=%s",
                session_id,
                exc,
            )
            return {
                "output": f"团队技能整理分析失败：{exc}",
                "result_type": "error",
            }

        if not simplify_result:
            return {
                "output": f"Skill '{skill_name}' 经验库状态良好，无需整理。",
                "result_type": "answer",
            }

        if isinstance(simplify_result, str):
            output = simplify_result
        elif isinstance(simplify_result, dict):
            ordered_keys = ("archived", "retained", "merged", "removed", "updated")
            parts = [f"{key}={simplify_result[key]}" for key in ordered_keys if key in simplify_result]
            output = (
                f"Skill '{skill_name}' 整理完成：{', '.join(parts)}"
                if parts
                else f"Skill '{skill_name}' 整理完成。"
            )
        else:
            output = f"Skill '{skill_name}' 整理完成。"

        return {
            "output": output,
            "result_type": "answer",
        }

    parts = stripped.split(maxsplit=2)
    if len(parts) < 2:
        return {
            "output": "请补充演进意图：`/evolve <skill_name> <user_query>`",
            "result_type": "error",
        }

    skill_name = parts[1].strip()
    user_query = parts[2].strip() if len(parts) > 2 else ""

    if not store.skill_exists(skill_name):
        available = "、".join(store.list_skill_names()) or "（无可用 Skill）"
        return {
            "output": f"未找到 Skill '{skill_name}'。当前可用：{available}",
            "result_type": "error",
        }

    if not user_query:
        return {
            "output": "请补充演进意图：`/evolve <skill_name> <user_query>`",
            "result_type": "error",
        }

    try:
        # Slash-command evolve returns before the normal team stream loop starts.
        # Launch the per-session watcher first so approval events still reach
        # session-bound clients such as TUI via the existing push path.
        _ensure_team_evolution_watcher(channel_id, session_id)
        await rail.request_user_evolution(skill_name, user_query)
    except Exception as exc:
        logger.warning(
            "[TeamHelpers] evolve failed: session_id=%s error=%s",
            session_id,
            exc,
        )
        return {
            "output": f"团队技能演进请求失败：{exc}",
            "result_type": "error",
        }

    return {
        "output": f"Skill '{skill_name}' 演进请求已提交，请等待审批。",
        "result_type": "answer",
    }


async def process_team_message_stream(
    request: Any,
    inputs: dict[str, Any],
    deep_agent: DeepAgent,
) -> AsyncIterator[AgentResponseChunk]:
    """Process a team-mode streaming request."""
    session_id = request.session_id or "default"
    rid = request.request_id
    channel_id = request.channel_id

    team_manager = get_team_manager(channel_id)
    query = inputs.get("query", "")
    is_first_request = not team_manager.has_stream_task(session_id)
    request_queue: asyncio.Queue | None = None

    slash_result = await _handle_team_slash_command(
        channel_id,
        session_id,
        str(query or ""),
    )
    if slash_result is not None:
        result_type = str(slash_result.get("result_type", "answer")).strip().lower()
        content = str(slash_result.get("output", ""))
        payload = (
            {"event_type": "chat.error", "error": content}
            if result_type == "error"
            else {"event_type": "chat.final", "content": content}
        )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload=payload,
            is_complete=False,
        )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload=None,
            is_complete=True,
        )
        return

    try:
        if deep_agent is None:
            raise RuntimeError("DeepAgent not initialized")
        team_spec = await team_manager.get_enriched_team_spec(
            session_id=session_id,
            deep_agent=deep_agent,
            request_id=rid,
            channel_id=channel_id,
            request_metadata=request.metadata,
        )
    except Exception as exc:
        logger.exception("[TeamHelpers] TeamAgent create failed: %s", exc)
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload={"event_type": "chat.error", "error": str(exc)},
            is_complete=False,
        )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload=None,
            is_complete=True,
        )
        return

    team_name = team_spec.team_name

    followup_prompt, rebuild_error = await _resolve_team_rebuild_followup(
        channel_id,
        session_id,
        str(query or ""),
    )
    if rebuild_error is not None:
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload={"event_type": "chat.error", "error": rebuild_error},
            is_complete=False,
        )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload=None,
            is_complete=True,
        )
        return
    if followup_prompt is not None:
        query = followup_prompt

    _ensure_team_evolution_watcher(channel_id, session_id)

    try:
        if is_first_request:
            team_manager.ensure_team_shared_skills_initialized(team_spec)
            await team_manager.prepare_runtime_activation(session_id, team_name)
            request_queue = asyncio.Queue()
            waiter_key = _waiter_key(channel_id, session_id)
            if waiter_key not in _pending_waiters:
                _pending_waiters[waiter_key] = []
            _pending_waiters[waiter_key].append((rid, request_queue))
            logger.info(
                "[TeamHelpers] first team request: channel_id=%s session_id=%s",
                waiter_key[0],
                session_id,
            )

            stream_task = asyncio.create_task(
                _consume_stream_with_query(
                    channel_id,
                    session_id,
                    team_spec,
                    query,
                )
            )
            team_manager.register_stream_task(session_id, stream_task)
        else:
            logger.info(
                "[TeamHelpers] follow-up team request: channel_id=%s session_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
            )
            if query:
                success = await team_manager.interact(session_id, query)
                if not success:
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=channel_id,
                        payload={
                            "event_type": "chat.error",
                            "error": "interact failed",
                        },
                        is_complete=False,
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=channel_id,
                        payload=None,
                        is_complete=True,
                    )
                    return

            logger.info(
                "[TeamHelpers] follow-up request submitted without waiter: channel_id=%s session_id=%s request_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
                rid,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=channel_id,
                payload=None,
                is_complete=True,
            )
            return

        try:
            while team_manager.has_stream_task(session_id):
                if request_queue is None:
                    break
                try:
                    event = await asyncio.wait_for(request_queue.get(), timeout=0.1)
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=channel_id,
                        payload=event,
                        is_complete=False,
                    )
                    if (
                        isinstance(event, dict)
                        and event.get("event_type") == "team.error"
                    ):
                        break
                except asyncio.TimeoutError:
                    if not team_manager.has_stream_task(session_id):
                        break
                    continue
        except asyncio.CancelledError:
            logger.info(
                "[TeamHelpers] event stream cancelled: channel_id=%s session_id=%s request_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
                rid,
            )
            raise
        except Exception as exc:
            logger.exception(
                "[TeamHelpers] event stream failed: channel_id=%s session_id=%s error=%s",
                _resolve_channel_id(channel_id),
                session_id,
                exc,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=channel_id,
                payload={"event_type": "chat.error", "error": str(exc)},
                is_complete=False,
            )

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload=None,
            is_complete=True,
        )
    finally:
        if request_queue is not None:
            waiter_key = _waiter_key(channel_id, session_id)
            waiters = _pending_waiters.get(waiter_key, [])
            _pending_waiters[waiter_key] = [
                (req_id, queue) for req_id, queue in waiters if req_id != rid
            ]
            if not _pending_waiters.get(waiter_key, []):
                _pending_waiters.pop(waiter_key, None)
                logger.info(
                    "[TeamHelpers] cleared waiter set: channel_id=%s session_id=%s",
                    waiter_key[0],
                    session_id,
                )


async def _consume_stream_with_query(
    channel_id: str | None,
    session_id: str,
    team_spec: Any,
    initial_query: str,
) -> None:
    """Consume the team stream in the background and broadcast parsed events."""
    received_chunks = 0
    try:
        logger.info(
            "[TeamHelpers] stream started: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=team_spec,
            inputs={"query": initial_query},
            session=session_id,
        ):
            received_chunks += 1
            if not _is_leader_output(chunk):
                continue
            parsed = parse_stream_chunk(chunk)
            if parsed is not None:
                if parsed.get("event_type") == "team.runtime_ready":
                    ready_team_name = str(parsed.get("team_name") or team_spec.team_name)
                    activation_kind = str(parsed.get("activation_kind") or "").strip()
                    _sync_team_identity_metadata(
                        channel_id=channel_id,
                        session_id=session_id,
                        mode="team",
                        ready_team_name=ready_team_name,
                        activation_kind=activation_kind,
                    )
                    tm = get_team_manager(channel_id)
                    tm.commit_runtime_ready(session_id, ready_team_name)
                    await tm.attach_distributed_hooks_for_runner_runtime(
                        team_name=ready_team_name,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    await _ensure_monitor_for_active_runtime(
                        channel_id,
                        session_id,
                        ready_team_name,
                    )
                _broadcast_event(channel_id, session_id, parsed)

        # If stream ended without any chunks, broadcast an error event
        if received_chunks == 0:
            logger.warning(
                "[TeamHelpers] stream ended with no output: channel_id=%s session_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
            )
            _broadcast_event(
                channel_id,
                session_id,
                {
                    "event_type": "team.error",
                    "error": "Team stream ended with no output (possible pool/DB inconsistency or internal error)",
                    "session_id": session_id,
                },
            )
        else:
            logger.warning(
                "[TeamHelpers] stream ended unexpectedly: channel_id=%s session_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
            )
    except asyncio.CancelledError:
        logger.info(
            "[TeamHelpers] stream cancelled: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        raise
    except Exception as exc:
        logger.error(
            "[TeamHelpers] stream failed: channel_id=%s session_id=%s error=%s",
            _resolve_channel_id(channel_id),
            session_id,
            exc,
        )
        _broadcast_event(
            channel_id,
            session_id,
            {
                "event_type": "team.error",
                "error": str(exc),
                "session_id": session_id,
            },
        )
    finally:
        get_team_manager(channel_id).clear_pending_runtime(session_id)
        get_team_manager(channel_id).pop_stream_task(session_id)


async def _consume_monitor_events(
    channel_id: str | None,
    session_id: str,
    monitor_handler: TeamMonitorHandler,
) -> None:
    """Consume monitor events in the background and broadcast them."""
    try:
        logger.info(
            "[TeamHelpers] monitor event loop started: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        async for event in monitor_handler.events():
            _broadcast_event(channel_id, session_id, event)

        logger.info(
            "[TeamHelpers] monitor event loop ended: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
    except asyncio.CancelledError:
        logger.info(
            "[TeamHelpers] monitor event loop cancelled: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        raise
    except Exception as exc:
        logger.error(
            "[TeamHelpers] monitor event loop failed: channel_id=%s session_id=%s error=%s",
            _resolve_channel_id(channel_id),
            session_id,
            exc,
        )


def _on_team_watcher_done(task: asyncio.Task) -> None:
    """Callback when a team evolution monitor task completes."""
    channel_id = getattr(task, "_team_channel_id", None)
    session_id = getattr(task, "_team_session_id", None)
    if isinstance(session_id, str):
        get_team_manager(channel_id).pop_team_evolution_watcher(session_id)

    if task.cancelled():
        return

    exc = task.exception()
    if exc is not None:
        logger.warning("[TeamHelpers] evolution monitor task exception: %s", exc)


async def _push_team_evolution_status(
    push_context: _TeamEvolutionPushContext,
    status_update: _TeamEvolutionStatusUpdate,
) -> None:
    await push_context.transport.send_push(
        build_server_push_message(
            session_id=push_context.session_id,
            request_id=status_update.request_id,
            fallback_channel_id=push_context.channel_id,
            payload={
                "event_type": "chat.evolution_status",
                "request_id": status_update.request_id,
                "status": status_update.status,
                "stage": status_update.stage,
                "message": status_update.message,
            },
        )
    )


async def _push_team_evolution_event(
    push_context: _TeamEvolutionPushContext,
    request_id: str,
    evt: Any,
) -> None:
    payload = _event_payload_dict(evt)
    evt_type = _event_type(evt)
    if evt_type and "event_type" not in payload:
        payload["event_type"] = evt_type
    payload.setdefault("request_id", request_id)
    await push_context.transport.send_push(
        build_server_push_message(
            session_id=push_context.session_id,
            request_id=request_id,
            fallback_channel_id=push_context.channel_id,
            payload=payload,
        )
    )


def _make_team_evolution_cycle_request_id(session_id: str, cycle_index: int) -> str:
    return f"team_evolve_{session_id}_{cycle_index}"


def _build_team_evolution_status_update(
    request_id: str,
    status: str,
    stage: str,
    message: str = "",
) -> _TeamEvolutionStatusUpdate:
    return _TeamEvolutionStatusUpdate(
        request_id=request_id,
        status=status,
        stage=stage,
        message=message,
    )


def _should_finish_active_cycle(
    active_cycle_request_id: str | None,
    still_running_after_drain: bool,
    wait_for_completion: bool,
    events: list[Any],
    outcomes: list[dict[str, str]],
) -> bool:
    has_evolution_activity = wait_for_completion or bool(events) or bool(outcomes)
    return (
        active_cycle_request_id is not None
        and not still_running_after_drain
        and has_evolution_activity
    )


async def _watch_team_evolution_and_push(
    channel_id: str | None,
    session_id: str,
    rail: Any,
) -> None:
    """Monitor TeamSkillRail and push stable status/approval events for every evolution cycle."""
    from jiuwenclaw.server.gateway_push import WebSocketGatewayPushTransport

    push_context = _TeamEvolutionPushContext(
        transport=WebSocketGatewayPushTransport(),
        channel_id=channel_id,
        session_id=session_id,
    )
    seen_request_ids: set[str] = set()
    fallback_cycle_index = 0
    active_cycle_request_id: str | None = None
    active_cycle_request_is_provisional = False

    try:
        while True:
            wait_for_completion = _team_rail_has_pending_evolution(rail)
            if wait_for_completion and active_cycle_request_id is None:
                fallback_cycle_index += 1
                active_cycle_request_id = _make_team_evolution_cycle_request_id(
                    session_id,
                    fallback_cycle_index,
                )
                active_cycle_request_is_provisional = True
                await _push_team_evolution_status(
                    push_context,
                    _build_team_evolution_status_update(
                        request_id=active_cycle_request_id,
                        status="start",
                        stage=_TEAM_EVOLUTION_START_STAGE,
                        message=_TEAM_EVOLUTION_START_MESSAGE,
                    ),
                )
            events = await rail.drain_pending_approval_events(wait=wait_for_completion)
            still_running_after_drain = _team_rail_has_pending_evolution(rail)
            outcomes = rail.drain_evolution_outcomes()

            if events:
                await _broadcast_team_evolution_progress(channel_id, session_id, events)

            grouped_approvals, _ = _group_team_evolution_approvals(session_id, events)

            for request_id, approval_events in grouped_approvals.items():
                if request_id in seen_request_ids:
                    logger.debug(
                        "[TeamHelpers] skip duplicated team evolution approval batch: session_id=%s request_id=%s",
                        session_id,
                        request_id,
                    )
                    continue
                seen_request_ids.add(request_id)
                if active_cycle_request_id is None:
                    active_cycle_request_id = request_id
                    active_cycle_request_is_provisional = False
                    await _push_team_evolution_status(
                        push_context,
                        _build_team_evolution_status_update(
                            request_id=active_cycle_request_id,
                            status="start",
                            stage=_TEAM_EVOLUTION_START_STAGE,
                            message=_TEAM_EVOLUTION_START_MESSAGE,
                        ),
                    )
                elif active_cycle_request_is_provisional and active_cycle_request_id != request_id:
                    active_cycle_request_id = request_id
                    active_cycle_request_is_provisional = False
                    await _push_team_evolution_status(
                        push_context,
                        _build_team_evolution_status_update(
                            request_id=active_cycle_request_id,
                            status="start",
                            stage=_TEAM_EVOLUTION_START_STAGE,
                            message=_TEAM_EVOLUTION_START_MESSAGE,
                        ),
                    )
                for evt in approval_events:
                    try:
                        await _push_team_evolution_event(
                            push_context,
                            request_id,
                            evt,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[TeamHelpers] push approval failed for request_id=%s event_type=%s error=%s",
                            request_id,
                            _event_type(evt) or "unknown",
                            exc,
                        )
            if _should_finish_active_cycle(
                active_cycle_request_id,
                still_running_after_drain,
                wait_for_completion,
                events,
                outcomes,
            ):
                outcome = outcomes[-1] if outcomes else None
                end_stage = "completed"
                end_message = "Team skill evolution analysis completed"
                if outcome is not None:
                    end_stage = (
                        str(outcome.get("status") or "failed").strip().lower()
                        or "failed"
                    )
                    end_message = str(
                        outcome.get("message") or "Team skill evolution analysis failed"
                    )
                await _push_team_evolution_status(
                    push_context,
                    _build_team_evolution_status_update(
                        request_id=active_cycle_request_id,
                        status="end",
                        stage=end_stage,
                        message=end_message,
                    ),
                )
                active_cycle_request_id = None
                active_cycle_request_is_provisional = False

            if not events and not outcomes:
                await asyncio.sleep(_TEAM_EVOLUTION_IDLE_SLEEP_SEC)
    except Exception as exc:
        logger.warning("[TeamHelpers] evolution monitor failed: %s", exc)
        try:
            if active_cycle_request_id is None:
                fallback_cycle_index += 1
                active_cycle_request_id = _make_team_evolution_cycle_request_id(
                    session_id,
                    fallback_cycle_index,
                )
                active_cycle_request_is_provisional = True
            await _push_team_evolution_status(
                push_context,
                _build_team_evolution_status_update(
                    request_id=active_cycle_request_id,
                    status="end",
                    stage="failed",
                    message=f"团队技能演进分析失败: {exc}",
                ),
            )
        except Exception as push_exc:
            logger.warning("[TeamHelpers] push status notification failed: %s", push_exc)
