# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuClawContextEngineeringRail — Extend CE Rail with offload/context hints.

Subclasses the SDK ContextEngineeringRail to inject an independent 'offload'
PromptSection, replacing the old approach of appending to the 'context' section.

F-REDUCE: Added `minimal` mode for subagent — skips tools/context injection,
only keeps context compression processors and offload hint.
"""
from __future__ import annotations

import re
from logging import getLogger
from typing import Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections.context import (
    _build_context_content,
    _read_context_file,
    _read_daily_memory,
    build_tools_section,
)
from openjiuwen.harness.prompts.sections.workspace import (
    build_workspace_section as _build_workspace,
)
from openjiuwen.harness.prompts.workspace_content.workspace_header import (
    CONTEXT_FILES,
    CONTEXT_FILE_TITLES,
    CONTEXT_HEADER,
    DAILY_MEMORY_TITLE,
)
from openjiuwen.harness.rails.context_engineering_rail import ContextEngineeringRail

logger = getLogger(__name__)

# Per-request overrides use the same slot/order as workspace files, with relay-provided headings.
_OVERRIDE_HEADING_SOUL = "## SOUL"
_OVERRIDE_HEADING_IDENTITY = "## IDENTITY"

# Relay-claw empty personality becomes a lone label line in system prompt.
_SOUL_LABEL_ONLY = re.compile(r"^性格[：:]\s*$")
_ROLE_LABEL_ONLY = re.compile(r"^角色[：:]\s*$")


def normalize_soul_override(value: Optional[str]) -> Optional[str]:
    """Treat whitespace-only or label-only soul (e.g. ``性格：``) as absent."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _SOUL_LABEL_ONLY.fullmatch(text):
        return None
    return text


def normalize_identify_override(value: Optional[str]) -> Optional[str]:
    """Treat whitespace-only or lone ``角色：`` identify line as absent."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _ROLE_LABEL_ONLY.fullmatch(text):
        return None
    return text


class JiuClawContextEngineeringRail(ContextEngineeringRail):
    """扩展 CE Rail，注入独立的 offload/上下文压缩 section。

    Args:
        processors: 透传给父类的用户级 processor 配置（增量合并/替换预置）。
        preset: 是否启用预置的上下文压缩 processor 配置。
        minimal: F-REDUCE — 是否跳过 tools/context section 注入（用于 subagent）。
        session_memory: SessionMemoryConfig 配置（传递给父类）。
    """

    OFFLOAD_HINT_CN = (
        "# 上下文压缩\n\n"
        "你的上下文在过长时会被自动压缩，"
        "并标记为[OFFLOAD: handle=<id>, type=<type>]。\n\n"
        "如果你认为需要读取隐藏的内容，"
        "可随时调用 reload_original_context_messages 工具。\n\n"
        "历史 QA 块：会话级目录见 [QA_BLOCK_CATALOG]；"
        "展开某一 qa_id 块内的概览与逐 message 目录请用 load_qa_index(qa_id)；"
        "目录或窗内 [[OFFLOAD: handle=…]] 句柄用 "
        'reload_original_context_messages(handle, "filesystem") 取回原文。\n\n'
        "请勿猜测或编造缺失的内容。\n\n"
        '存储类型："in_memory"（会话缓存）、"filesystem"（QA 块 offload 落盘）'
    )

    OFFLOAD_HINT_EN = (
        "# Context Compression\n\n"
        "Your context will be automatically compressed when it becomes too long "
        "and marked with [OFFLOAD: handle=<id>, type=<type>].\n\n"
        'Call reload_original_context_messages(offload_handle="<id>", '
        'offload_type="<type>"), using the exact values from the marker.\n\n'
        "For historical QA blocks: see [QA_BLOCK_CATALOG] for session-level index; "
        "call load_qa_index(qa_id) to expand a block's overview and per-message catalog; "
        'use reload_original_context_messages(handle, "filesystem") for '
        "[[OFFLOAD: handle=…]] in QA catalogs.\n\n"
        "Do not guess or fabricate missing content.\n\n"
        'Storage types: "in_memory" (session cache), "filesystem" (QA block offload)'
    )

    def __init__(
        self,
        processors=None,
        preset: bool = True,
        minimal: bool = False,
        session_memory=None,
    ) -> None:
        """Initialize with optional minimal mode for subagent."""
        super().__init__(
            processors=processors,
            preset=preset,
            session_memory=session_memory,
        )
        self._minimal = minimal
        self._skip_context_files: set[str] = getattr(self, "_skip_context_files", None) or set()
        self._request_identify: Optional[str] = None
        self._request_soul: Optional[str] = None

    def set_skip_context_files(self, files: set[str]) -> None:
        """Legacy hook; prefer set_request_identify / set_request_soul for soul/identity."""
        self._skip_context_files = set(files)

    def set_request_identify(self, identify: Optional[str]) -> None:
        self._request_identify = normalize_identify_override(identify)

    def set_request_soul(self, soul: Optional[str]) -> None:
        self._request_soul = normalize_soul_override(soul)

    def uninit(self, agent) -> None:
        self._skip_context_files = set()
        self._request_identify = None
        self._request_soul = None
        super().uninit(agent)

    async def _build_context_content_with_overrides(self, lang: str) -> str:
        """Build context section; replace SOUL.md / IDENTITY.md slots with request overrides."""
        if self.workspace is None or self.sys_operation is None:
            return ""

        soul_override = self._request_soul
        identify_override = self._request_identify

        if not soul_override and not identify_override:
            return await _build_context_content(
                self.sys_operation,
                self.workspace,
                lang,
            )

        header = CONTEXT_HEADER.get(lang, CONTEXT_HEADER["cn"])
        titles = CONTEXT_FILE_TITLES.get(lang, CONTEXT_FILE_TITLES["cn"])
        daily_title_tpl = DAILY_MEMORY_TITLE.get(lang, DAILY_MEMORY_TITLE["cn"])
        parts = [header]

        for file_key in CONTEXT_FILES:
            if file_key in self._skip_context_files:
                continue
            if file_key == "SOUL.md" and soul_override:
                parts.append(f"{_OVERRIDE_HEADING_SOUL}\n\n{soul_override}\n\n")
                continue
            if file_key == "IDENTITY.md" and identify_override:
                parts.append(f"{_OVERRIDE_HEADING_IDENTITY}\n\n{identify_override}\n\n")
                continue
            content = await _read_context_file(self.sys_operation, self.workspace, file_key)
            if content is None:
                continue
            title = titles.get(file_key, f"## {file_key}")
            parts.append(f"{title}\n\n{content}\n\n")

        if lang == "cn":
            parts.append("[以下文件仅在有实际内容时注入，空文件跳过]\n\n")
        else:
            parts.append(
                "[The following files are injected only when they contain real content; "
                "empty files are skipped]\n\n"
            )

        daily_content = await _read_daily_memory(self.sys_operation, self.workspace)
        if daily_content:
            from openjiuwen.harness.prompts.sections.context import _format_date

            date = _format_date("Asia/Shanghai")
            title = daily_title_tpl.format(date=date)
            parts.append(f"{title}\n\n{daily_content}\n\n")

        return "".join(parts)

    async def _build_context_section_with_overrides(self, lang: str) -> Optional[PromptSection]:
        if self.workspace is None:
            return None
        content = await self._build_context_content_with_overrides(lang)
        return PromptSection(
            name="context",
            content={lang: content},
            priority=80,
        )

    async def _inject_workspace_context_tools(self, ctx: AgentCallbackContext) -> None:
        """Inject workspace/tools/context sections."""
        self._refresh_task_state_runtime(ctx)
        if self.system_prompt_builder is None:
            return

        workspace = self.workspace
        if workspace is None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
            return

        lang = self.system_prompt_builder.language
        workspace_section = await _build_workspace(
            self.sys_operation,
            workspace,
            lang,
        )
        tools_section = build_tools_section(self._ability_manager, lang)
        context_section = await self._build_context_section_with_overrides(lang)

        if workspace_section is not None:
            self.system_prompt_builder.add_section(workspace_section)
        else:
            self.system_prompt_builder.remove_section("workspace")

        if tools_section is not None:
            self.system_prompt_builder.add_section(tools_section)
        else:
            self.system_prompt_builder.remove_section("tools")

        if context_section is not None:
            self.system_prompt_builder.add_section(context_section)
            rendered = context_section.render(lang)
            logger.info(
                "[JiuClawContextEngineeringRail] context injected: "
                "soul_override=%s identify_override=%s has_heading_soul=%s has_heading_identity=%s "
                "has_workspace_soul_file=%s has_soul_file_body=%s preview=%r",
                bool(self._request_soul),
                bool(self._request_identify),
                _OVERRIDE_HEADING_SOUL in rendered,
                _OVERRIDE_HEADING_IDENTITY in rendered,
                "## SOUL.md" in rendered,
                "# 灵魂" in rendered,
                rendered[:320],
            )
        else:
            self.system_prompt_builder.remove_section("context")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject workspace + context (if not minimal), then offload section."""
        if not self._minimal:
            await self._inject_workspace_context_tools(ctx)

        if not self.system_prompt_builder:
            return

        lang = self.system_prompt_builder.language or "cn"
        hint = self.OFFLOAD_HINT_CN if lang == "cn" else self.OFFLOAD_HINT_EN

        self.system_prompt_builder.add_section(
            PromptSection(
                name="offload",
                content={lang: hint},
                priority=90,
            )
        )
