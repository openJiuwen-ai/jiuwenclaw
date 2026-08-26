# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Artifact detection rail for SkillTurbo tool calls.

复用 TaskExecutionRail 的共享产物检测逻辑（detect_artifact_paths /
fire_artifact_hook 等），在 SkillTurbo 工具调用后检测产物文件，同时
触发 IMAGE_ARTIFACT_POST_PROCESS 和 ARTIFACT_POST_PROCESS 扩展 hook
供扩展做原地后处理（如加水印），并发射 artifact.generated 事件到
session stream。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)

# 复用 jiuwenswarm TaskExecutionRail 的共享产物检测逻辑
from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
    ARTIFACT_DETECTION_TOOL_NAMES,
    detect_artifact_paths,
    filter_unhooked,
    fire_artifact_hook,
    mark_hooked,
    pop_tool_start_time,
    resolve_workspace_base,
)

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[SkillTurboArtifact]"


class SkillTurboArtifactRail:
    """在 SkillTurbo 工具调用后检测产物文件并发射 artifact.generated 事件。

    使用共享的 detect_artifact_paths 提取产物路径（含 invoke_tool 解包、
    按工具类型分流、mtime/工作区/去重过滤），同时触发
    IMAGE_ARTIFACT_POST_PROCESS 和 ARTIFACT_POST_PROCESS 扩展 hook
    （供原地后处理如加水印），再发射 artifact.generated 事件到
    session stream。
    """

    priority = 90

    def __init__(self, executor: Any) -> None:
        self._executor = executor
        # 已触发过产物 hook 的文件身份（路径+mtime_ns+size），防止重复后处理
        self._hooked_artifacts: set[tuple[str, int, int]] = set()
        # 工具调用开始时间（按 tool_call_id 记录，用于 mtime 校验）
        self._tool_start_times: dict[str, float] = {}

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name not in ARTIFACT_DETECTION_TOOL_NAMES:
            return
        tc = getattr(ctx.inputs, "tool_call", None)
        tool_call_id = str(getattr(tc, "id", "") or "")
        if tool_call_id:
            self._tool_start_times[tool_call_id] = time.time()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None or not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name not in ARTIFACT_DETECTION_TOOL_NAMES:
            return

        session_id = self._get_session_id(ctx.session)
        task_id = self._executor.current_task_id()
        detect_start = time.perf_counter()

        detection = detect_artifact_paths(
            ctx.inputs.tool_name,
            getattr(ctx.inputs, "tool_args", None),
            getattr(ctx.inputs, "tool_result", None),
            tool_start_time=pop_tool_start_time(
                self._tool_start_times, ctx
            ),
            workspace_base=resolve_workspace_base(),
        )

        # 去重：跳过已 hook 过且内容未变化的文件
        # （存在性过滤已在 detect_artifact_paths 统一出口处理）
        paths = filter_unhooked(detection.paths, self._hooked_artifacts)

        if not paths:
            return

        logger.info(
            "%s Detect start: tool=%s session_id=%s count=%d",
            _LOG_PREFIX,
            detection.tool_name,
            session_id,
            len(paths),
        )

        # 触发扩展 hook（发射事件前，供扩展原地后处理如加水印）
        fired = await fire_artifact_hook(
            session_id=session_id,
            tool_name=detection.tool_name,
            task_id=task_id,
            artifact_paths=paths,
            log_prefix=_LOG_PREFIX,
        )
        if fired:
            mark_hooked(paths, self._hooked_artifacts)

        # 发射 artifact.generated 事件到 session stream
        emitted = await self._emit_artifact_generated(
            ctx.session, paths, session_id, detection.tool_name, task_id
        )

        logger.info(
            "%s Detect done: tool=%s session_id=%s emitted=%s elapsed_ms=%d",
            _LOG_PREFIX,
            detection.tool_name,
            session_id,
            emitted,
            int((time.perf_counter() - detect_start) * 1000),
        )

    @staticmethod
    def _get_session_id(session: Any) -> str:
        try:
            return str(session.get_session_id() or "?")
        except Exception:
            return "?"

    async def _emit_artifact_generated(
        self,
        session: Any,
        paths: list[str],
        session_id: str,
        tool_name: str,
        task_id: str | None,
    ) -> bool:
        """构建 artifact.generated payload 并写入 session stream。"""
        artifacts_payload = []
        for path_str in paths:
            p = Path(path_str)
            try:
                size = p.stat().st_size if p.exists() else 0
            except OSError:
                size = 0
            artifacts_payload.append(
                {
                    "path": str(p),
                    "name": p.name,
                    "extension": p.suffix.lower(),
                    "size": size,
                    "exists": p.exists(),
                }
            )

        payload = {
            "artifacts": artifacts_payload,
            "tool_name": tool_name,
            "task_id": task_id,
            "timestamp": time.time(),
            "count": len(artifacts_payload),
        }

        try:
            await session.write_stream(
                OutputSchema(
                    type="artifact.generated",
                    index=0,
                    payload=payload,
                )
            )
            return True
        except Exception as exc:
            logger.warning(
                "%s emit failed: %s", _LOG_PREFIX, exc, exc_info=True
            )
            return False
