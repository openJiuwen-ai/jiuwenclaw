# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared artifact emission service for TaskExecutionRail and SubagentContextRail.

Provides unified artifact detection and emission logic to avoid code duplication
between main agent (TaskExecutionRail) and subagent (SubagentContextRail).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema

from jiuwenclaw.utils import logger

# Only write/exec tools that create files in tool_result need detection. skip it here.
ARTIFACT_DETECTION_ALLOWED_TOOLS = frozenset({
    "mcp_exec_command",
    "exec_command",
    "bash",
    "code",
    "send_file_to_user",
    "write_text_file",
    "write_file",
    "edit_file",
    "write",
    "text_to_image",
})

_SESSION_EMITTED_ARTIFACTS: dict[str, set[str]] = {}

_BODY_SCAN_TIMEOUT_S = 2.0


def _normalize_path_key(path: str) -> str:
    return str(path or "").replace("\\", "/").lower()


def _mark_session_emitted(session_id: str, paths: list[str]) -> None:
    if not session_id or not paths:
        return
    emitted = _SESSION_EMITTED_ARTIFACTS.setdefault(session_id, set())
    for path in paths:
        key = _normalize_path_key(path)
        if key:
            emitted.add(key)


def _already_emitted_in_session(session_id: str, path: str) -> bool:
    if not session_id:
        return False
    emitted = _SESSION_EMITTED_ARTIFACTS.get(session_id)
    if not emitted:
        return False
    return _normalize_path_key(path) in emitted


def should_detect_artifacts(tool_name: str | None) -> bool:
    """True when artifact detection should run for this tool."""
    return (tool_name or "").strip() in ARTIFACT_DETECTION_ALLOWED_TOOLS


def _coerce_send_file_path_list(raw: Any) -> list[str]:
    """Normalize abs_file_path_list from tool_args (detection-only, not shared with send_file)."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                raw = parsed
            elif isinstance(parsed, str):
                raw = [parsed]
            else:
                raw = [raw]
        except (TypeError, ValueError):
            raw = [raw]

    if not isinstance(raw, list):
        raw = [str(raw)]

    paths: list[str] = []
    for item in raw:
        path = str(item).strip()
        if path:
            paths.append(path)
    return paths


def _parse_send_file_paths_from_tool_args(tool_args: Any) -> list[str]:
    """Extract abs_file_path_list from ToolCallInputs.tool_args."""
    if tool_args is None:
        return []

    payload: Any = tool_args
    if isinstance(tool_args, str):
        try:
            payload = json.loads(tool_args)
        except (TypeError, ValueError):
            logger.info(
                "%s send_file tool_args JSON parse failed, len=%d",
                "[ArtifactEmitter]",
                len(tool_args),
            )
            return []

    raw: Any = None
    if isinstance(payload, dict):
        raw = payload.get("abs_file_path_list")
    elif isinstance(payload, list):
        raw = payload
    else:
        logger.debug(
            "[ArtifactEmitter] send_file tool_args unexpected type: %s",
            type(payload).__name__,
        )
        return []

    if raw is None:
        return []

    return _coerce_send_file_path_list(raw)


def _artifact_log_summaries(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": a.get("name", ""),
            "path": a.get("path", ""),
            "size": a.get("size"),
            "extension": a.get("extension", ""),
        }
        for a in artifacts
    ]


@dataclass
class ArtifactEmitContext:
    """Context for artifact emission.
    
    Encapsulates all parameters needed for emit_artifact_generated to comply
    with G.FNM.03 (function parameter limit).
    
    Attributes:
        session: Session to write stream events
        tool_result: Tool execution result (string, dict, or object)
        tool_name: Tool name for payload
        workspace_base: Workspace base path for validation
        tool_start_time: Tool start timestamp for file mtime validation
        task_id: Task ID for correlation
        subagent_id: Optional subagent ID (for subagent context)
        tool_args: Raw tool arguments (send_file_to_user: abs_file_path_list)
        log_prefix: Log message prefix (default: "[ArtifactEmitter]")
    """
    session: Session
    tool_result: Any
    tool_name: str
    workspace_base: Any
    tool_start_time: float | None
    task_id: str | None
    subagent_id: str | None = None
    tool_args: Any = None
    log_prefix: str = "[ArtifactEmitter]"


def _refresh_artifact_sizes(
    artifacts: list[dict[str, Any]],
    log_prefix: str = "[ArtifactEmitter]",
) -> None:
    """Re-read file sizes after extension post-processing may have rewritten files."""
    for artifact in artifacts:
        path = str(artifact.get("path") or "").strip()
        if not path:
            logger.warning(
                "%s refresh artifact size skipped: empty path artifact=%s",
                log_prefix,
                artifact,
            )
            continue
        try:
            artifact["size"] = Path(path).stat().st_size
        except OSError as exc:
            logger.warning(
                "%s refresh artifact size failed path=%s error=%s",
                log_prefix,
                path,
                exc,
            )


async def _trigger_artifact_post_process_hook(
    *,
    session_id: str,
    tool_name: str,
    task_id: str | None,
    subagent_id: str | None,
    artifacts: list[dict[str, Any]],
    log_prefix: str,
) -> None:
    """Invoke ``ARTIFACT_POST_PROCESS`` extension hooks before ``artifact.generated``."""
    if not artifacts:
        return

    try:
        from jiuwenclaw.extensions.registry import ExtensionRegistry
        from jiuwenclaw.schema import AgentServerHookEvents
        from jiuwenclaw.schema.hooks_context import ArtifactPostProcessHookContext
    except ImportError as exc:
        logger.warning(
            "%s Skip artifact post-process hook import failed: %s",
            log_prefix,
            exc,
        )
        return

    artifact_paths = [
        str(item.get("path") or "").strip()
        for item in artifacts
        if str(item.get("path") or "").strip()
    ]
    hook_ctx = ArtifactPostProcessHookContext(
        session_id=session_id,
        tool_name=tool_name,
        task_id=task_id,
        subagent_id=subagent_id,
        artifact_paths=artifact_paths,
    )
    try:
        await ExtensionRegistry.get_instance().trigger(
            AgentServerHookEvents.ARTIFACT_POST_PROCESS,
            hook_ctx,
        )
    except RuntimeError:
        logger.warning(
            "%s Skip artifact post-process hook: ExtensionRegistry not initialized",
            log_prefix,
        )
        return
    except Exception as exc:
        logger.warning(
            "%s artifact post-process hook failed session_id=%s tool=%s error=%s",
            log_prefix,
            session_id,
            tool_name,
            exc,
        )
        return

    # Sync back: if hook renamed files via ctx.artifact_paths, update artifacts
    new_paths = {
        old: new
        for old, new in zip(artifact_paths, hook_ctx.artifact_paths)
        if old != new and new
    }
    if new_paths:
        for item in artifacts:
            old_path = str(item.get("path") or "").strip()
            if old_path in new_paths:
                new_path = new_paths[old_path]
                item["path"] = new_path
                item["name"] = Path(new_path).name
                logger.info(
                    "%s artifact path updated by hook: %s -> %s",
                    log_prefix,
                    Path(old_path).name,
                    Path(new_path).name,
                )

    _refresh_artifact_sizes(artifacts, log_prefix=log_prefix)
    logger.info(
        "%s artifact post-process hook done session_id=%s tool=%s count=%d",
        log_prefix,
        session_id,
        tool_name,
        len(artifacts),
    )


async def _extract_artifacts_async(
    ctx: ArtifactEmitContext,
    *,
    scan_body_text: bool = True,
    skip_mtime_check: bool = False,
) -> list[dict[str, Any]]:
    """Run artifact path extraction off the event loop with a wall-clock timeout."""
    from jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail import (
        _extract_artifact_paths_from_tool_result,
    )

    cancel_event = threading.Event()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _extract_artifact_paths_from_tool_result,
                ctx.tool_result,
                ctx.workspace_base,
                ctx.tool_start_time,
                scan_body_text=scan_body_text,
                skip_mtime_check=skip_mtime_check,
                cancel_event=cancel_event,
            ),
            timeout=_BODY_SCAN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        cancel_event.set()
        logger.warning(
            "%s Body scan timed out (%.1fs), skipping artifact extraction",
            ctx.log_prefix,
            _BODY_SCAN_TIMEOUT_S,
        )
        return []
    except Exception as exc:
        logger.exception(
            "%s Body scan failed: %s",
            ctx.log_prefix,
            exc,
        )
        return []


async def emit_artifact_generated(ctx: ArtifactEmitContext) -> bool:
    """Extract artifacts from tool result and emit artifact.generated event.
    
    This function handles the complete artifact detection pipeline:
    1. Extract paths from tool result (structured fields + regex patterns)
    2. Filter by timestamp (files modified after tool_start_time)
    3. Deduplicate by recent send cache
    4. Trigger ARTIFACT_POST_PROCESS extension hooks (in-place file post-processing)
    5. Build payload and emit artifact.generated event
    
    Args:
        ctx: ArtifactEmitContext containing all emission parameters
        
    Returns:
        True if artifacts were emitted, False otherwise
        
    Note:
        Imports _extract_artifact_paths_from_tool_result, _is_recently_sent, 
        _mark_as_sent from task_execution_rail to avoid circular dependency.
    """
    # Import here to avoid circular dependency at module load time
    from jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail import (
        _build_artifacts_from_explicit_paths,
        _is_recently_sent,
        _mark_as_sent,
    )

    tool_name = (ctx.tool_name or "").strip()
    if not should_detect_artifacts(tool_name):
        logger.info(
            "%s Skip artifact detection: tool=%s not in whitelist",
            ctx.log_prefix,
            tool_name,
        )
        return False

    session_id = ctx.session.get_session_id()

    logger.info("%s Processing tool session_id=%s tool='%s'", ctx.log_prefix, session_id, tool_name)

    # Step 1: Extract artifacts
    extract_source = "tool_result"
    candidate_paths: list[str] = []

    if tool_name == "send_file_to_user":
        candidate_paths = _parse_send_file_paths_from_tool_args(ctx.tool_args)
        if candidate_paths:
            extract_source = "send_file:tool_args+explicit_paths"
            artifacts = _build_artifacts_from_explicit_paths(
                candidate_paths,
                ctx.workspace_base,
                tool_start_time=ctx.tool_start_time,
                skip_mtime_check=True,
            )
        else:
            extract_source = "send_file:regex_fallback"
            artifacts = await _extract_artifacts_async(
                ctx,
                scan_body_text=True,
                skip_mtime_check=True,
            )
    else:
        artifacts = await _extract_artifacts_async(ctx)

    logger.info(
        "%s 产物检测 session_id=%s tool=%s source=%s candidate_paths=%s count=%d artifacts=%s",
        ctx.log_prefix,
        session_id,
        tool_name,
        extract_source,
        candidate_paths,
        len(artifacts),
        _artifact_log_summaries(artifacts),
    )

    if not artifacts:
        return False
    
    # Step 2: Filter by existence
    existing_artifacts = [a for a in artifacts if a.get("exists")]
    
    if not existing_artifacts:
        return False
    
    # Step 3: Deduplication - filter recently sent artifacts
    new_artifacts = [
        a for a in existing_artifacts 
        if not _is_recently_sent(a.get("path", ""))
    ]

    # send_file_to_user is a delivery-stage fallback; avoid re-emitting files
    # that were already emitted earlier in this same session.
    if tool_name == "send_file_to_user":
        new_artifacts = [
            a for a in new_artifacts
            if not _already_emitted_in_session(session_id, a.get("path", ""))
        ]
    
    if not new_artifacts:
        skipped_paths = [a.get("name", "") for a in existing_artifacts]
        logger.debug(
            "%s Skip sending session_id=%s: files=[%s]",
            ctx.log_prefix, session_id, ", ".join(skipped_paths)
        )
        return False

    await _trigger_artifact_post_process_hook(
        session_id=session_id,
        tool_name=tool_name,
        task_id=ctx.task_id,
        subagent_id=ctx.subagent_id,
        artifacts=new_artifacts,
        log_prefix=ctx.log_prefix,
    )
    
    # Step 5: Build payload
    artifacts_payload = [
        {
            "path": a.get("path", ""),
            "name": a.get("name", ""),
            "extension": a.get("extension", ""),
            "size": a.get("size"),
            "exists": True,
        }
        for a in new_artifacts
    ]
    
    # Step 6: Emit event
    payload = {
        "artifacts": artifacts_payload,
        "tool_name": ctx.tool_name,
        "task_id": ctx.task_id,
        "timestamp": time.time(),
        "count": len(artifacts_payload),
    }
    if ctx.subagent_id:
        payload["subagent_id"] = ctx.subagent_id
    
    try:
        await ctx.session.write_stream(
            OutputSchema(type="artifact.generated", index=0, payload=payload)
        )
        
        # Mark as sent for deduplication
        for artifact in new_artifacts:
            _mark_as_sent(artifact.get("path", ""))
        _mark_session_emitted(
            session_id,
            [artifact.get("path", "") for artifact in new_artifacts],
        )
        
        logger.info(
            "%s 消息发送成功 session_id=%s tool=%s task_id=%s count=%d",
            ctx.log_prefix, session_id, ctx.tool_name, ctx.task_id or "N/A", len(new_artifacts)
        )
        
        for artifact in new_artifacts:
            logger.info(
                "%s 产物 session_id=%s name='%s' path='%s' size=%d bytes extension='%s'",
                ctx.log_prefix, session_id,
                artifact.get("name", ""),
                artifact.get("path", ""),
                artifact.get("size", 0),
                artifact.get("extension", "")
            )
        
        return True
        
    except Exception as e:
        logger.warning(
            "%s 消息发送失败 session_id=%s tool=%s error=%s",
            ctx.log_prefix, session_id, ctx.tool_name, str(e)
        )
        return False