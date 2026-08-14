# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""子 Agent Skill 执行期的权限裁决 rail：ASK 走 Future 委托，不抛 checkpoint。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail

from jiuwenclaw.agentserver.deep_agent.skill_lifecycle_events import (
    is_skill_authorization_gate_call,
    parse_tool_call_arguments,
)
from jiuwenclaw.agentserver.deep_agent.rails.permission_rail import TOOL_NAME_ALIASES
from jiuwenclaw.agentserver.deep_agent.rails.skill_authorization_rail import (
    _resolve_session_id,
)
from jiuwenclaw.agentserver.permissions.config_loader import (
    get_effective_permissions_config,
)
from jiuwenclaw.agentserver.permissions.core import PermissionEngine, get_permission_engine
from jiuwenclaw.agentserver.permissions.models import PermissionLevel
from jiuwenclaw.agentserver.permissions.skill_authorization.grant_store import (
    SkillGrantStore,
    get_skill_grant_store,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.schema import (
    is_skill_authorization_enabled,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.composer import (
    reset_skill_authorization_context,
    setup_skill_authorization_context,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.subagent_approval_registry import (
    ApprovalSender,
    SubagentApprovalCancelled,
    SubagentApprovalCapacityError,
    SubagentApprovalKind,
    SubagentApprovalRegistry,
    get_subagent_approval_registry,
)
from jiuwenclaw.agentserver.deep_agent.rails.subagent_skill_authorization_rail import (
    _resolve_subagent_scope,
)

logger = logging.getLogger(__name__)


_SEVERITY = {PermissionLevel.ALLOW: 0, PermissionLevel.ASK: 1, PermissionLevel.DENY: 2}


def _tighten_permission_level(
    base_level: PermissionLevel,
    owner_level: str | None,
) -> PermissionLevel:
    """Apply owner scope as a restriction after the Skill-aware engine result."""
    normalized = owner_level.strip().lower() if isinstance(owner_level, str) else ""
    try:
        owner = PermissionLevel(normalized)
    except ValueError:
        return base_level
    return owner if _SEVERITY.get(owner, 0) > _SEVERITY.get(base_level, 0) else base_level


class SubagentPermissionRail(ConfirmInterruptRail):
    """Apply normal tool permission semantics during a subagent Skill window."""

    priority: int = 90

    def __init__(
        self,
        *,
        agent_scope_id: str,
        engine: PermissionEngine | Any | None = None,
        grant_store: SkillGrantStore | None = None,
        approval_registry: SubagentApprovalRegistry | None = None,
        approval_sender: ApprovalSender | None = None,
        approval_timeout: float = 120.0,
        config_provider: Callable[[], dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> None:
        super().__init__(tool_names=[])
        self._agent_scope_id = (agent_scope_id or "").strip()
        self._engine = engine or get_permission_engine()
        self._grant_store = grant_store or get_skill_grant_store()
        self._approval_registry = approval_registry or get_subagent_approval_registry()
        self._approval_sender = approval_sender
        self._approval_timeout = approval_timeout
        self._config_provider = config_provider or get_effective_permissions_config
        self._approval_session_id = (session_id or "").strip()

    def _resolve_scope(self, ctx: AgentCallbackContext) -> tuple[str, str] | None:
        return _resolve_subagent_scope(
            ctx,
            agent_scope_id=self._agent_scope_id,
            approval_session_id=self._approval_session_id,
            session_resolver=_resolve_session_id,
        )

    def _enabled(self) -> bool:
        try:
            return is_skill_authorization_enabled(self._config_provider())
        except Exception:  # noqa: BLE001
            logger.warning(
                "[skill_authorization] subagent.permission.flag_read_failed",
                exc_info=True,
            )
            return False

    @staticmethod
    def _approved(answer: Any) -> bool:
        if isinstance(answer, list):
            return any(SubagentPermissionRail._approved(item) for item in answer)
        if not isinstance(answer, dict):
            return False
        selected = answer.get("selected_options")
        return isinstance(selected, list) and any(
            str(item).strip() in {"本次允许", "允许"}
            for item in selected
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self._enabled():
            return
        try:
            from jiuwenclaw.agentserver.deep_agent.permissions.owner_scopes import (
                TOOL_PERMISSION_CONTEXT,
            )

            permission_context = TOOL_PERMISSION_CONTEXT.get()
            if (
                permission_context is not None
                and permission_context.scene == "group_digital_avatar"
            ):
                return
        except Exception:  # noqa: BLE001 — 保持既有子 Agent 路径
            return
        scope = self._resolve_scope(ctx)
        if scope is None:
            return
        session_id, agent_scope_id = scope
        active_skill_name = self._grant_store.get_active_skill_execution(
            session_id,
            agent_scope_id,
        )
        if active_skill_name is None:
            return

        tool_call = ctx.inputs.tool_call
        tool_name = ctx.inputs.tool_name
        tool_args = parse_tool_call_arguments(tool_call)
        if is_skill_authorization_gate_call(
            tool_name,
            tool_args,
            self._config_provider(),
        ):
            return

        # 裁决前显式绑定授权 Context，让 PermissionEngine 命中 ACTIVE Grant 应用 overlay。
        # 跨任务/线程时 ContextVar 不一定可靠传播，这里强制绑定本 rail 作用域，裁决后恢复。
        token = setup_skill_authorization_context(
            session_id,
            agent_scope_id,
            "",
        )
        try:
            result = await self._engine.check_permission(
                tool_name=TOOL_NAME_ALIASES.get(tool_name, tool_name),
                tool_args=tool_args,
                channel_id=None,
                session_id=session_id,
            )
        finally:
            reset_skill_authorization_context(token)

        # 与主 Agent 一致：owner_scopes 只能在引擎（含 Skill overlay）之后收紧。
        try:
            from jiuwenclaw.agentserver.deep_agent.permissions.owner_scopes import (
                TOOL_PERMISSION_CONTEXT,
                _resolve_owner_scope_level,
            )

            perm_ctx = TOOL_PERMISSION_CONTEXT.get()
            if perm_ctx is not None and perm_ctx.principal_user_id:
                config = self._config_provider()
                owner_scopes = config.get("owner_scopes", {}) if isinstance(config, dict) else {}
                cid = perm_ctx.channel_id.strip()
                uid = perm_ctx.principal_user_id.strip()
                channel_scopes = owner_scopes.get(cid) or {} if isinstance(owner_scopes, dict) else {}
                scope_cfg = channel_scopes.get(uid) if isinstance(channel_scopes, dict) else None
                owner_level = _resolve_owner_scope_level(
                    scope_cfg,
                    TOOL_NAME_ALIASES.get(tool_name, tool_name),
                    tool_args,
                )
                tightened = _tighten_permission_level(result.permission, owner_level)
                if tightened != result.permission:
                    result.permission = tightened
                    result.matched_rule = (
                        f"{result.matched_rule or 'permission_engine'}|owner_scopes:{owner_level}"
                    )
                    result.reason = f"owner_scopes 将权限收紧为 {tightened.value}"
        except Exception:  # noqa: BLE001 — owner 上下文存在时异常必须拒绝
            logger.warning(
                "[skill_authorization] subagent.permission.owner_scope_failed",
                exc_info=True,
            )
            result.permission = PermissionLevel.DENY
            result.matched_rule = "owner_scopes:evaluation_error"
            result.reason = "owner_scopes 权限评估失败"
        if result.permission == PermissionLevel.ALLOW:
            decision = self.approve()
        elif result.permission == PermissionLevel.DENY:
            decision = self.reject(
                tool_result=f"[PERMISSION_DENIED] {result.reason or 'Operation not allowed'}",
            )
        else:
            decision = await self._resolve_ask(
                session_id=session_id,
                agent_scope_id=agent_scope_id,
                tool_call=tool_call,
                tool_name=tool_name,
                tool_args=tool_args,
                reason=result.reason,
                risk=result.risk,
                skill_name=active_skill_name,
            )
        ctx.extra["_interrupt_decision"] = decision
        self._apply_decision(ctx, tool_call, tool_name, decision)

    async def _resolve_ask(
        self,
        *,
        session_id: str,
        agent_scope_id: str,
        tool_call: Any,
        tool_name: str,
        tool_args: dict[str, Any],
        reason: str | None,
        risk: dict | None,
        skill_name: str,
    ):
        from jiuwenclaw.agentserver.permissions.skill_authorization import (
            get_skill_authorization_generation,
        )

        authorization_generation = get_skill_authorization_generation()
        if self._approval_sender is None:
            return self.reject(
                tool_result="[PERMISSION_DENIED] 子 Agent 权限审批通道不可用",
            )
        tool_call_id = str(getattr(tool_call, "id", "") or "")
        try:
            answer = await self._approval_registry.request(
                kind=SubagentApprovalKind.TOOL_PERMISSION,
                session_id=self._approval_session_id or session_id,
                agent_scope_id=agent_scope_id,
                tool_call_id=tool_call_id,
                payload={
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "skill_name": skill_name,
                    "reason": reason or "Operation requires approval",
                    "risk": risk,
                },
                sender=self._approval_sender,
                timeout=self._approval_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[skill_authorization] subagent.permission.ask_timeout session=%s scope=%s tool=%s",
                session_id,
                agent_scope_id,
                tool_name,
            )
            return self.reject(
                tool_result="[PERMISSION_DENIED] 子 Agent 权限审批超时",
            )
        except (SubagentApprovalCancelled, SubagentApprovalCapacityError):
            return self.reject(
                tool_result="[PERMISSION_DENIED] 子 Agent 权限审批已取消或请求过多",
            )
        except Exception:  # noqa: BLE001 — 审批通道异常 fail-closed：仅拒绝当前调用
            logger.warning(
                "[skill_authorization] subagent.permission.ask_channel_error session=%s scope=%s tool=%s",
                session_id,
                agent_scope_id,
                tool_name,
                exc_info=True,
            )
            return self.reject(
                tool_result="[PERMISSION_DENIED] 子 Agent 权限审批通道异常",
            )
        if not self._enabled():
            return self.reject(
                tool_result="[PERMISSION_DENIED] Skill 动态授权功能已关闭",
            )
        if authorization_generation != get_skill_authorization_generation():
            return self.reject(
                tool_result="[PERMISSION_DENIED] Skill 动态授权配置已变更",
            )
        if self._grant_store.get_active_skill_execution(
            session_id,
            agent_scope_id,
        ) != skill_name:
            return self.reject(
                tool_result="[PERMISSION_DENIED] Skill 执行作用域已结束或发生变化",
            )
        if self._approved(answer):
            return self.approve()
        return self.reject(tool_result="[PERMISSION_DENIED] 用户未批准子 Agent 工具调用")
