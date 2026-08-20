# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""子 Agent 委托 Skill 加载授权 rail。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any, Callable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from jiuwenclaw.agentserver.deep_agent.rails.skill_authorization_rail import (
    SKILL_AUTHORIZATION_CONTEXT_TOKEN_KEY,
    SkillAuthorizationRail,
    _SkillApprovalCall,
    _parse_answer_to_action,
    _resolve_session_id,
)
from jiuwenclaw.agentserver.deep_agent.skill_lifecycle_events import (
    extract_skill_lifecycle_event,
    is_root_skill_load,
    is_skill_complete,
)
from jiuwenclaw.agentserver.permissions.skill_authorization import (
    GrantDecision,
    SkillApprovalAction,
    SubagentApprovalKind,
    SubagentApprovalRegistry,
    get_skill_authorization_context,
    get_skill_authorization_generation,
    get_subagent_approval_registry,
    setup_skill_authorization_context,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.subagent_approval_registry import (
    ApprovalExpirySender,
    ApprovalSender,
    SubagentApprovalCancelled,
    SubagentApprovalCapacityError,
)

logger = logging.getLogger(__name__)


def _resolve_subagent_scope(
    ctx: AgentCallbackContext,
    *,
    agent_scope_id: str,
    approval_session_id: str,
    session_resolver: Callable[[AgentCallbackContext], str | None],
) -> tuple[str, str] | None:
    """子 Agent 授权作用域：恒定绑定主会话 session_id + 子 Agent scope。

    ContextVar 命中且 scope 匹配时直接返回；否则回落到 rail 注入的主会话
    session_id（不回落到子 Agent 复合 session，避免 Grant 命名空间错位）。
    """
    authz = get_skill_authorization_context()
    if authz is not None and authz.session_id:
        if authz.agent_scope_id and authz.agent_scope_id == agent_scope_id:
            return authz.session_id, authz.agent_scope_id
    session_id = approval_session_id or session_resolver(ctx)
    if not session_id or not agent_scope_id:
        return None
    return session_id, agent_scope_id


class SubagentSkillAuthorizationRail(SkillAuthorizationRail):
    """Reuse main Manifest/Grant logic while delegating the interactive step."""

    _gate_log_tag = "subagent.skill"

    def __init__(
        self,
        *,
        approval_sender: ApprovalSender | None,
        approval_expiry_sender: ApprovalExpirySender | None = None,
        approval_registry: SubagentApprovalRegistry | None = None,
        approval_timeout: float = 120.0,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._approval_sender = approval_sender
        self._approval_expiry_sender = approval_expiry_sender
        self._approval_registry = approval_registry or get_subagent_approval_registry()
        self._approval_timeout = approval_timeout
        self._approval_session_id = (session_id or "").strip()

    def _resolve_scope(self, ctx: AgentCallbackContext) -> tuple[str, str] | None:
        return _resolve_subagent_scope(
            ctx,
            agent_scope_id=self._agent_scope_id,
            approval_session_id=self._approval_session_id,
            session_resolver=_resolve_session_id,
        )

    def _invoke_session_id(self, ctx: AgentCallbackContext) -> str | None:
        """子 Agent 委托审批统一绑定主会话 session。"""
        return self._approval_session_id or _resolve_session_id(ctx)

    # ---------- 门禁模板钩子 ----------

    def _claim_gate(self, ctx: AgentCallbackContext, authorization_generation: int) -> bool:
        """子 Agent 无原权限 Rail 兜底，无需 handled 短路标记。"""
        return True

    def _proceed_tool_call(self, ctx: AgentCallbackContext, tool_call: Any, tool_name: str) -> None:
        """子 Agent 无原权限 Rail 接管，放行必须显式 approve。"""
        self._apply_decision(ctx, tool_call, tool_name, self.approve())

    def _on_policy_evaluation_failed(self, ctx: AgentCallbackContext, tool_call: Any, tool_name: str) -> None:
        self._apply_decision(
            ctx, tool_call, tool_name,
            self.reject(tool_result="[PERMISSION_DENIED] 子 Agent Skill 权限策略评估失败"),
        )

    def _on_gate_stale(self, ctx: AgentCallbackContext, tool_call: Any, tool_name: str) -> None:
        self._apply_decision(
            ctx, tool_call, tool_name,
            self.reject(tool_result="[PERMISSION_DENIED] Skill 动态授权配置已变更"),
        )

    def _try_reuse_session_approval(self, call: _SkillApprovalCall) -> bool:
        """子 Agent 生命周期短，审批始终落 local；不回写父或会话缓存。"""
        return False

    async def _run_approval_flow(self, call: _SkillApprovalCall) -> None:
        """子 Agent 审批：委托主会话 Future 等待答案，不能 checkpoint interrupt。"""
        ctx, tool_call, tool_name = call.ctx, call.tool_call, call.tool_name
        tool_call_id, session_id, agent_scope_id = call.tool_call_id, call.session_id, call.agent_scope_id
        manifest, diff = call.manifest, call.diff
        authorization_generation = call.authorization_generation
        if self._approval_sender is None:
            logger.warning(
                "[skill_authorization] subagent.skill.no_approval_channel skill=%s scope=%s",
                manifest.skill_name, agent_scope_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return
        card = self._build_approval_card(manifest, diff, None, agent_scope_id)
        card = replace(
            card,
            actions=tuple(
                action for action in card.actions
                if action != SkillApprovalAction.APPROVE_SESSION.value
            ),
        )
        if (
            authorization_generation != get_skill_authorization_generation()
            or not self._feature_enabled()
        ):
            self._apply_decision(
                ctx, tool_call, tool_name,
                self.reject(tool_result="[PERMISSION_DENIED] Skill 动态授权配置已变更"),
            )
            return
        try:
            answer = await self._approval_registry.request(
                kind=SubagentApprovalKind.SKILL_LOAD,
                session_id=self._approval_session_id or session_id,
                agent_scope_id=agent_scope_id,
                tool_call_id=tool_call_id,
                payload={
                    "message": self._render_approval_message(card),
                    "card": card.to_dict(),
                    "options": self._build_ui_options(card),
                    "skill_name": manifest.skill_name,
                },
                sender=self._approval_sender,
                expiry_sender=self._approval_expiry_sender,
                timeout=self._approval_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[skill_authorization] subagent.skill.approval_timeout skill=%s scope=%s",
                manifest.skill_name, agent_scope_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return
        except SubagentApprovalCancelled:
            logger.info(
                "[skill_authorization] subagent.skill.no_overlay skill=%s scope=%s "
                "reason=approval_cancelled",
                manifest.skill_name, agent_scope_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return
        except SubagentApprovalCapacityError:
            logger.warning(
                "[skill_authorization] subagent.skill.reject skill=%s scope=%s "
                "reason=approval_capacity",
                manifest.skill_name, agent_scope_id,
            )
            self._apply_decision(
                ctx, tool_call, tool_name,
                self.reject(tool_result="[PERMISSION_REJECTED] 子 Agent 审批请求过多，请稍后重试。"),
            )
            return
        except Exception:  # noqa: BLE001 — 审批通道异常（如 WebSocket 断开）不得击穿子 Agent
            logger.warning(
                "[skill_authorization] subagent.skill.approval_channel_error skill=%s scope=%s",
                manifest.skill_name, agent_scope_id, exc_info=True,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return

        if not self._feature_enabled():
            logger.info(
                "[skill_authorization] subagent.skill.no_overlay skill=%s scope=%s "
                "reason=feature_disabled_after_answer",
                manifest.skill_name, agent_scope_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return
        if authorization_generation != get_skill_authorization_generation():
            logger.info(
                "[skill_authorization] subagent.skill.no_overlay skill=%s scope=%s "
                "reason=authorization_generation_changed",
                manifest.skill_name, agent_scope_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return

        action = _parse_answer_to_action(answer, allow_session=False)
        current_manifest = self._resolve_manifest_for_call(manifest.skill_name)
        if (
            current_manifest is None
            or current_manifest.identity_tuple() != manifest.identity_tuple()
        ):
            logger.warning(
                "[skill_authorization] subagent.skill.approval_stale skill=%s scope=%s",
                manifest.skill_name, agent_scope_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return
        if action is None:
            logger.warning(
                "[skill_authorization] subagent.skill.reject skill=%s scope=%s "
                "reason=invalid_approval_action",
                manifest.skill_name, agent_scope_id,
            )
            self._apply_decision(
                ctx, tool_call, tool_name,
                self.reject(tool_result="[PERMISSION_REJECTED] 无法识别的子 Agent Skill 审批结果。"),
            )
            return
        if action == SkillApprovalAction.APPROVE_ONCE:
            # 子 Agent 生命周期短，批准始终落 local；不回写父或会话缓存。
            self._create_pending_grant_quiet(
                session_id, agent_scope_id, current_manifest, tool_call_id,
                decision=GrantDecision.LOCAL, log_tag="subagent.skill.grant_create_failed",
            )
        self._proceed_tool_call(ctx, tool_call, tool_name)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        event = extract_skill_lifecycle_event(ctx)
        await super().after_tool_call(ctx)
        if not self._feature_enabled() or event is None:
            return
        scope = self._resolve_scope(ctx)
        if scope is None:
            return
        session_id, agent_scope_id = scope
        if is_skill_complete(event):
            self.store.exit_skill_execution(session_id, agent_scope_id)
        elif is_root_skill_load(event):
            # Grant 是否激活不影响窗口：continue_without_overlay 仍按基础权限裁决。
            self.store.enter_skill_execution(
                session_id,
                agent_scope_id,
                event.skill_name,
            )
