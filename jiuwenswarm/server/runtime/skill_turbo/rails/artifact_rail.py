# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Artifact detection rail for SkillTurbo tool calls.

复用 jiuwenswarm TaskExecutionRail 的路径提取函数，在 SkillTurbo 工具调用后
检测产物文件并发射 artifact.generated 事件到 session stream。
同时触发 ARTIFACT_POST_PROCESS 扩展 hook 供扩展做原地后处理。

与源仓库 artifact_emitter 相比的简化点（方案 B）：
- 无 mtime 时间戳校验（可能误报旧文件为本次产物）
- 无全局/会话级去重缓存（可能重复发射同一产物）
- 使用 jiuwenswarm 现有的提取函数（返回 list[str] 而非带 size/extension 的 dict）
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs

# 复用 jiuwenswarm TaskExecutionRail 的路径提取函数
from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
    _extract_file_paths_from_write_tool,
    _extract_image_paths_from_tool_result,
    _extract_raw_paths_from_result_text,
    _parse_tool_args_payload,
)

logger = logging.getLogger(__name__)

# 产物文件扩展名白名单（覆盖 PPT skill 产物类型）
_ARTIFACT_EXTENSIONS = frozenset({
    # 图像
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    # 文档
    ".pptx", ".ppt", ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    # 压缩包
    ".zip", ".tar", ".gz",
})

# 触发产物检测的工具白名单（与源仓库 ARTIFACT_DETECTION_ALLOWED_TOOLS 对齐）
_ARTIFACT_DETECT_TOOLS = frozenset({
    "send_file_to_user",
    "write_file",
    "edit_file",
    "write",
    "write_text_file",
    "text_to_image",
    "generate_image",
    "bash",
    "exec_command",
    "mcp_exec_command",
    "code",
})

# write 类工具集合
_WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "write", "write_text_file",
})


class SkillTurboArtifactRail:
    """在 SkillTurbo 工具调用后检测产物文件并发射 artifact.generated 事件。

    使用 jiuwenswarm TaskExecutionRail 的路径提取函数提取产物路径，
    构建 artifact.generated payload 并写入 session stream，
    同时触发 ARTIFACT_POST_PROCESS 扩展 hook。
    """

    priority = 90

    def __init__(self, executor: Any) -> None:
        self._executor = executor

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        setattr(ctx, "_tool_start_time", time.time())

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None or not isinstance(ctx.inputs, ToolCallInputs):
            return

        tool_name = ctx.inputs.tool_name
        if tool_name not in _ARTIFACT_DETECT_TOOLS:
            return

        tool_result = getattr(ctx.inputs, "tool_result", None)
        tool_args = getattr(ctx.inputs, "tool_args", None)

        paths = self._extract_artifact_paths(tool_name, tool_args, tool_result)
        if not paths:
            return

        # 过滤不存在的文件
        existing_paths = [p for p in paths if Path(p).exists()]
        if not existing_paths:
            return

        session_id = self._get_session_id(ctx.session)
        task_id = self._executor.current_task_id()
        detect_start = time.perf_counter()

        logger.info(
            "[SkillTurboArtifact] Detect start: tool=%s session_id=%s",
            tool_name,
            session_id,
        )

        # 触发 ARTIFACT_POST_PROCESS 扩展 hook（发射前）
        await self._trigger_artifact_hook(
            ctx, existing_paths, tool_name, session_id, task_id
        )

        # 发射 artifact.generated 事件到 session stream
        emitted = await self._emit_artifact_generated(
            ctx.session, existing_paths, session_id, tool_name, task_id
        )

        logger.info(
            "[SkillTurboArtifact] Detect done: tool=%s session_id=%s "
            "emitted=%s elapsed_ms=%d",
            tool_name,
            session_id,
            emitted,
            int((time.perf_counter() - detect_start) * 1000),
        )

    @staticmethod
    def _extract_artifact_paths(
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
    ) -> list[str]:
        """根据工具类型提取产物路径，返回去重后的路径列表。"""
        paths: list[str] = []

        # 1. send_file_to_user: 从 tool_args 提取显式路径
        if tool_name == "send_file_to_user":
            payload = _parse_tool_args_payload(tool_args)
            for key in ("path", "file_path", "file", "abs_file_path"):
                value = str(payload.get(key) or "").strip()
                if value:
                    paths.append(value)
            # abs_file_path_list（列表形式）
            raw_list = payload.get("abs_file_path_list")
            if isinstance(raw_list, list):
                paths.extend(str(p).strip() for p in raw_list if str(p).strip())

        # 2. 图像工具: 使用专用提取函数
        elif tool_name in ("generate_image", "text_to_image"):
            paths = _extract_image_paths_from_tool_result(tool_result)

        # 3. write 类工具: 使用专用提取函数
        elif tool_name in _WRITE_TOOLS:
            paths = _extract_file_paths_from_write_tool(
                tool_name, tool_args, tool_result
            )

        # 4. 其他工具（bash/code 等）: 通用正则提取 + 扩展名白名单过滤
        else:
            raw_paths = _extract_raw_paths_from_result_text(tool_result)
            paths = [
                p
                for p in raw_paths
                if Path(p).suffix.lower() in _ARTIFACT_EXTENSIONS
            ]

        # 去重（保持顺序）
        seen: set[str] = set()
        unique: list[str] = []
        for p in paths:
            identity = p.replace("\\", "/").lower()
            if identity not in seen:
                seen.add(identity)
                unique.append(p)
        return unique

    @staticmethod
    def _get_session_id(session: Any) -> str:
        try:
            return str(session.get_session_id() or "?")
        except Exception:
            return "?"

    async def _trigger_artifact_hook(
        self,
        ctx: AgentCallbackContext,
        paths: list[str],
        tool_name: str,
        session_id: str,
        task_id: str | None,
    ) -> None:
        """触发 ARTIFACT_POST_PROCESS 扩展 hook，供扩展做原地后处理。"""
        try:
            from jiuwenswarm.extensions.registry import ExtensionRegistry
            from jiuwenswarm.extensions.hook_event import AgentServerHookEvents
            from jiuwenswarm.extensions.hooks_context import (
                ArtifactPostProcessHookContext,
            )
        except ImportError as exc:
            logger.debug(
                "[SkillTurboArtifact] skip hook, import failed: %s", exc
            )
            return

        hook_ctx = ArtifactPostProcessHookContext(
            session_id=session_id,
            tool_name=tool_name,
            task_id=task_id,
            artifact_paths=paths,
        )
        try:
            await ExtensionRegistry.get_instance().trigger(
                AgentServerHookEvents.ARTIFACT_POST_PROCESS,
                hook_ctx,
            )
        except RuntimeError:
            # ExtensionRegistry 未初始化，静默跳过
            pass
        except Exception as exc:
            logger.warning(
                "[SkillTurboArtifact] hook failed: %s", exc, exc_info=True
            )

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
                "[SkillTurboArtifact] emit failed: %s", exc, exc_info=True
            )
            return False
