# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team agent streaming helpers."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import get_agent_teams_home, independent_member_workspace, team_home
from openjiuwen.agent_teams.runtime import RunActionKind
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.monitor import TeamStreamLogger
from openjiuwen.core.runner import Runner
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.team import TeamManager, get_team_manager
from jiuwenclaw.agentserver.team.handlers.workflow_monitor_handler import WorkflowMonitorHandler
from jiuwenclaw.agentserver.team.handlers.workflow_state import WorkflowRunState
from jiuwenclaw.agentserver.session_metadata import get_session_metadata, update_session_metadata
from jiuwenclaw.agentserver.session_history import append_history_record
from jiuwenclaw.agentserver.team.handlers.team_monitor_handler import TeamMonitorHandler
from jiuwenclaw.agentserver.stream_utils import parse_stream_chunk
from jiuwenclaw.schema.agent import AgentResponseChunk

logger = logging.getLogger(__name__)
DEBUG_PREFIX = '/debug'


def strip_slash_directive(query: str, prefix: str) -> tuple[str, bool]:
    if not isinstance(query, str):
        return (query, False)
    stripped = query.lstrip()
    if not stripped.startswith(prefix):
        return (query, False)
    remainder = stripped[len(prefix):]
    if remainder and (not remainder[0].isspace()):
        return (query, False)
    return (remainder.lstrip(), True)


def increment_session_round_count(session_id: str) -> int:
    from jiuwenclaw.agentserver.session_metadata import _current_timestamp, _enqueue_write, _read_metadata
    metadata = _read_metadata(session_id)
    current_round = int(metadata.get('round_id', 0))
    new_round = current_round + 1
    metadata['round_id'] = new_round
    metadata['last_message_at'] = _current_timestamp()
    _enqueue_write(session_id, metadata)
    return new_round


_WORKFLOW_RUNS_STATE_KEY = 'workflow_runs'
_TEAM_CREATE_KINDS = {RunActionKind.CREATE.value, RunActionKind.NEW_TEAM_IN_SESSION.value}
_HIDE_DM_PREFIX = '/hide_dm'
_STREAM_TRACE_ENV_KEY = 'JIUWENSWARM_TEAM_STREAM_TRACE'
_HIDE_TEAMMATE_ENV_KEY = 'JIUWENSWARM_TEAM_HIDE_TEAMMATE'
_FOLLOWUP_INTERACT_BOUNDARY_TIMEOUT_SEC = 10.0
_FOLLOWUP_INTERACT_POLL_INTERVAL_SEC = 0.05


def _safe_team_path_segment(value: str, fallback: str = '_') -> str:
    """Sanitize a value into one path segment for team workspace paths."""
    normalized = re.sub('[^A-Za-z0-9_.-]+', '_', str(value or '').strip())
    normalized = normalized.strip('._-')
    return normalized[:96] or fallback


def _team_hide_teammate_enabled() -> bool:
    """Return whether non-leader teammate frames should be filtered out in team mode."""
    return os.environ.get(_HIDE_TEAMMATE_ENV_KEY, '').strip().lower() == 'true'


_INTERACT_REASON_ERROR_MAP: dict[str, str] = {
    'not_active': 'Team is initializing, please try again later',
    'session_mismatch': 'Session state mismatch, please refresh and retry',
    'gate_closed': 'Team is shutting down, please try again later',
    'unknown_human_agent': 'Member not found, please check the name',
    'human_agent_not_enabled': (
        'Human agent is not yet available, please try again later'
    ),
    'no_team_backend': 'Team backend not ready, please try again later',
    'agent_unavailable': (
        'Target member not available, please check the member name'
    ),
}


def _tgt_godview() -> dict:
    return {'intent': 'godview'}


def _tgt_mention(member_names, *, mention_all: bool = False, speaker: str | None = None) -> dict:
    """mention intent：投递给被点名成员并带 @（飞书 <at>）。"""
    tgt: dict = {'intent': 'mention', 'member_names': list(member_names), 'speaker': speaker}
    if mention_all:
        tgt['mention_all'] = True
    return tgt


def _tgt_private(member_names, *, speaker: str | None = None) -> dict:
    """private intent：投递给被点名成员但不带 @。"""
    return {'intent': 'private', 'member_names': list(member_names), 'speaker': speaker}


def _p2p_fanout(inner: dict) -> list[dict]:
    """P2P 消息 fan_out：godview + 收件人(mention, 带 @) + 发送方(private, 不带 @)。

    - 收件人用 mention：被 @ 提醒，飞书渲染 <at>。
    - 发送方用 private：自己发的消息不该 @ 自己（private intent 在
      ``_build_routing_target`` 里不注入 mention_member_ids），零打扰，
      仅用于发送方在自己的 /join 窗口看到自己发出的 P2P 卡片。
    - from_member 缺失时不追加 private([None])，避免把 None 当 member_name
      查 Registry 留下调试噪音（见 dispatch_to_session 的 lookup_member）。
    - from/to 落同一物理容器（飞书群、同一 ws）时，dispatch_to_session 的
      sent_containers 跨 intent 去重，先到的 intent 标记容器已发，后到跳过，
      至少显示一次，不会双发。
    """
    targets = [_tgt_godview(), _tgt_mention([inner['to_member']], speaker=inner.get('from_member'))]
    fm = inner.get('from_member')
    if fm:
        targets.append(_tgt_private([fm], speaker=fm))
    return targets


_INNER_TYPE_FANOUT: dict[str, Any] = {'team.message.p2p': _p2p_fanout, 'team.message.broadcast': lambda inner: [
    _tgt_godview(), _tgt_mention([], mention_all=True, speaker=inner.get('from_member'))]}
_ROLE_FANOUT: dict[str, Any] = {'teammate': lambda ev: [
    _tgt_godview(), _tgt_private([ev['member_name']], speaker=ev['member_name'])]}
_GODVIEW_TARGET = [_tgt_godview()]


def _build_logical_targets(event: dict) -> list[dict]:
    """所有 team 事件 → fan_out 规则（表驱动，依次查两维后兜底 godview）。

    查询顺序（命中即返回）：
      1. event.event.type ∈ _INNER_TYPE_FANOUT  —— team.message.p2p/broadcast
      2. event.role ∈ _ROLE_FANOUT              —— teammate 输出 → private
      3. 兜底 → [godview]                        —— leader 输出、team.member、team.task 等

    p2p 用 mention（带 @），teammate 输出用 private（不带 @），broadcast 用 mention_all。
    """
    if event.get('event_type') == 'team.message':
        inner = event.get('event', {}) or {}
        fn = _INNER_TYPE_FANOUT.get(inner.get('type', ''))
        if fn:
            return fn(inner)
    role = str(event.get('role', '')).strip().lower()
    fn = _ROLE_FANOUT.get(role)
    if fn:
        member_name = str(event.get('member_name', '')).strip()
        if member_name:
            return fn(event)
    return _GODVIEW_TARGET


def _is_followup_delivery_boundary_reason(reason: str | None) -> bool:
    """Return whether follow-up delivery likely hit a runtime boundary."""
    normalized = str(reason or '')
    if normalized in {'gate_closed', 'not_active'}:
        return True
    return normalized.startswith('deliver_to_leader_failed:')


@dataclass(slots=True)
class _FollowupInteractBoundaryResult:
    """Result of delivering a follow-up across a runtime boundary."""
    success: bool
    reason: str | None
    first_request_ready: bool


async def _deliver_followup_interact_across_boundary(
        team_manager: Any,
        session_id: str,
        query: Any,
        *,
        initial_reason: str | None = None,
        timeout_sec: float = _FOLLOWUP_INTERACT_BOUNDARY_TIMEOUT_SEC,
        poll_interval_sec: float = _FOLLOWUP_INTERACT_POLL_INTERVAL_SEC) -> _FollowupInteractBoundaryResult:
    """Deliver a follow-up until interact succeeds or the session becomes first-run ready."""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    sleep_sec = max(0.01, poll_interval_sec)
    last_reason = initial_reason
    while time.monotonic() < deadline:
        if not await _team_session_has_runtime(team_manager, session_id):
            return _FollowupInteractBoundaryResult(success=False, reason=last_reason, first_request_ready=True)
        await asyncio.sleep(sleep_sec)
        if not await _team_session_has_runtime(team_manager, session_id):
            return _FollowupInteractBoundaryResult(success=False, reason=last_reason, first_request_ready=True)
        success, reason = await team_manager.interact(session_id, query)
        if success:
            return _FollowupInteractBoundaryResult(success=True, reason=None, first_request_ready=False)
        last_reason = reason
        if not _is_followup_delivery_boundary_reason(reason):
            return _FollowupInteractBoundaryResult(success=False, reason=reason, first_request_ready=False)
    first_request_ready = not await _team_session_has_runtime(team_manager, session_id)
    return _FollowupInteractBoundaryResult(success=False, reason=last_reason, first_request_ready=first_request_ready)


def _build_team_event_chunk_meta(event: Any) -> tuple[dict | None, dict]:
    """从 team event 统一推导 (agent_ref, metadata)，供所有 team 事件产出路径调用。

    - agent_ref: 成员身份标识。前端 team.member.spawned 用 agent_ref.id 拼接
      /join team_<name>_session_<sid>，取不到会 fallback 'unknown'。
    - metadata: fan_out_targets 路由元数据，由 _build_logical_targets 产出。

    按设计（§10/§14.2.3/§13.3）agent_ref 是 server 层统一注入，不在 monitor 层加。
    非 team 事件（chat.error / processing_status / completion 等控制信号）返回
    (None, {})，不注入。
    """
    if not isinstance(event, dict):
        return (None, {})
    ev_type = event.get('event_type', '')
    role = event.get('role', '')
    if role == 'teammate':
        agent_ref: dict | None = {'mode': 'team', 'id': event.get('member_name', 'teammate')}
    elif ev_type in ('team.member', 'team.task'):
        inner = event.get('event', {}) or {}
        agent_ref = {'mode': 'team', 'id': inner.get('team_id', 'team')}
    elif ev_type == 'team.message':
        inner = event.get('event', {}) or {}
        agent_ref = {'mode': 'team', 'id': inner.get('from_member', 'team')}
    else:
        agent_ref = None
    fan_out = _build_logical_targets(event)
    metadata = {'fan_out_targets': fan_out} if fan_out else {}
    return (agent_ref, metadata)


def _extract_query_directives(query: str) -> tuple[str, bool, bool]:
    """Strip all leading slash directives from the first team query.

    Returns (cleaned_query, hide_dm, debug).
    """
    query, hide_dm = strip_slash_directive(query, _HIDE_DM_PREFIX)
    query, debug = strip_slash_directive(query, DEBUG_PREFIX)
    return (query, hide_dm, debug)


@dataclass(slots=True)
class _FirstTeamRequestPreparation:
    """Result of first-request preprocessing."""
    recovered_runtime: bool
    query: Any
    hide_dm: bool
    debug: bool
    error_chunks: list[AgentResponseChunk] | None = None


async def _prepare_first_team_request(
    *,
    team_manager: Any,
    session_id: str,
    channel_id: str | None,
    request_id: str,
        query: Any) -> _FirstTeamRequestPreparation:
    """Apply first-request preprocessing shared by cold starts and fallback starts."""
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
    hide_dm = False
    debug = False
    if isinstance(query, InteractiveInput):
        wait_for_resumable = getattr(team_manager, 'wait_for_resumable_runtime', None)
        restored = False
        if callable(wait_for_resumable):
            try:
                restored = bool(await wait_for_resumable(session_id))
            except Exception as exc:
                logger.warning(
                    '[TeamHelpers] waiting for resumable runtime failed: channel_id=%s session_id=%s error=%s',
                    _resolve_channel_id(channel_id),
                    session_id,
                    exc)
        if restored or await _team_session_has_runtime(team_manager, session_id):
            logger.info(
                '[TeamHelpers] interactive input recovered paused team runtime: channel_id=%s session_id=%s',
                _resolve_channel_id(channel_id),
                session_id)
            return _FirstTeamRequestPreparation(recovered_runtime=True, query=query, hide_dm=hide_dm, debug=debug)
        logger.warning(
            '[TeamHelpers] interactive input ignored because no active team '
            'runtime exists: channel_id=%s session_id=%s',
            _resolve_channel_id(channel_id),
            session_id)
        error_chunks = [
            AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={
                    'event_type': 'chat.error',
                    'error': 'Team runtime is not active, please restart the task'},
                is_complete=False),
            _team_processing_done_chunk(
                request_id,
                channel_id,
                session_id),
            AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload=None,
                is_complete=True)]
        return _FirstTeamRequestPreparation(
            recovered_runtime=False,
            query=query,
            hide_dm=hide_dm,
            debug=debug,
            error_chunks=error_chunks)
    query, hide_dm, debug = _extract_query_directives(str(query or ''))
    if hide_dm or debug:
        logger.info(
            '[TeamHelpers] query directives captured for first team request: '
            'channel_id=%s session_id=%s hide_dm=%s debug=%s',
            _resolve_channel_id(channel_id),
            session_id,
            hide_dm,
            debug)
    return _FirstTeamRequestPreparation(recovered_runtime=False, query=query, hide_dm=hide_dm, debug=debug)


def sync_team_identity_metadata(
    *,
    channel_id: str | None,
    session_id: str,
    mode: str,
    ready_team_name: str,
        activation_kind: str | None) -> None:
    """Persist team identity when a team runtime becomes ready."""
    metadata = get_session_metadata(session_id)
    existing_team_name = str(metadata.get('team_name') or '').strip()
    normalized_kind = str(activation_kind or '').strip()
    if existing_team_name and existing_team_name != ready_team_name:
        logger.warning(
            '[TeamHelpers] team session identity mismatch, keep existing metadata: '
            'session_id=%s existing_team_name=%s new_team_name=%s activation_kind=%s',
            session_id,
            existing_team_name,
            ready_team_name,
            normalized_kind)
        return
    # Session metadata schema has no team_name field yet; persist mode only.
    update_session_metadata(
        session_id=session_id,
        channel_id=_resolve_channel_id(channel_id),
        mode=mode,
    )


def persist_workflow_runs(runs: dict[str, WorkflowRunState], session_id: str) -> None:
    """Persist WorkflowRunState dict to session metadata (file-based store)."""
    from jiuwenclaw.agentserver.session_metadata import _read_metadata, _enqueue_write
    runs_data = {run_id: run_state.model_dump() for run_id, run_state in runs.items()}
    metadata = _read_metadata(session_id)
    metadata[_WORKFLOW_RUNS_STATE_KEY] = runs_data
    _enqueue_write(session_id, metadata)


def restore_workflow_runs(session_id: str) -> dict[str, WorkflowRunState] | None:
    """Restore WorkflowRunState dict from session metadata."""
    from jiuwenclaw.agentserver.session_metadata import _read_metadata
    metadata = _read_metadata(session_id)
    runs_data = metadata.get(_WORKFLOW_RUNS_STATE_KEY)
    if not runs_data:
        return None
    return {run_id: WorkflowRunState.model_validate(run_data) for run_id, run_data in runs_data.items()}


def _resolve_channel_id(channel_id: str | None) -> str:
    return str(channel_id or 'default').strip() or 'default'


def _resolve_request_language(request: Any) -> str:
    metadata = getattr(request, 'metadata', None)
    params = getattr(request, 'params', None)
    sources = []
    if isinstance(metadata, dict):
        sources.append(metadata)
    if isinstance(params, dict):
        sources.append(params)
    for source in sources:
        for key in ('language', 'preferred_language', 'preferred_response_language'):
            value = source.get(key)
            if value:
                return str(value).strip().lower() or 'zh'
    return 'zh'


def _safe_query_preview(query: Any, limit: int = 200) -> str:
    if isinstance(query, str):
        return query[:limit]
    return str(query)[:limit]


def _normalize_team_query(query: Any, *, channel_id: str | None, language: str) -> Any:
    return query


async def _team_session_has_runtime(team_manager: TeamManager, session_id: str) -> bool:
    return team_manager.is_runtime_active(session_id) or team_manager.is_runtime_pending(
        session_id) or bool(team_manager.has_stream_task(session_id))


async def query_team_human_members_for_join(session_id: str, team_name: str) -> list[dict[str, Any]]:
    """直查 team.db 取该 team 的全部成员（未 role 过滤，交调用方过滤）。

    纯查询：session_id↔team_name 一致性校验与对外文案均由 gateway 拼，
    本函数只查不判。team_name 空、DB miss、DB 异常一律返回空 list。
    session_id 仅用于日志排查，不参与查询。
    """
    if not team_name:
        return []
    try:
        members = await TeamMonitorHandler.get_member_list_from_db(team_name)
    except Exception as exc:
        logger.warning(
            '[TeamHelpers] query_team_human_members_for_join db query failed: session=%s team=%s error=%s',
            session_id,
            team_name,
            exc)
        return []
    return members or []


async def ensure_monitor_handlers_for_active_runtime(
        channel_id: str | None,
        session_id: str,
        team_name: str,
        hide_dm: bool = False,
        enable_swarmflow: bool = False) -> None:
    """Attach TeamMonitorHandler and optionally WorkflowMonitorHandler for the active runtime.

    Both handlers obtain their own TeamMonitor from Runner (independent listeners on
    team_agent). WorkflowMonitorHandler is only created when enable_swarmflow is True.
    """
    tm = get_team_manager(channel_id)
    existing_monitor = tm.get_monitor(session_id)
    if existing_monitor is None or not existing_monitor.is_running:
        token = set_session_id(session_id)
        try:
            monitor = await Runner.get_agent_team_monitor(team_name=team_name, session_id=session_id, hide_dm=hide_dm)
        finally:
            reset_session_id(token)
        if monitor is None:
            logger.warning(
                '[TeamHelpers] active team monitor unavailable: channel_id=%s session_id=%s team_name=%s',
                _resolve_channel_id(channel_id),
                session_id,
                team_name)
        else:
            monitor_handler = TeamMonitorHandler(monitor, session_id)
            try:
                await monitor_handler.start()
                tm.register_monitor(session_id, monitor_handler)
                logger.info('[TeamHelpers] Monitor started: channel_id=%s session_id=%s team_name=%s',
                            _resolve_channel_id(channel_id), session_id, team_name)
                if monitor_handler.is_running:
                    asyncio.create_task(_consume_monitor_events(channel_id, session_id, monitor_handler))
            except Exception as exc:
                logger.warning('[TeamHelpers] Monitor start failed: %s', exc)
    if not enable_swarmflow:
        return
    existing_wf = tm.get_workflow_handler(session_id)
    if existing_wf is not None and existing_wf.is_running:
        return
    initial_runs: dict[str, WorkflowRunState] | None = None
    if existing_wf is not None:
        initial_runs = existing_wf.get_run_states()
        restored_from_disk = restore_workflow_runs(session_id)
        if restored_from_disk:
            for run_id, run_state in restored_from_disk.items():
                if run_id not in initial_runs:
                    initial_runs[run_id] = run_state
        tm.pop_workflow_handler(session_id)
    else:
        initial_runs = restore_workflow_runs(session_id)
    wf_token = set_session_id(session_id)
    try:
        wf_monitor = await Runner.get_agent_team_monitor(team_name=team_name, session_id=session_id)
    finally:
        reset_session_id(wf_token)
    if wf_monitor is None:
        logger.warning(
            '[TeamHelpers] workflow monitor unavailable: channel_id=%s session_id=%s team_name=%s',
            _resolve_channel_id(channel_id),
            session_id,
            team_name)
        return
    wf_handler = WorkflowMonitorHandler(
        monitor=wf_monitor,
        session_id=session_id,
        channel_id=channel_id,
        initial_runs=initial_runs)
    try:
        await wf_handler.start()
        tm.register_workflow_handler(session_id, wf_handler)
        logger.info(
            '[TeamHelpers] WorkflowMonitorHandler started: channel_id=%s session_id=%s team_name=%s',
            _resolve_channel_id(channel_id),
            session_id,
            team_name)
        if wf_handler.is_running:
            asyncio.create_task(_consume_workflow_events(channel_id, session_id, wf_handler),
                                name=f'workflow_events_{_resolve_channel_id(channel_id)}_{session_id}')
    except Exception as exc:
        logger.warning('[TeamHelpers] WorkflowMonitorHandler start failed: %s', exc)
_CRON_DELEGATION_GRACE_SECONDS = 2.0
_TEAM_BUILDING_EVENT_TYPES = frozenset({'team.member', 'team.task', 'workflow.updated'})


def _broadcast_event(channel_id: str | None, session_id: str, event: dict[str, Any]) -> None:
    """Broadcast an event to all request queues waiting on the same session."""
    tm = get_team_manager(channel_id)
    tm.broadcast_event(session_id, event)
    if not tm.has_seen_team_events(session_id) and event.get('event_type') in _TEAM_BUILDING_EVENT_TYPES:
        tm.mark_seen_team_events(session_id)


def _approval_chunk_from_event(evt: Any) -> dict[str, Any] | None:
    parsed = parse_stream_chunk(evt)
    if not isinstance(parsed, dict) or parsed.get('event_type') != 'chat.ask_user_question':
        return None
    request_id = parsed.get('request_id')
    questions = parsed.get('questions')
    if not isinstance(request_id, str) or not request_id.strip():
        return None
    if not isinstance(questions, list) or not questions:
        return None
    return parsed


async def _broadcast_team_state_snapshot(channel_id: str | None, session_id: str) -> None:
    """Broadcast a snapshot of all member and task states.

    Called before ``team.completed`` so the frontend receives the final
    state (e.g. members transitioning from "busy" to "ready") even when
    the monitor events arrive after the has_stream_task loop exits.

    Each snapshot event is also persisted via ``_persist_team_history_event``,
    mirroring the behaviour of ``_consume_monitor_events``.
    """
    try:
        team_manager = get_team_manager(channel_id)
        monitor_handler = team_manager.get_monitor_handler(session_id)
        if monitor_handler is None:
            return
        snapshot = await monitor_handler.get_team_snapshot()
        if snapshot is None:
            return
        team_id = snapshot.get('team_id', '')
        for m in snapshot.get('members', []):
            event = {
                'event_type': 'team.member',
                'session_id': session_id,
                'event': {
                    'type': 'team.member.status_changed',
                    'team_id': team_id,
                    'member_id': m['member_id'],
                    'new_status': m['status']}}
            _persist_team_history_event(channel_id, session_id, event)
            _broadcast_event(channel_id, session_id, event)
        for t in snapshot.get('tasks', []):
            event = {
                'event_type': 'team.task',
                'session_id': session_id,
                'event': {
                    'type': 'team.task.status_snapshot',
                    'team_id': team_id,
                    'task_id': t['task_id'],
                    'status': t['status'],
                    'assignee': t.get('assignee'),
                    'title': t.get('title'),
                    'content': t.get('content'),
                    'title_truncated': t.get('title_truncated'),
                    'title_original_size': t.get('title_original_size'),
                    'content_truncated': t.get('content_truncated'),
                    'content_original_size': t.get('content_original_size')}}
            _persist_team_history_event(channel_id, session_id, event)
            _broadcast_event(channel_id, session_id, event)
    except Exception:
        logger.debug('[TeamHelpers] failed to broadcast team state snapshot: session_id=%s', session_id)


def _approval_result_from_event_or_items(*,
                                         skill_name: str,
                                         event: Any,
                                         items: list[Any],
                                         no_changes_output: str,
                                         invalid_output: str) -> dict[str,
                                                                      Any]:
    approval_chunk = _approval_chunk_from_event(event)
    if approval_chunk is not None:
        questions = approval_chunk.get('questions', [])
        return {
            'output': f"Skill '{skill_name}' 演进请求已生成，请在审批弹框中确认。",
            'result_type': 'answer',
            'approval_chunks': [approval_chunk],
            'question_count': len(questions)}
    if not items:
        return {'output': no_changes_output, 'result_type': 'answer'}
    return {'output': invalid_output, 'result_type': 'error'}


def _is_leader_output(chunk: Any) -> bool:
    """Return whether a team OutputSchema chunk should be shown to claw users."""
    chunk_type = getattr(chunk, 'type', None)
    payload = getattr(chunk, 'payload', None)
    if chunk_type == 'message' and isinstance(payload, dict):
        event_type_str = payload.get('event_type')
        if event_type_str in ('team.runtime_ready', 'team.completed'):
            return True
    if chunk_type == 'team.runtime_ready':
        return True
    role = getattr(chunk, 'role', None)
    if role is None:
        return True
    if role == TeamRole.LEADER:
        return True
    role_value = getattr(role, 'value', role)
    return str(role_value).strip().lower() == TeamRole.LEADER.value


def _is_teammate_output(chunk: Any) -> bool:
    """Return whether a team OutputSchema chunk is from a non-leader member."""
    role = getattr(chunk, 'role', None)
    if role is None:
        return False
    if role == TeamRole.LEADER:
        return False
    role_value = getattr(role, 'value', role)
    return str(role_value).strip().lower() != TeamRole.LEADER.value


def _enrich_teammate_event(parsed: dict[str, Any], chunk: Any) -> dict[str, Any]:
    """Enrich a parsed teammate event with role and source_member for frontend display."""
    parsed['role'] = TeamRole.TEAMMATE.value
    source_member = getattr(chunk, 'source_member', None)
    if source_member:
        parsed['member_name'] = str(source_member)
    return parsed


_TEAM_TOOL_RESULT_TEXT_LIMIT = 512


def _truncate_team_tool_result_event(parsed: dict[str, Any]) -> dict[str, Any]:
    """Trim large team tool result fields before forwarding them to clients."""
    if parsed.get('event_type') != 'chat.tool_result':
        return parsed
    next_event = dict(parsed)
    truncated = False
    original_size = 0
    for key in ('result', 'raw_output'):
        value = next_event.get(key)
        if not isinstance(value, str):
            continue
        original_size += len(value)
        if len(value) <= _TEAM_TOOL_RESULT_TEXT_LIMIT:
            continue
        next_event[key] = value[:_TEAM_TOOL_RESULT_TEXT_LIMIT]
        truncated = True
    if truncated:
        next_event['truncated'] = True
        next_event['original_size'] = original_size
    return next_event


def _is_duplicate_ask_user_question(parsed: dict[str, Any], emitted_request_ids: set[str]) -> bool:
    if parsed.get('event_type') != 'chat.ask_user_question':
        return False
    request_id = str(parsed.get('request_id') or '').strip()
    if not request_id:
        return False
    if request_id in emitted_request_ids:
        return True
    emitted_request_ids.add(request_id)
    return False


def _team_processing_done_chunk(request_id: str, channel_id: str | None, session_id: str) -> AgentResponseChunk:
    return AgentResponseChunk(
        request_id=request_id,
        channel_id=channel_id,
        payload={
            'event_type': 'chat.processing_status',
            'session_id': session_id,
            'is_processing': False,
            'is_complete': True},
        is_complete=False)


def _resolve_team_slash_skills_dir(session_id: str) -> str | None:
    metadata = get_session_metadata(session_id)
    team_name = str(metadata.get('team_name') or '').strip()
    if not team_name:
        return None
    return str(team_home(team_name) / 'team-workspace' / 'skills')


def _team_spec_skills_dir(team_spec: Any) -> str:
    workspace = getattr(team_spec, 'workspace', None)
    root_path = str(getattr(workspace, 'root_path', '') or '').strip()
    if root_path:
        return str(Path(root_path) / 'skills')
    team_name = str(getattr(team_spec, 'team_name', '') or '').strip()
    return str(team_home(team_name) / 'team-workspace' / 'skills')


def _team_spec_monitor_roots(team_spec: Any, session_id: str | None = None) -> list[str]:
    """Return team/member workspace roots where file-op history may be written."""
    roots: list[str] = []

    def add_root(value: Any) -> None:
        raw = str(value or '').strip()
        if not raw:
            return
        try:
            root = str(Path(raw).expanduser().resolve())
        except Exception:
            root = raw
        if root not in roots:
            roots.append(root)
    workspace = getattr(team_spec, 'workspace', None)
    root_path = str(getattr(workspace, 'root_path', '') or '').strip()
    team_name = str(getattr(team_spec, 'team_name', '') or '').strip()
    home = team_home(team_name)
    add_root(root_path or str(home / 'team-workspace'))
    add_root(home / 'workspaces')
    if session_id and team_name:
        add_root(home / 'sessions' / _safe_team_path_segment(session_id) / 'worktrees')
    agents = getattr(team_spec, 'agents', None)
    if isinstance(agents, dict):
        for member_name, member_spec in agents.items():
            member_workspace = getattr(member_spec, 'workspace', None)
            add_root(getattr(member_workspace, 'root_path', None))
            add_root(home / 'workspaces' / f'{member_name}_workspace')
            try:
                add_root(str(independent_member_workspace(str(member_name))))
            except Exception as exc:
                logger.debug(
                    '[TeamHelpers] failed to resolve independent member workspace: member=%s error=%s',
                    member_name,
                    exc)
    return roots


def _persist_team_file_monitor_roots(session_id: str, team_spec: Any) -> None:
    roots = _team_spec_monitor_roots(team_spec, session_id=session_id)
    if not roots:
        return
    try:
        from jiuwenclaw.agentserver.session_metadata import _enqueue_write, _read_metadata
        metadata = _read_metadata(session_id)
        if not metadata:
            for _ in range(3):
                time.sleep(0.05)
                metadata = _read_metadata(session_id)
                if metadata:
                    break
            if not metadata:
                logger.warning(
                    '[TeamHelpers] cannot persist team_file_monitor_roots: metadata not initialized, session=%s',
                    session_id)
                return
        existing = metadata.get('team_file_monitor_roots')
        if roots == existing:
            return
        metadata['team_file_monitor_roots'] = roots
        _enqueue_write(session_id, metadata)
    except Exception as exc:
        logger.warning('[TeamHelpers] failed to persist team file monitor roots: session=%s error=%s', session_id, exc)


async def _start_team_stream_round(
    *,
    channel_id: str | None,
    session_id: str,
    request_id: str,
    team_manager: Any,
    team_name: str,
    team_spec: Any,
    query: str,
    hide_dm: bool = False,
    debug: bool = False,
        source: str = 'first') -> asyncio.Queue:
    """Start a team stream round and register its waiter queue."""
    from jiuwenclaw.agentserver.team.team_manager import sync_team_observability
    sync_team_observability()
    await team_manager.prepare_runtime_activation(session_id, team_name)
    request_queue: asyncio.Queue = asyncio.Queue()
    team_manager.add_waiter(session_id, request_id, request_queue)
    logger.info('[TeamHelpers] %s team request: channel_id=%s session_id=%s',
                source, _resolve_channel_id(channel_id), session_id)
    stream_envs: dict[str, Any] = {}
    if hide_dm:
        stream_envs['hide_dm'] = True
    if debug:
        stream_envs[_STREAM_TRACE_ENV_KEY] = '1'
    round_id = increment_session_round_count(session_id)
    stream_task = asyncio.create_task(
        _consume_stream_with_query(
            channel_id,
            session_id,
            team_spec,
            query,
            round_id=round_id,
            envs=stream_envs or None))
    team_manager.register_stream_task(session_id, stream_task)
    return request_queue


async def process_team_message_stream(request: Any,
                                      inputs: dict[str,
                                                   Any],
                                      deep_agent: DeepAgent,
                                      *,
                                      runtime_scope: Any | None = None) -> AsyncIterator[AgentResponseChunk]:
    """Process a team-mode streaming request."""
    session_id = request.session_id or 'default'
    rid = request.request_id
    channel_id = request.channel_id
    team_manager = get_team_manager(channel_id)
    language = _resolve_request_language(request)
    query = _normalize_team_query(inputs.get('query', ''), channel_id=channel_id, language=language)
    query_text = query if isinstance(query, str) else ''
    try:
        from jiuwenclaw.agentserver.team.remote_member_bootstrap import wait_for_pending_shutdown_cleanup_for_session
        await wait_for_pending_shutdown_cleanup_for_session(session_id)
    except Exception as exc:
        logger.warning(
            '[TeamHelpers] waiting for pending shutdown cleanup failed: session_id=%s error=%s',
            session_id,
            exc)
    has_active_waiters = team_manager.has_waiters(session_id)
    is_first_request = not team_manager.has_stream_task(session_id) and (
        not has_active_waiters) and (
        not team_manager.is_session_initialized(session_id))
    request_queue: asyncio.Queue | None = None
    hide_dm = False
    debug = False
    if is_first_request:
        preparation = await _prepare_first_team_request(
            team_manager=team_manager,
            session_id=session_id,
            channel_id=channel_id,
            request_id=rid,
            query=query,
        )
        if preparation.error_chunks is not None:
            for chunk in preparation.error_chunks:
                yield chunk
            return
        if preparation.recovered_runtime:
            is_first_request = False
        else:
            query = preparation.query
            query_text = query if isinstance(query, str) else ''
            hide_dm = preparation.hide_dm
            debug = preparation.debug
    try:
        request_metadata = dict(request.metadata or {})
        member_name = str(request_metadata.get('member_name') or '').strip()
        if member_name and query_text and (not query_text.startswith('$')):
            query = f'${member_name} {query_text}'
            query_text = query if isinstance(query, str) else str(query)
            logger.info(
                '[TeamHelpers] prefixed query with member identity: member=%s session=%s query_preview=%s',
                member_name,
                session_id,
                _safe_query_preview(query))
        if isinstance(getattr(request, 'params', None), dict):
            request_metadata.setdefault('mode', request.params.get('mode'))
        resolved_mode = str(request_metadata.get('mode') or '').strip()
        params_obj = getattr(request, 'params', None)
        requested_model_name = (str(params_obj.get('model_name') or '').strip()
                                if isinstance(params_obj, dict) else '') or None
        team_spec = await team_manager.get_swarm_enriched_team_spec(
            session_id,
            mode=resolved_mode or "team",
            request_metadata=request_metadata,
            requested_model_name=requested_model_name,
        )
        _persist_team_file_monitor_roots(session_id, team_spec)
    except Exception as exc:
        logger.exception('[TeamHelpers] TeamAgent create failed: %s', exc)
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload={'event_type': 'chat.error', 'error': str(exc)},
            is_complete=False,
        )
        yield AgentResponseChunk(request_id=rid, channel_id=channel_id, payload=None, is_complete=True)
        return
    team_name = team_spec.team_name
    team_skills_dir = _team_spec_skills_dir(team_spec)
    ensure_ready = getattr(team_manager, 'ensure_team_shared_skills_ready_for_session', None)
    shared_skills_ready_prepared = False
    if is_first_request and callable(ensure_ready):
        ensure_ready(session_id, team_spec)
        shared_skills_ready_prepared = True
    try:
        first_request_source = 'first'
        if not is_first_request:
            logger.info('[TeamHelpers] follow-up team request: channel_id=%s session_id=%s',
                        _resolve_channel_id(channel_id), session_id)
            if query:
                success, reason = await team_manager.interact(session_id, query)
                if not success:
                    logger.warning(
                        '[TeamHelpers] interact failed: channel_id=%s session_id=%s reason=%s query=%s',
                        _resolve_channel_id(channel_id),
                        session_id,
                        reason,
                        _safe_query_preview(query))
                    first_request_ready = False
                    if _is_followup_delivery_boundary_reason(reason):
                        boundary_result = await _deliver_followup_interact_across_boundary(
                            team_manager,
                            session_id,
                            query,
                            initial_reason=reason,
                        )
                        success = boundary_result.success
                        reason = boundary_result.reason
                        first_request_ready = boundary_result.first_request_ready
                    if not success and first_request_ready:
                        preparation = await _prepare_first_team_request(
                            team_manager=team_manager,
                            session_id=session_id,
                            channel_id=channel_id,
                            request_id=rid,
                            query=query,
                        )
                        if preparation.error_chunks is not None:
                            for chunk in preparation.error_chunks:
                                yield chunk
                            return
                        is_first_request = not preparation.recovered_runtime
                        if is_first_request:
                            first_request_source = 'follow-up fallback'
                            query = preparation.query
                            hide_dm = preparation.hide_dm
                            debug = preparation.debug
                            logger.info(
                                '[TeamHelpers] follow-up interact reclassified by '
                                'first-request condition: channel_id=%s session_id=%s reason=%s',
                                _resolve_channel_id(channel_id),
                                session_id,
                                reason)
                    elif not success and _is_followup_delivery_boundary_reason(reason):
                        reason = reason or 'gate_closed'
                    if not success and (not is_first_request):
                        final_reason = reason or ''
                        if final_reason == 'gate_closed':
                            yield AgentResponseChunk(
                                request_id=rid,
                                channel_id=channel_id,
                                payload=None,
                                is_complete=True,
                            )
                            return
                        error_msg = _INTERACT_REASON_ERROR_MAP.get(
                            final_reason, 'Failed to send message, please try again later')
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=channel_id,
                            payload={'event_type': 'chat.error', 'error': error_msg},
                            is_complete=False,
                        )
                        yield AgentResponseChunk(request_id=rid, channel_id=channel_id, payload=None, is_complete=True)
                        return
                logger.info(
                    '[TeamHelpers] follow-up request submitted without waiter: '
                    'channel_id=%s session_id=%s request_id=%s',
                    _resolve_channel_id(channel_id),
                    session_id,
                    rid)
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=channel_id,
                    payload={
                        'event_type': 'chat.processing_status_deferred',
                        'session_id': session_id,
                    },
                    is_complete=False,
                )
                yield AgentResponseChunk(request_id=rid, channel_id=channel_id, payload=None, is_complete=True)
                return
        if is_first_request:
            if callable(ensure_ready) and (not shared_skills_ready_prepared):
                ensure_ready(session_id, team_spec)
                shared_skills_ready_prepared = True
            request_queue = await _start_team_stream_round(
                channel_id=channel_id,
                session_id=session_id,
                request_id=rid,
                team_manager=team_manager,
                team_name=team_name,
                team_spec=team_spec,
                query=query,
                hide_dm=hide_dm,
                debug=debug,
                source=first_request_source,
            )
        try:
            while team_manager.has_stream_task(session_id):
                if request_queue is None:
                    break
                try:
                    event = await asyncio.wait_for(request_queue.get(), timeout=0.1)
                    # AgentResponseChunk has no agent_ref/metadata; stream payload only.
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=channel_id,
                        payload=event,
                        is_complete=False,
                    )
                    if isinstance(event, dict) and event.get('event_type') == 'team.error':
                        break
                except asyncio.TimeoutError:
                    if not team_manager.has_stream_task(session_id):
                        break
                    continue
            if request_queue is not None:
                drained = 0
                while True:
                    try:
                        event = request_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    drained += 1
                    yield AgentResponseChunk(request_id=rid, channel_id=channel_id, payload=event, is_complete=False)
                    if isinstance(event, dict):
                        if event.get('event_type') == 'team.error':
                            break
                if drained:
                    logger.info(
                        '[TeamHelpers] drained remaining events after has_stream_task '
                        'loop: channel_id=%s session_id=%s request_id=%s drained=%s',
                        _resolve_channel_id(channel_id),
                        session_id,
                        rid,
                        drained)
        except asyncio.CancelledError:
            logger.info('[TeamHelpers] event stream cancelled: channel_id=%s session_id=%s request_id=%s',
                        _resolve_channel_id(channel_id), session_id, rid)
            raise
        except Exception as exc:
            logger.exception(
                '[TeamHelpers] event stream failed: channel_id=%s session_id=%s error=%s',
                _resolve_channel_id(channel_id),
                session_id,
                exc)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=channel_id,
                payload={'event_type': 'chat.error', 'error': str(exc)},
                is_complete=False,
            )
        yield AgentResponseChunk(request_id=rid, channel_id=channel_id, payload=None, is_complete=True)
        team_manager.clear_session_initialized(session_id)
        logger.info('[TeamHelpers] stream ended, cleared init marker: channel_id=%s session_id=%s',
                    _resolve_channel_id(channel_id), session_id)
    finally:
        if request_queue is not None:
            team_manager.remove_waiter(session_id, rid)
            if not team_manager.has_waiters(session_id):
                logger.info('[TeamHelpers] cleared waiter set: session_id=%s', session_id)


async def _consume_stream_with_query(channel_id: str | None,
                                     session_id: str,
                                     team_spec: Any,
                                     initial_query: str,
                                     *,
                                     round_id: int,
                                     envs: dict[str,
                                                Any] | None = None) -> None:
    """Consume the team stream in the background and broadcast parsed events."""
    _envs = envs or {}
    hide_dm: bool = bool(_envs.get('hide_dm', False))
    received_chunks = 0
    emitted_ask_user_request_ids: set[str] = set()
    tm_ = get_team_manager(channel_id)
    tm_.reset_seen_team_events(session_id)
    tm_.reset_workflow_completed(session_id)
    try:
        logger.info('[TeamHelpers] stream started: channel_id=%s session_id=%s round_id=%s',
                    _resolve_channel_id(channel_id), session_id, round_id)
        _broadcast_event(channel_id,
                         session_id,
                         {'event_type': 'chat.processing_status',
                          'session_id': session_id,
                          'rid': round_id,
                          'is_processing': True,
                          'is_complete': False})
        stream_trace_enabled = bool(_envs.get(_STREAM_TRACE_ENV_KEY) or os.environ.get(_STREAM_TRACE_ENV_KEY))
        lg: TeamStreamLogger | None = None
        if stream_trace_enabled:
            traces_dir = get_agent_teams_home() / 'traces'
            traces_dir.mkdir(parents=True, exist_ok=True)
            lg = TeamStreamLogger(file_path=str(traces_dir / f'dump-team-{session_id}.txt'))
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=team_spec,
            inputs={'query': initial_query},
            session=session_id,
            envs=envs,
            stream_logger=lg,
        ):
            received_chunks += 1
            if received_chunks == 1 or received_chunks % 30 == 0:
                _role = getattr(chunk, 'role', None)
                logger.info(
                    '[TeamHelpers] stream progress: channel_id=%s session_id=%s received=%s role=%s type=%s',
                    _resolve_channel_id(channel_id),
                    session_id,
                    received_chunks,
                    _role,
                    getattr(
                        chunk,
                        'type',
                        None))
            is_leader = _is_leader_output(chunk)
            is_teammate = _is_teammate_output(chunk)
            if not is_leader and (not is_teammate):
                if received_chunks <= 3:
                    logger.info(
                        '[TeamHelpers] stream chunk filtered (non-leader/non-teammate): session_id=%s role=%s type=%s',
                        session_id,
                        getattr(
                            chunk,
                            'role',
                            None),
                        getattr(
                            chunk,
                            'type',
                            None))
                continue
            if _team_hide_teammate_enabled() and (not is_leader):
                continue
            parsed = parse_stream_chunk(chunk)
            if parsed is not None:
                if not is_leader and parsed.get('event_type') == 'chat.reasoning':
                    continue
                if _is_duplicate_ask_user_question(parsed, emitted_ask_user_request_ids):
                    continue
                if not is_leader and parsed.get('event_type') == 'chat.ask_user_question':
                    continue
                parsed['rid'] = round_id
                if is_teammate:
                    parsed = _enrich_teammate_event(parsed, chunk)
                elif is_leader:
                    parsed['role'] = TeamRole.LEADER.value
                parsed = _truncate_team_tool_result_event(parsed)
                if parsed.get('event_type') == 'team.runtime_ready':
                    ready_team_name = str(parsed.get('team_name') or team_spec.team_name)
                    activation_kind = str(parsed.get('activation_kind') or '').strip()
                    sync_team_identity_metadata(
                        channel_id=channel_id,
                        session_id=session_id,
                        mode='team',
                        ready_team_name=ready_team_name,
                        activation_kind=activation_kind)
                    tm = get_team_manager(channel_id)
                    tm.commit_runtime_ready(session_id, ready_team_name)
                    await tm.attach_distributed_hooks_for_runner_runtime(
                        team_name=ready_team_name,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    await ensure_monitor_handlers_for_active_runtime(
                        channel_id,
                        session_id,
                        ready_team_name,
                        hide_dm=hide_dm,
                        enable_swarmflow=bool(
                            getattr(team_spec, 'enable_swarmflow', False),
                        ),
                    )
                elif parsed.get('event_type') == 'team.interact.failed':
                    reason = str(parsed.get('reason') or '').strip()
                    error_msg = _INTERACT_REASON_ERROR_MAP.get(reason, 'Failed to send message, please try again later')
                    logger.warning(
                        '[TeamHelpers] initial team interact failed: channel_id=%s session_id=%s reason=%s',
                        _resolve_channel_id(channel_id),
                        session_id,
                        reason)
                    _broadcast_event(channel_id,
                                     session_id,
                                     {'event_type': 'chat.error',
                                      'error': error_msg,
                                      'reason': reason,
                                      'session_id': session_id,
                                      'rid': round_id})
                    _broadcast_event(channel_id,
                                     session_id,
                                     {'event_type': 'chat.processing_status',
                                      'session_id': session_id,
                                      'rid': round_id,
                                      'is_processing': False,
                                      'is_complete': True})
                    continue
                elif parsed.get('event_type') == 'team.completed':
                    _broadcast_event(channel_id,
                                     session_id,
                                     {'event_type': 'chat.processing_status',
                                      'session_id': session_id,
                                      'rid': round_id,
                                      'is_processing': False,
                                      'is_complete': True,
                                      'member_count': parsed.get('member_count'),
                                         'task_count': parsed.get('task_count')})
                    continue
                elif parsed.get('event_type') == 'chat.error':
                    _broadcast_event(channel_id, session_id, parsed)
                    if is_leader:
                        _broadcast_event(
                            channel_id, session_id, {
                                'event_type': 'chat.final', 'content': '', 'session_id': session_id, 'rid': round_id})
                    continue
                if parsed.get('event_type') == 'chat.final':
                    tm_ = get_team_manager(channel_id)
                    should_finish_round = not tm_.has_seen_team_events(
                        session_id) or tm_.is_workflow_completed(session_id)
                    _broadcast_event(channel_id, session_id, parsed)
                    if should_finish_round:
                        _broadcast_event(channel_id,
                                         session_id,
                                         {'event_type': 'chat.processing_status',
                                          'session_id': session_id,
                                          'rid': round_id,
                                          'is_processing': False,
                                          'is_complete': True})
                    continue
                _broadcast_event(channel_id, session_id, parsed)
        if received_chunks == 0:
            logger.warning(
                '[TeamHelpers] stream ended with no output: channel_id=%s session_id=%s',
                _resolve_channel_id(channel_id),
                session_id)
            _broadcast_event(channel_id,
                             session_id,
                             {'event_type': 'team.error',
                              'error': (
                                  'Team stream ended with no output '
                                  '(possible pool/DB inconsistency or internal error)'
                              ),
                              'session_id': session_id})
        else:
            logger.info('[TeamHelpers] stream ended: channel_id=%s session_id=%s chunks=%s',
                        _resolve_channel_id(channel_id), session_id, received_chunks)
    except asyncio.CancelledError:
        logger.info('[TeamHelpers] stream cancelled: channel_id=%s session_id=%s',
                    _resolve_channel_id(channel_id), session_id)
        raise
    except Exception as exc:
        logger.error('[TeamHelpers] stream failed: channel_id=%s session_id=%s error=%s',
                     _resolve_channel_id(channel_id), session_id, exc, exc_info=True)
        _broadcast_event(channel_id, session_id, {'event_type': 'team.error',
                         'error': str(exc), 'session_id': session_id})
    finally:
        if lg is not None:
            try:
                lg.flush()
            except Exception as e:
                logger.warning(f'TeamStreamLogger flush failed, error is {e}')
        await _broadcast_team_state_snapshot(channel_id, session_id)
        try:
            _broadcast_event(channel_id, session_id, {'event_type': 'team.completed', 'session_id': session_id})
        except Exception:
            logger.debug('[TeamHelpers] failed to broadcast team.completed on stream end: session_id=%s', session_id)
        team_manager = get_team_manager(channel_id)
        team_manager.clear_pending_runtime(session_id)
        clear_active_runtime = getattr(team_manager, 'clear_active_runtime', None)
        if callable(clear_active_runtime):
            clear_active_runtime(session_id)
        team_manager.pop_stream_task(session_id)


async def _consume_monitor_events(channel_id: str | None, session_id: str, monitor_handler: TeamMonitorHandler) -> None:
    """Consume monitor events in the background and broadcast them."""
    try:
        logger.info('[TeamHelpers] monitor event loop started: channel_id=%s session_id=%s',
                    _resolve_channel_id(channel_id), session_id)
        async for event in monitor_handler.events():
            _persist_team_history_event(channel_id, session_id, event)
            _broadcast_event(channel_id, session_id, event)
        logger.info('[TeamHelpers] monitor event loop ended: channel_id=%s session_id=%s',
                    _resolve_channel_id(channel_id), session_id)
    except asyncio.CancelledError:
        logger.info('[TeamHelpers] monitor event loop cancelled: channel_id=%s session_id=%s',
                    _resolve_channel_id(channel_id), session_id)
        raise
    except Exception as exc:
        logger.error('[TeamHelpers] monitor event loop failed: channel_id=%s session_id=%s error=%s',
                     _resolve_channel_id(channel_id), session_id, exc)
_WF_PHASE_STATUS_TO_TASK: dict[str,
                               tuple[str,
                                     str]] = {'planned': ('team.task.created',
                                                          'pending'),
                                              'running': ('team.task.claimed',
                                                          'in_progress'),
                                              'completed': ('team.task.completed',
                                                            'completed'),
                                              'failed': ('team.task.cancelled',
                                                         'cancelled'),
                                              'stopped': ('team.task.cancelled',
                                                          'cancelled')}


def _team_event_envelope(category: str, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Wrap an inner team event dict in the standard broadcast envelope."""
    return {'event_type': category, 'session_id': session_id, 'event': event}


def _workflow_updated_to_team_events(event: dict[str,
                                                 Any],
                                     session_id: str,
                                     seen_phase: dict[str,
                                                      str],
                                     seen_agent: dict[str,
                                                      str],
                                     spawned_members: set[str]) -> list[dict[str,
                                                                             Any]]:
    """Convert one ``workflow.updated`` event into web ``team.member`` / ``team.task`` events.

    Each swarmflow phase becomes a ``team.task`` and each worker (agent) becomes a
    ``team.member``. Only status *changes* produce events — the ``workflow.updated``
    delta repeatedly re-includes a running phase (once per agent that starts inside
    it), so ``seen_phase`` / ``seen_agent`` dedup by last-observed status.
    """
    if event.get('event_type') != 'workflow.updated':
        return []
    wf = event.get('workflow') or {}
    run_id = str(wf.get('id') or '')
    team_id = str(wf.get('name') or run_id or 'swarmflow')
    if not run_id:
        return []
    out: list[dict[str, Any]] = []
    for phase in wf.get('phases', []) or []:
        phase_id = phase.get('id')
        status = phase.get('status')
        if not phase_id or not status:
            continue
        task_id = f'{run_id}:{phase_id}'
        if seen_phase.get(task_id) != status:
            seen_phase[task_id] = status
            mapping = _WF_PHASE_STATUS_TO_TASK.get(status)
            if mapping is not None:
                task_type, task_status = mapping
                out.append(_team_event_envelope('team.task',
                                                session_id,
                                                {'type': task_type,
                                                 'team_id': team_id,
                                                 'task_id': task_id,
                                                 'title': phase.get('name') or phase_id,
                                                    'status': task_status}))
        for agent in phase.get('agents', []) or []:
            agent_id = agent.get('id')
            agent_status = agent.get('status')
            if not agent_id or not agent_status:
                continue
            member_id = f'{run_id}:{agent_id}'
            if member_id not in spawned_members:
                spawned_members.add(member_id)
                seen_agent[member_id] = 'running'
                out.append(_team_event_envelope('team.member',
                                                session_id,
                                                {'type': 'team.member.spawned',
                                                 'team_id': team_id,
                                                 'member_id': member_id,
                                                 'name': agent.get('name') or agent_id,
                                                    'status': 'busy'}))
            if seen_agent.get(member_id) != agent_status:
                old_status = seen_agent.get(member_id, 'busy')
                seen_agent[member_id] = agent_status
                if agent_status != 'running':
                    out.append(_team_event_envelope('team.member',
                                                    session_id,
                                                    {'type': 'team.member.status_changed',
                                                     'team_id': team_id,
                                                     'member_id': member_id,
                                                     'old_status': old_status,
                                                     'new_status': agent_status}))
    return out


async def _consume_workflow_events(
        channel_id: str | None,
        session_id: str,
        workflow_handler: WorkflowMonitorHandler) -> None:
    """Consume workflow events in the background and broadcast them.

    TUI keeps the native ``workflow.updated`` stream. Every other channel (web)
    gets the events translated into ``team.member`` / ``team.task`` so the
    existing web frontend can render swarmflow workers/phases.
    """
    is_tui = _resolve_channel_id(channel_id) == 'tui'
    seen_phase: dict[str, str] = {}
    seen_agent: dict[str, str] = {}
    spawned_members: set[str] = set()
    try:
        logger.info('[TeamHelpers] workflow event loop started: channel_id=%s session_id=%s is_tui=%s',
                    _resolve_channel_id(channel_id), session_id, is_tui)
        async for event in workflow_handler.events():
            wf = event.get('workflow', {})
            logger.info(
                '[WF_DBG _consume_workflow_events] broadcast: channel_id=%s '
                'session_id=%s event_type=%s workflow_id=%s workflow_name=%s '
                'status=%s phases_count=%d agent_count=%d completed_agent_count=%d',
                _resolve_channel_id(channel_id),
                session_id,
                event.get('event_type', ''),
                wf.get('id', ''),
                wf.get('name', ''),
                wf.get('status', ''),
                len(wf.get('phases', [])),
                wf.get('agent_count', 0),
                wf.get('completed_agent_count', 0),
            )
            if is_tui:
                _broadcast_event(channel_id, session_id, event)
                wf_status = (wf.get('status') or '').strip()
                if wf_status in ('completed', 'failed', 'stopped'):
                    logger.info('[TeamHelpers] workflow terminal: channel_id=%s session_id=%s wf_status=%s',
                                _resolve_channel_id(channel_id), session_id, wf_status)
                    get_team_manager(channel_id).mark_workflow_completed(session_id)
                continue
            for team_ev in _workflow_updated_to_team_events(event, session_id, seen_phase, seen_agent, spawned_members):
                _persist_team_history_event(channel_id, session_id, team_ev)
                _broadcast_event(channel_id, session_id, team_ev)
            wf_status = (wf.get('status') or '').strip()
            if wf_status in ('completed', 'failed', 'stopped'):
                logger.info('[TeamHelpers] workflow terminal: channel_id=%s session_id=%s wf_status=%s',
                            _resolve_channel_id(channel_id), session_id, wf_status)
                get_team_manager(channel_id).mark_workflow_completed(session_id)
        logger.info('[TeamHelpers] workflow event loop ended: channel_id=%s session_id=%s',
                    _resolve_channel_id(channel_id), session_id)
    except asyncio.CancelledError:
        logger.debug('[TeamHelpers] workflow event loop cancelled: channel_id=%s session_id=%s',
                     _resolve_channel_id(channel_id), session_id)
        raise
    except Exception as exc:
        logger.error('[TeamHelpers] workflow event loop failed: channel_id=%s session_id=%s error=%s',
                     _resolve_channel_id(channel_id), session_id, exc)


def _persist_team_history_event(channel_id: str | None, session_id: str, event: dict[str, Any]) -> None:
    """Persist team monitor events required by team.history.get panel restore."""
    evt_type = event.get('event_type')
    if evt_type not in {'team.member', 'team.task'}:
        return
    payload = event.get('event')
    if not isinstance(payload, dict):
        return
    request_key = ''
    if evt_type == 'team.member':
        member_event_type = str(payload.get('type') or '').strip()
        if member_event_type not in {
            'team.member.spawned',
            'team.member.restarted',
            'team.member.status_changed',
                'team.member.shutdown'}:
            return
        member_id = str(payload.get('member_id') or '').strip()
        if not member_id:
            return
        if member_event_type == 'team.member.status_changed' and (not str(payload.get('new_status') or '').strip()):
            return
        request_key = f"{member_id}-{member_event_type.rsplit('.', 1)[-1]}"
    else:
        task_id = str(payload.get('task_id') or payload.get('id') or '').strip()
        if not task_id:
            return
        request_key = task_id
    timestamp = time.time()
    append_history_record(
        session_id=session_id,
        request_id=f'{evt_type}-{request_key}-{int(timestamp * 1000)}',
        channel_id=_resolve_channel_id(channel_id),
        role='assistant',
        content='',
        timestamp=timestamp,
        event_type=evt_type,
        extra={
            'session_id': session_id,
            'event': dict(payload)},
        mode='team')
