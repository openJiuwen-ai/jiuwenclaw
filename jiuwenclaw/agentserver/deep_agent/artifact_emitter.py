# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared artifact emission service for TaskExecutionRail and SubagentContextRail.

Provides unified artifact detection and emission logic to avoid code duplication
between main agent (TaskExecutionRail) and subagent (SubagentContextRail).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema

from jiuwenclaw.utils import logger

# Read-only tools return large bodies (file text, listings, search hits). Regex path
# extraction + sync stat on the event loop can block for minutes.
READ_ONLY_ARTIFACT_SKIP_TOOLS = frozenset({
    "read_file",
    "grep",
    "glob",
    "list_files",
    "skill_tool",
    "fetch_webpage",
    "mcp_petal_search",
    "free_search",
})


def should_skip_artifact_body_scan(tool_name: str | None) -> bool:
    """True when artifact detection must not scan tool_result body text."""
    return (tool_name or "").strip() in READ_ONLY_ARTIFACT_SKIP_TOOLS


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
        log_prefix: Log message prefix (default: "[ArtifactEmitter]")
    """
    session: Session
    tool_result: Any
    tool_name: str
    workspace_base: Any
    tool_start_time: float | None
    task_id: str | None
    subagent_id: str | None = None
    log_prefix: str = "[ArtifactEmitter]"


async def emit_artifact_generated(ctx: ArtifactEmitContext) -> bool:
    """Extract artifacts from tool result and emit artifact.generated event.
    
    This function handles the complete artifact detection pipeline:
    1. Extract paths from tool result (structured fields + regex patterns)
    2. Filter by timestamp (files modified after tool_start_time)
    3. Deduplicate by recent send cache
    4. Build payload and emit artifact.generated event
    
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
        _extract_artifact_paths_from_tool_result,
        _is_recently_sent,
        _mark_as_sent,
    )

    tool_name = (ctx.tool_name or "").strip()
    if should_skip_artifact_body_scan(tool_name):
        logger.debug(
            "%s Skip body scan for read-only tool=%s",
            ctx.log_prefix,
            tool_name,
        )
        return False

    session_id = ctx.session.get_session_id()

    logger.info("%s Processing tool session_id=%s tool='%s'", ctx.log_prefix, session_id, tool_name)
    
    # Step 1: Extract artifacts from tool result
    artifacts = _extract_artifact_paths_from_tool_result(
        ctx.tool_result, ctx.workspace_base, tool_start_time=ctx.tool_start_time
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
    
    if not new_artifacts:
        skipped_paths = [a.get("name", "") for a in existing_artifacts]
        logger.debug(
            "%s Skip sending session_id=%s: files=[%s]",
            ctx.log_prefix, session_id, ", ".join(skipped_paths)
        )
        return False
    
    # Step 4: Build payload
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
    
    # Step 5: Emit event
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