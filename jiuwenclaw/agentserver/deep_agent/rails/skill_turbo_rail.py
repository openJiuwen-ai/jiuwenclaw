# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurboRail — turbo 加速面渐进式注入 + skill_complete 释放.

职责：
1. [层1 常驻] before_model_call 注入 turbo 面 catalog（name + description）
2. [层2 按需] after_tool_call 检测 skill_turbo_tool activate 成功 → 钉入 SKILL_TURBO.md 正文
3. [释放] after_tool_call 检测 skill_complete → 释放 turbo 正文 pin

设计要点（§5.1/§5.2/§5.7）：
- 层1 catalog 幂等注入（不碰正文）
- 层2 钉入复用 active_skill_body 机制（record_active_skill_body）
- skill_complete 释放用 turbo_name = f"{skill_name}_turbo" 单一真值源
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.prompt_builder import PromptPriority
from jiuwenclaw.utils import logger

__all__ = ["SkillTurboRail"]


_TURBO_CATALOG_SECTION_NAME = "skill_turbo_catalog"


def _build_turbo_catalog_text(faces: list[Any]) -> str:
    """构建层1 catalog 文本（turbo 面 name + description）."""
    if not faces:
        return ""

    lines = ["## Skill Turbo 加速面清单\n"]
    lines.append("以下 skill 有 turbo 加速面，可用 `skill_turbo_tool` 在线执行：\n")
    for face in faces:
        lines.append(f"- **{face.turbo_name}**（源 skill: {face.source_skill}）: {face.description}")
    lines.append(
        "\n使用方式：先 `skill_turbo_tool(skill_name=..., scenario=..., plan_name=None)` activate，"
        "获取 schema 概览后规划 todo，再逐节点 `skill_turbo_tool(skill_name, scenario, plan_name, inputs)` execute。"
    )
    return "\n".join(lines)


class SkillTurboRail(DeepAgentRail):
    """turbo 加速面渐进式注入 rail.

    priority = 9（在 SkillProtocolPromptRail priority=8 之后）
    """

    priority = 9

    def __init__(self) -> None:
        self._system_prompt_builder: Any = None

    def init(self, agent: Any) -> None:
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        builder = getattr(agent, "system_prompt_builder", None)
        if builder is not None:
            builder.remove_section(_TURBO_CATALOG_SECTION_NAME)
        self._system_prompt_builder = None

    def _resolve_language(self) -> str:
        builder = self._system_prompt_builder
        if builder is not None:
            lang = getattr(builder, "language", None)
            if lang:
                return lang
        return "cn"

    def _resolve_priority(self) -> int:
        return PromptPriority.SYSTEM_HIGH

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """[层1 常驻] 注入 turbo 面 catalog（幂等，不碰正文）."""
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is None:
            return

        # 发现所有 turbo 面
        try:
            from jiuwenclaw.agentserver.skill_turbo.online.schema_loader import (
                discover_all_turbo_faces,
            )

            faces = discover_all_turbo_faces()
        except Exception as exc:
            logger.debug("[SkillTurboRail] discover turbo faces failed: %s", exc)
            return

        if not faces:
            return

        text = _build_turbo_catalog_text(faces)
        if not text:
            return

        language = self._resolve_language()
        section = PromptSection(
            name=_TURBO_CATALOG_SECTION_NAME,
            content={language: text},
            priority=self._resolve_priority(),
        )
        builder.add_section(section)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """[层2 按需] activate 钉入正文 + skill_complete 释放."""
        tool_name = ctx.inputs.tool_name
        tool_args = ctx.inputs.tool_args or {}
        tool_result = ctx.inputs.tool_result

        if tool_name == "skill_turbo_tool":
            await self._handle_skill_turbo_tool(ctx, tool_args, tool_result)
        elif tool_name == "skill_complete":
            await self._handle_skill_complete(ctx, tool_args)

    async def _handle_skill_turbo_tool(
        self,
        ctx: AgentCallbackContext,
        tool_args: dict[str, Any],
        tool_result: Any,
    ) -> None:
        """检测 activate 成功 → 钉入 SKILL_TURBO.md 正文（层2）."""
        # 只处理 activate（plan_name is None）
        if tool_args.get("plan_name") is not None:
            return

        # 检查 tool_result 是否成功
        result_dict = _extract_result_dict(tool_result)
        if not result_dict or not result_dict.get("success"):
            return

        skill_name = tool_args.get("skill_name", "")
        if not skill_name:
            return

        try:
            from jiuwenclaw.agentserver.skill_turbo.online.skill_turbo_tool import (
                resolve_turbo_face_for_skill,
            )
            from openjiuwen.core.context_engine.active_skill_bodies import (
                record_active_skill_body,
            )

            face = resolve_turbo_face_for_skill(skill_name)
            from jiuwenclaw.agentserver.skill_turbo.online.schema_loader import (
                load_skill_turbo_body,
            )

            body = load_skill_turbo_body(face.turbo_dir)

            # 钉入 turbo 正文（复用 active_skill_body 机制）
            session = _get_session(ctx)
            if session is not None:
                # 构造一个模拟的 tool_message + result 供 record_active_skill_body 使用
                # turbo_name 作为 skill_name 钉入
                _record_turbo_body(session, face.turbo_name, body)
                logger.info(
                    "[SkillTurboRail] pinned SKILL_TURBO.md body for %s",
                    face.turbo_name,
                )
        except Exception as exc:
            logger.warning("[SkillTurboRail] pin turbo body failed: %s", exc)

    async def _handle_skill_complete(
        self,
        ctx: AgentCallbackContext,
        tool_args: dict[str, Any],
    ) -> None:
        """检测 skill_complete → 释放 turbo 正文 pin."""
        skill_name = tool_args.get("skill_name", "")
        if not skill_name:
            return

        turbo_name = f"{skill_name}_turbo"  # 单一真值源（与 activate 钉入一致）

        try:
            from openjiuwen.core.context_engine.active_skill_bodies import (
                unregister_active_skill_body,
            )

            session = _get_session(ctx)
            if session is not None:
                count = unregister_active_skill_body(session, turbo_name)
                if count > 0:
                    logger.info(
                        "[SkillTurboRail] released turbo body for %s (%d entries)",
                        turbo_name, count,
                    )
        except Exception as exc:
            logger.warning("[SkillTurboRail] release turbo body failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────


def _extract_result_dict(tool_result: Any) -> dict[str, Any] | None:
    """从 tool_result 提取 dict（兼容多种返回类型）."""
    if isinstance(tool_result, dict):
        return tool_result
    # OutputSchema / pydantic model
    data = getattr(tool_result, "data", None)
    if isinstance(data, dict):
        return data
    payload = getattr(tool_result, "payload", None)
    if isinstance(payload, dict):
        return payload
    return None


def _get_session(ctx: AgentCallbackContext) -> Any:
    """从 ctx 获取 session."""
    session = getattr(ctx, "session", None)
    if session is not None:
        return session
    context = getattr(ctx, "context", None)
    if context is not None:
        session = getattr(context, "_session_ref", None)
        if session is not None:
            return session
    return None


def _record_turbo_body(session: Any, turbo_name: str, body: str) -> None:
    """将 turbo 正文钉入 active_skill_bodies.

    直接操作 session state（record_active_skill_body 需要 tool_message 格式，
    这里简化为直接写入 state key）.
    """
    try:
        from openjiuwen.core.context_engine.active_skill_bodies import (
            ACTIVE_SKILL_BODIES_STATE_KEY,
        )

        state = session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY) or {}
        # 用 turbo_name 作为 key
        state[turbo_name] = {
            "skill_name": turbo_name,
            "relative_file_path": "SKILL_TURBO.md",
            "body": body,
            "invoked_at": __import__("time").time(),
        }
        session.update_state({ACTIVE_SKILL_BODIES_STATE_KEY: state})
    except Exception as exc:
        logger.debug("[SkillTurboRail] _record_turbo_body fallback: %s", exc)
        # 兜底：直接设属性
        if not hasattr(session, "_turbo_bodies"):
            session._turbo_bodies = {}
        session._turbo_bodies[turbo_name] = body
