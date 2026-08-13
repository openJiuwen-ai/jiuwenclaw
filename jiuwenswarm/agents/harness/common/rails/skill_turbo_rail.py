# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurboRail — turbo 加速面渐进式注入 + execute source_id + skill_complete 释放.

职责：
1. [层1 常驻] before_model_call 注入 turbo 面 catalog（name + description + 场景数）
2. [层2 按需] after_tool_call 检测 skill_turbo_tool discover/activate 成功 → 钉入 SKILL_TURBO.md 正文
3. [execute] after_tool_call 检测 skill_turbo_tool execute → 仅维护 stream_source_id（todo 由 Agent 自调 todo_modify）
4. [释放] after_tool_call 检测 skill_complete → 释放 turbo 正文 attachment + 清理 executor 缓存

设计要点：
- 层1 catalog 幂等注入（不碰正文），仅含 name + description + 场景数（渐进式披露）
- 层2 正文钉入/释放走 ``prompt_attachment_manager``（``PromptAttachmentKind.SKILL``）
- turbo_name 统一取自 face.turbo_name，与 pin/unpin 同源
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.prompt.prompt_builder import PromptPriority
from jiuwenswarm.common.utils import logger

__all__ = [
    "SkillTurboRail",
    "get_skill_turbo_online_source_id",
    "set_skill_turbo_online_source_id",
    "reset_skill_turbo_online_source_id",
]


_TURBO_CATALOG_SECTION_NAME = "skill_turbo_catalog"
_TURBO_BODY_ATTACHMENT_SOURCE = "jiuwenswarm.skill_turbo_rail"


def _turbo_body_section_name(turbo_name: str) -> str:
    return f"skill_turbo.{turbo_name}"

# ── Skill Turbo 在线执行：可选的 stream_source_id ContextVar（兼容保留）──
# 串行在线已对齐批量：节点内容靠 TaskExecutionRail task.start 进子气泡，
# execute 路径不再写入本 ContextVar。保留 getter 供 interface_deep 无害读取。
_skill_turbo_online_source_id: ContextVar[str | None] = ContextVar(
    "skill_turbo_online_source_id", default=None
)


def get_skill_turbo_online_source_id() -> str | None:
    """Return the stream_source_id to inject for main Agent chat.delta, or None."""
    return _skill_turbo_online_source_id.get()


def set_skill_turbo_online_source_id(value: str | None) -> Token:
    """Set the stream_source_id to inject for main Agent chat.delta."""
    return _skill_turbo_online_source_id.set(value)


def reset_skill_turbo_online_source_id(token: Token) -> None:
    """Reset the stream_source_id ContextVar to its pre-set value."""
    _skill_turbo_online_source_id.reset(token)


def _build_turbo_catalog_text(faces: list[Any]) -> str:
    """构建层1 catalog 文本（turbo 面 name + description + 场景数）.

    层1 瘦身：仅注入 name + description + 场景数，不含场景触发条件。
    场景触发条件通过 discover 模式按需获取（渐进式披露），避免层1 膨胀。
    """
    if not faces:
        return ""

    lines = ["## Skill Turbo 加速面清单\n"]
    lines.append("以下 skill 有 turbo 加速面，可用 `skill_turbo_tool` 在线执行：\n")
    for face in faces:
        scenario_count = face.scenario_count
        lines.append(
            f"- **{face.turbo_name}**（源 skill: {face.source_skill}）: {face.description}"
            f"（{scenario_count} 个场景）"
        )
    lines.append(
        "\n使用方式：先 `skill_turbo_tool(skill_name=..., scenario=None, plan_name=None)` discover 获取场景清单与触发条件，"
        "据触发条件选择 scenario 后 `skill_turbo_tool(skill_name, scenario, plan_name=None)` activate 获取节点契约，"
        "再逐节点 `skill_turbo_tool(skill_name, scenario, plan_name, inputs)` execute。"
    )
    lines.append(
        "\n**执行约束（重要）**：\n"
        "- **静默执行**：逐节点 execute 期间不要向用户播报每步进度（如\"Stage 1 完成\"\"正在检测环境依赖\"等），"
        "节点进度通过 todo/task 事件自动展示在副气泡；仅在全部节点完成、需要用户补充信息、或发生错误时才输出文本。\n"
        "- **ask_user 中断**：节点需要用户补充信息时 execute 会触发 HITL 中断，前端自动弹窗询问用户，"
        "你无需手动转发；用户回复后 execute 自动 resume 继续，不要重复调用同一节点。"
    )
    return "\n".join(lines)


class SkillTurboRail(DeepAgentRail):
    """turbo 加速面渐进式注入 rail.

    priority > TaskExecutionRail/JiuSwarmStreamEventRail: execute 后 Agent 自调 todo_modify
    推进 todo，后续进度 rails 再读取并 emit task.update/todo.updated。
    """

    priority = 86

    def __init__(self, skill_roots: list[str] | None = None) -> None:
        self._system_prompt_builder: Any = None
        self._attachment_manager: Any = None
        # 与 DeepAdapter._resolve_skill_dirs() / SkillUseRail 同源，避免只扫空的
        # workspace/skills 导致 turbo faces=0。
        self._skill_roots: list[str] = [str(p) for p in (skill_roots or []) if str(p).strip()]
        # 管理 online source_id ContextVar 的 token，确保跨 execute 阶段不泄漏。
        # 每次 set 前先 reset 上一枚 token，skill_complete/uninit 时统一清零。
        self._online_source_id_token: Token | None = None

    def _bind_skill_roots(self) -> None:
        """把 adapter 解析出的 skill roots 注入 ContextVar，供 skill_turbo_tool 复用."""
        if not self._skill_roots:
            return
        try:
            from jiuwenswarm.agents.skill_turbo.online.context_vars import (
                set_skill_turbo_skill_roots,
            )

            set_skill_turbo_skill_roots(self._skill_roots)
        except Exception as exc:
            logger.debug("[SkillTurboRail] bind skill_roots failed: %s", exc)

    def _set_online_source_id(self, value: str | None) -> None:
        """安全设置 online source_id：先 reset 旧 token，再 set 并保存新 token。"""
        if self._online_source_id_token is not None:
            try:
                reset_skill_turbo_online_source_id(self._online_source_id_token)
            except ValueError:
                # token 已被其他路径消费（如嵌套 set），忽略
                pass
            self._online_source_id_token = None
        self._online_source_id_token = set_skill_turbo_online_source_id(value)

    def _reset_online_source_id(self) -> None:
        """清除 online source_id（skill_complete / uninit 时调用）。"""
        if self._online_source_id_token is not None:
            try:
                reset_skill_turbo_online_source_id(self._online_source_id_token)
            except ValueError:
                pass
            self._online_source_id_token = None

    def init(self, agent: Any) -> None:
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._attachment_manager = getattr(agent, "prompt_attachment_manager", None)

    def uninit(self, agent: Any) -> None:
        builder = getattr(agent, "system_prompt_builder", None)
        if builder is not None:
            builder.remove_section(_TURBO_CATALOG_SECTION_NAME)
        self._system_prompt_builder = None
        self._attachment_manager = None
        # 确保异常/中断路径下 ContextVar 不泄漏
        self._reset_online_source_id()

    def _resolve_language(self) -> str:
        builder = self._system_prompt_builder
        if builder is not None:
            lang = getattr(builder, "language", None)
            if lang:
                # 与 skill_protocol_prompt_rail 一致：zh → cn，保证 content key
                # 落在 SystemPromptBuilder 支持的语言集合（cn/en）内。
                return "cn" if lang in ("cn", "zh", "zh-cn", "zh_cn") else "en"
        return "cn"

    def _resolve_priority(self) -> int:
        # SKILLS(40) < SKILL_PROTOCOL(45): catalog must appear before skill_protocol,
        # so skill_protocol_prompt_rail reference to upper catalog section holds
        return PromptPriority.SKILLS

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """[层1 常驻] 注入 turbo 面 catalog（幂等，不碰正文）."""
        # 与 SkillUseRail 同源 roots：先注入 ContextVar，再显式传给 discover，
        # 避免 tool/schema_loader 只扫到空的 workspace/skills。
        self._bind_skill_roots()

        builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if builder is not None:
            self._system_prompt_builder = builder
        if builder is None:
            return

        try:
            from jiuwenswarm.agents.skill_turbo.online.schema_loader import (
                discover_all_turbo_faces,
            )

            faces = discover_all_turbo_faces(
                self._skill_roots if self._skill_roots else None
            )
        except Exception as exc:
            logger.warning("[SkillTurboRail] discover turbo faces failed: %s", exc)
            return

        if not faces:
            return

        text = _build_turbo_catalog_text(faces)
        if not text:
            return

        try:
            language = self._resolve_language()
            section = PromptSection(
                name=_TURBO_CATALOG_SECTION_NAME,
                content={language: text},
                priority=self._resolve_priority(),
            )
            builder.add_section(section)
        except Exception as exc:
            logger.warning("[SkillTurboRail] add catalog section failed: %s", exc)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """工具执行前再次绑定 skill roots；execute 时清除 stream_source_id 注入.

        在线串行对齐批量：节点内容靠 TaskExecutionRail task.start 进子气泡，
        不在节点执行期注入 stream_source_id（避免无 task 栈时落到主气泡）。
        """
        self._bind_skill_roots()
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name != "skill_turbo_tool":
            return
        tool_args = _extract_tool_args_dict(ctx.inputs.tool_args)
        # execute（有 plan_name）：清掉规划期 ContextVar，走 task 栈路由
        if tool_args.get("plan_name") is not None:
            self._reset_online_source_id()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """[层2 按需] discover/activate 钉入正文 + skill_complete 释放."""
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name
        tool_args = _extract_tool_args_dict(ctx.inputs.tool_args)
        tool_result = ctx.inputs.tool_result

        logger.debug(
            "[SkillTurboRail] after_tool_call: tool=%s plan_name=%s",
            tool_name,
            tool_args.get("plan_name"),
        )

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
        """detect → 钉入正文; execute → 保持无 source_id; activate → 补钉正文."""
        plan_name = tool_args.get("plan_name")

        # execute 模式：对齐批量，不维护 stream_source_id（todo + task.start 负责子气泡）
        if plan_name is not None:
            self._reset_online_source_id()
            return

        # activate 模式：幂等补钉 SKILL_TURBO.md；规划旁白不强行灌 source_id
        if tool_args.get("scenario") is not None:
            result_dict = _extract_result_dict(tool_result)
            if result_dict and result_dict.get("success"):
                await self._pin_turbo_body(ctx, tool_args.get("skill_name", ""))
            return

        # discover 模式：钉入 SKILL_TURBO.md 正文
        result_dict = _extract_result_dict(tool_result)
        if not result_dict or not result_dict.get("success"):
            return

        skill_name = tool_args.get("skill_name", "")
        if not skill_name:
            return

        await self._pin_turbo_body(ctx, skill_name)

    @staticmethod
    def _resolve_turbo_name(skill_name: str) -> str | None:
        """解析 turbo_name（与 pin 同源，来自 face.turbo_name = frontmatter name）.

        消除 pin 用 face.turbo_name / unpin 用 f"{skill_name}_turbo" 两源不一致：
        两者仅在 frontmatter name 严格遵循 "{source_skill}_turbo" 约定时相等，
        否则 unpin 找不到 pin → turbo 正文永久驻留 → 后续 skill_tool 被占位阻塞。
        统一从 face.turbo_name 取值，必然与 pin 一致。

        Returns:
            turbo_name 或 None（face 未找到/解析失败）
        """
        try:
            from jiuwenswarm.agents.skill_turbo.online.skill_turbo_tool import (
                resolve_turbo_face_for_skill,
            )

            face = resolve_turbo_face_for_skill(skill_name)
            return face.turbo_name if face else None
        except Exception as exc:
            logger.debug("[SkillTurboRail] resolve turbo_name failed: %s", exc)
            return None

    async def _pin_turbo_body(self, ctx: AgentCallbackContext, skill_name: str) -> bool:
        """钉入 SKILL_TURBO.md 正文（幂等，discover/activate 均可调）.

        discover 钉入失败（session 缺失/IO 抖动）时 activate 再试一次，
        保证 execute 阶段有 SKILL_TURBO.md 正文。钉入幂等（覆盖写入）。

        Returns:
            True 钉入成功；False 失败（调用方据实记录，不可假装成功）
        """
        if not skill_name:
            return False
        try:
            from jiuwenswarm.agents.skill_turbo.online.skill_turbo_tool import (
                resolve_turbo_face_for_skill,
            )
            from jiuwenswarm.agents.skill_turbo.online.schema_loader import (
                load_skill_turbo_body,
            )

            face = resolve_turbo_face_for_skill(skill_name)
            if face is None:
                logger.warning(
                    "[SkillTurboRail] pin turbo body: face not found for %s", skill_name
                )
                return False
            body = load_skill_turbo_body(face.turbo_dir)
            if await self._upsert_turbo_body_attachment(ctx, face.turbo_name, body):
                logger.info(
                    "[SkillTurboRail] pinned SKILL_TURBO.md body for %s",
                    face.turbo_name,
                )
                return True
            logger.error(
                "[SkillTurboRail] FAILED to pin SKILL_TURBO.md body for %s; "
                "subsequent execute may lack body",
                face.turbo_name,
            )
            return False
        except Exception as exc:
            logger.warning("[SkillTurboRail] pin turbo body failed: %s", exc)
            return False

    async def _upsert_turbo_body_attachment(
        self,
        ctx: AgentCallbackContext,
        turbo_name: str,
        body: str,
    ) -> bool:
        """将 SKILL_TURBO.md 正文写入 prompt attachment（PromptAttachmentKind.SKILL）."""
        manager = self._attachment_manager
        if manager is None:
            manager = getattr(getattr(ctx, "agent", None), "prompt_attachment_manager", None)
        if manager is None:
            logger.warning(
                "[SkillTurboRail] prompt attachment manager unavailable; skip pin %s",
                turbo_name,
            )
            return False
        section = _turbo_body_section_name(turbo_name)
        content = f"# SKILL_TURBO: {turbo_name}\n\n{body}"
        try:
            writer = manager.bind_context(ctx)
            await writer.add_section(
                section,
                content,
                PromptAttachmentKind.SKILL,
                _TURBO_BODY_ATTACHMENT_SOURCE,
                priority=PromptPriority.SKILLS,
                content_kind="text/markdown",
            )
            return True
        except ValueError as exc:
            logger.warning(
                "[SkillTurboRail] pin turbo body attachment failed section=%s: %s",
                section,
                exc,
            )
            return False
        except Exception as exc:
            logger.error("[SkillTurboRail] _upsert_turbo_body_attachment failed: %s", exc)
            return False

    async def _clear_turbo_body_attachment(
        self,
        ctx: AgentCallbackContext,
        turbo_name: str,
    ) -> bool:
        """释放 turbo 正文 attachment."""
        manager = self._attachment_manager
        if manager is None:
            manager = getattr(getattr(ctx, "agent", None), "prompt_attachment_manager", None)
        if manager is None:
            return False
        section = _turbo_body_section_name(turbo_name)
        try:
            await manager.bind_context(ctx).clear_section(section)
            return True
        except ValueError as exc:
            logger.warning(
                "[SkillTurboRail] clear turbo body attachment failed section=%s: %s",
                section,
                exc,
            )
            return False
        except Exception as exc:
            logger.warning("[SkillTurboRail] _clear_turbo_body_attachment failed: %s", exc)
            return False

    async def _handle_skill_complete(
        self,
        ctx: AgentCallbackContext,
        tool_args: dict[str, Any],
    ) -> None:
        """检测 skill_complete → 释放 turbo 正文 pin + 清除在线执行 ContextVar + 清理 executor 缓存."""
        # 清除在线执行 ContextVar（主 Agent 最终总结应回主气泡）
        self._reset_online_source_id()

        skill_name = tool_args.get("skill_name", "")
        if not skill_name:
            return

        try:
            turbo_name = self._resolve_turbo_name(skill_name)
            if turbo_name is None:
                logger.warning(
                    "[SkillTurboRail] skill_complete: turbo face not found for %s, "
                    "skip attachment release",
                    skill_name,
                )
            elif await self._clear_turbo_body_attachment(ctx, turbo_name):
                logger.info(
                    "[SkillTurboRail] released turbo body attachment for %s",
                    turbo_name,
                )
        except Exception as exc:
            logger.warning("[SkillTurboRail] release turbo body attachment failed: %s", exc)

        # 清理 executor 缓存
        try:
            session = _get_session(ctx)
            session_id = _get_session_id(session) if session is not None else ""
            if session_id:
                from jiuwenswarm.agents.skill_turbo.online.skill_turbo_tool import (
                    clear_cached_parent_executor,
                )
                clear_cached_parent_executor(session_id)
        except Exception as cache_exc:
            logger.debug("[SkillTurboRail] clear executor cache failed: %s", cache_exc)


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


def _extract_tool_args_dict(tool_args: Any) -> dict[str, Any]:
    """从 ToolCallInputs.tool_args 提取 dict.

    AbilityManager 在 after_tool_call 中会把 ``ctx.inputs.tool_args`` 重置为
    ``tool_call.arguments`` 原始值；线上日志显示该值是 JSON 字符串。因此 rail
    不能假设 tool_args 已被解析为 dict。
    """
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        try:
            parsed = json.loads(tool_args)
        except json.JSONDecodeError:
            logger.debug("[SkillTurboRail] parse tool_args json failed")
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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


def _get_session_id(session: Any) -> str:
    """Best-effort session id extraction（与 plan_pause_helpers.session_id_from_session 同逻辑）."""
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        return str(getter() or "")
    sid = getattr(session, "session_id", None)
    if callable(sid):
        return str(sid() or "")
    return str(sid or "")


