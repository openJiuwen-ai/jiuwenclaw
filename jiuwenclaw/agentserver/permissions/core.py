# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限引擎 - 核心权限控制模块。

Phase-1 编排：

1. 加载 / 热更新 ``permissions`` 配置。
2. ``check_permission`` 按设计文档第 2 节的 Guard 管线评估：
   - 工具档位（``allow`` / ``deny`` / ``guard``）短路 DENY/ALLOW；``guard`` 进入 Guard 管线。
   - 子线 A：``evaluate_tiered_policy_detailed``（命令 / 参数规则）。**已注册的「路径类」读写工具**
     （非 shell）在管线 A **仅解析整工具 DENY**；非 DENY 时不产出 allow/guard 等档位，
     **跳过**管线 A 的其余档位与 ``rules`` / subcommand，实质判定完全由管线 B（``file_guard``）承担。
   - 子线 B：``FileGuardChecker.evaluate_accesses`` + ``evaluate_command_intents``
     （三轴文件路径判定）。
   - 通过 ``strictest`` 合并档位与 ``file_guard`` 结果（路径类工具在非 DENY 时仅由 B 侧抬升降）。
   - ``file_operations`` 透传到 ``PermissionResult`` 供审批卡渲染。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.permissions.checker import PERMISSION_ENABLED_CHANNELS, ToolPermissionLog
from jiuwenclaw.agentserver.permissions.command_intent import (
    CommandIntent,
    collect_command_intents,
    is_command_intent_enabled,
)
from jiuwenclaw.agentserver.permissions.file_guard import (
    FileGuardChecker,
    classify_tool_file_action_kind,
    report_legacy_path_rules_at_load,
)
from jiuwenclaw.agentserver.permissions.models import (
    FileOperation,
    PermissionLevel,
    PermissionResult,
    SubcommandPermissionResult,
)
from jiuwenclaw.agentserver.permissions.shell_tools import is_shell_permission_tool
from jiuwenclaw.agentserver.permissions.tiered_policy import (
    evaluate_tiered_policy_detailed,
    evaluate_tiered_policy_path_tool_pipeline_a,
    get_builtin_security_rules,
    strictest as tiered_policy_strictest,
)

logger = logging.getLogger(__name__)


class PermissionEngine:
    """Phase-1 权限引擎。"""

    def __init__(
        self,
        config: dict | None = None,
        llm: Any = None,
        model_name: str | None = None,
    ):
        self.config = config or {}
        self._enabled = self.config.get("enabled", True)
        self._llm = llm
        self._model_name = model_name
        self._file_guard = FileGuardChecker(self.config)
        report_legacy_path_rules_at_load(self.config)

    # ---------- 配置 ----------

    def update_config(self, config: dict):
        """热更新配置。"""
        self.config = config
        self._enabled = config.get("enabled", True)
        self._file_guard = FileGuardChecker(config)
        report_legacy_path_rules_at_load(self.config)

    def update_llm(self, llm: Any, model_name: str | None) -> None:
        """供 ``PermissionInterruptRail`` 等热更新模型；用于 L3-Cmd LLM 调用。"""
        self._llm = llm
        self._model_name = model_name

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def file_guard(self) -> FileGuardChecker:
        return self._file_guard

    @property
    def llm(self) -> Any:
        """供外部（如 ``PermissionInterruptRail`` 诊断日志）只读获取已绑定的 LLM 客户端。

        通过 ``@property`` 暴露公共访问器、避免外部直接读 ``_llm`` 私有字段，
        保持 ``update_llm`` 仍是唯一的写入入口。
        """
        return self._llm

    @property
    def model_name(self) -> str | None:
        """与 ``llm`` 配套的模型名 getter；只读、不可绕过 ``update_llm`` 写入。"""
        return self._model_name

    # ---------- 同步直查（不发起 LLM；仅工具档位 + 子线 A + 子线 B 的工具参数通道） ----------

    def check_tool_permission_directly(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_id: str = "web",
    ) -> tuple[PermissionLevel | None, str | None]:
        """直接检查工具权限，不受 enabled 开关和 channel 限制。"""
        return self.evaluate_global_policy_directly(tool_name, tool_args, channel_id)

    def evaluate_global_policy_directly(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_id: str = "web",
        *,
        include_external_directory: bool = True,
    ) -> tuple[PermissionLevel | None, str | None]:
        """直接评估全局权限，不受 enabled/channel 短路影响。

        ``include_external_directory`` 名字保留向后兼容，实际现在控制是否参与 ``file_guard``
        子线 B（仅注册表通道，不发 LLM）。
        """
        permission, matched_rule, _, _ = self.evaluate_global_policy_with_details(
            tool_name,
            tool_args,
            channel_id,
            include_external_directory=include_external_directory,
        )
        return permission, matched_rule

    @staticmethod
    def _is_registered_path_tool_non_shell(tool_name: str) -> bool:
        """已注册（或兜底集合）路径类工具且非 shell：管线 A 只做 DENY 短路。"""
        return (
            classify_tool_file_action_kind(tool_name) is not None
            and not is_shell_permission_tool(tool_name)
        )

    def _evaluation_config(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if config is not None:
            return config
        from jiuwenclaw.agentserver.permissions.config_loader import (
            merge_session_permissions_overlay,
        )

        return merge_session_permissions_overlay(self.config, session_id=session_id)

    def _evaluation_file_guard(
        self,
        config: dict[str, Any],
        file_guard: FileGuardChecker | None = None,
    ) -> FileGuardChecker:
        if file_guard is not None:
            return file_guard
        if config is self.config:
            return self._file_guard
        return FileGuardChecker(config)

    def _evaluate_tier_for_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        config: dict[str, Any] | None = None,
    ) -> tuple[PermissionLevel | None, str | None, list[tuple[str, PermissionLevel, str]] | None]:
        """子线 A：路径类工具仅 ``evaluate_tiered_policy_path_tool_pipeline_a``（仅 DENY），其余完整 tiered。"""
        cfg = config if config is not None else self.config
        if PermissionEngine._is_registered_path_tool_non_shell(tool_name):
            tier_perm, tier_rule = evaluate_tiered_policy_path_tool_pipeline_a(cfg, tool_name)
            return tier_perm, tier_rule, None
        tp, tr, subs = evaluate_tiered_policy_detailed(cfg, tool_name, tool_args)
        return tp, tr, subs

    def evaluate_global_policy_with_details(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_id: str = "web",
        *,
        include_external_directory: bool = True,
        extra_intents: list[CommandIntent] | None = None,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
        file_guard: FileGuardChecker | None = None,
    ) -> tuple[
        PermissionLevel | None,
        str | None,
        list[SubcommandPermissionResult] | None,
        list[FileOperation] | None,
    ]:
        """同步直查的细节版本：返回 ``(level, rule, subcommand_results, file_operations)``。

        - ``include_external_directory`` 等价于"是否合并 file_guard 子线 B"。
        - ``extra_intents`` 由调用方提前异步算好（如 ``check_permission`` 内的 L1+L3-Cmd），
          这里做去重合并；不会自己发 LLM。
        """
        if not isinstance(tool_args, dict):
            logger.warning(
                "[PermissionEngine] direct tool_args is not a dict (type=%s), using {}",
                type(tool_args).__name__,
            )
            tool_args = {}

        cfg = self._evaluation_config(session_id=session_id, config=config)
        fg_checker = self._evaluation_file_guard(cfg, file_guard)

        tier_perm, tier_rule, raw_subs = self._evaluate_tier_for_tool(
            tool_name,
            tool_args,
            config=cfg,
        )
        permission = tier_perm
        matched_rule = tier_rule
        subcommand_results: list[SubcommandPermissionResult] | None = None
        if raw_subs is not None:
            subcommand_results = [
                SubcommandPermissionResult(text=text, permission=lvl, matched_rule=rule)
                for text, lvl, rule in raw_subs
            ]

        file_operations: list[FileOperation] | None = None
        merged_file_guard = bool(include_external_directory and permission != PermissionLevel.DENY)
        if merged_file_guard:
            fg_result = self._evaluate_file_guard(
                tool_name,
                tool_args,
                extra_intents,
                file_guard=fg_checker,
            )
            if fg_result is not None:
                if permission is None:
                    permission = fg_result.permission
                    matched_rule = fg_result.matched_rule
                else:
                    merged = tiered_policy_strictest(permission, fg_result.permission)
                    if merged != permission:
                        permission = merged
                        matched_rule = (
                            f"{matched_rule}|{fg_result.matched_rule}"
                            if matched_rule
                            else fg_result.matched_rule
                        )
                    elif fg_result.permission == permission and fg_result.matched_rule:
                        matched_rule = (
                            f"{matched_rule}|{fg_result.matched_rule}"
                            if matched_rule
                            else fg_result.matched_rule
                        )
                if fg_result.file_operations:
                    file_operations = list(fg_result.file_operations)
            elif permission is None:
                permission = PermissionLevel.ALLOW

        return permission, matched_rule, subcommand_results, file_operations

    # ---------- file_guard 调度 ----------

    def _collect_file_guard_accesses(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        extra_intents: list[CommandIntent] | None,
        *,
        file_guard: FileGuardChecker | None = None,
    ) -> list[tuple[Path, str, str]]:
        """合并工具参数通道与命令意图通道的路径访问列表（与子线 B 全量判定同源）。"""
        fg = file_guard if file_guard is not None else self._file_guard
        accesses = fg.collect_tool_arg_accesses(tool_name, tool_args)
        ws = fg.workspace_root()
        if extra_intents:
            for intent in extra_intents:
                action = getattr(intent, "action", None)
                if action not in ("read", "write", "exec"):
                    continue
                source = getattr(intent, "source", "shlex")
                for raw in getattr(intent, "paths", ()) or ():
                    if not isinstance(raw, str) or not raw.strip():
                        continue
                    try:
                        p = Path(raw)
                        if not p.is_absolute():
                            p = (ws / p).resolve()
                        else:
                            p = p.resolve()
                    except (OSError, RuntimeError):
                        continue
                    accesses.append((p, action, source))
        return accesses

    def _evaluate_file_guard(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        extra_intents: list[CommandIntent] | None,
        *,
        file_guard: FileGuardChecker | None = None,
    ) -> PermissionResult | None:
        """合并工具参数通道 + 命令意图通道，得到一份 file_guard 结果。"""
        fg = file_guard if file_guard is not None else self._file_guard
        accesses = self._collect_file_guard_accesses(
            tool_name,
            tool_args,
            extra_intents,
            file_guard=fg,
        )
        return fg.evaluate_accesses(accesses)

    # ---------- 异步主入口 ----------

    async def check_permission(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_id: str = "web",
        session_id: str | None = None,
    ) -> PermissionResult:
        """检查工具调用权限（按 Guard 管线编排）。"""
        logger.info(
            "[PermissionEngine] permission.check.start tool=%s channel=%s enabled=%s",
            tool_name, channel_id, self._enabled,
        )

        if not isinstance(tool_args, dict):
            logger.warning(
                "[PermissionEngine] tool_args is not a dict (type=%s), using {}",
                type(tool_args).__name__,
            )
            tool_args = {}

        if not self._enabled:
            builtin_deny = self._evaluate_builtin_deny_only(tool_name, tool_args)
            if builtin_deny is not None:
                logger.warning(ToolPermissionLog(
                    tool=tool_name,
                    decision="DENY",
                    source="system",
                    rule=builtin_deny,
                    channel=channel_id or "empty",
                    session_id=session_id or "empty",
                ).to_json(), extra={'component': 'permissions'})
                return PermissionResult(
                    permission=PermissionLevel.DENY,
                    matched_rule=builtin_deny,
                    reason=self._get_reason(PermissionLevel.DENY, tool_name, builtin_deny),
                )
            logger.info(ToolPermissionLog(
                tool="N/A",
                decision="SKIP",
                source="system",
                rule="system_disabled",
                channel=channel_id or "empty",
                session_id=session_id or "empty",
            ).to_json(), extra={'component': 'permissions'})
            return PermissionResult(
                permission=PermissionLevel.ALLOW,
                reason="Permission system is disabled",
            )

        normalized_channel = (channel_id or "").strip() or "web"
        if normalized_channel not in PERMISSION_ENABLED_CHANNELS:
            logger.info(ToolPermissionLog(
                tool="N/A",
                decision="SKIP",
                source="system",
                rule="channel_filter",
                channel=normalized_channel,
                session_id=session_id or "empty",
            ).to_json(), extra={'component': 'permissions'})
            return PermissionResult(
                permission=PermissionLevel.ALLOW,
                reason=f"Skipped for channel: {normalized_channel}",
            )

        eval_config = self._evaluation_config(session_id=session_id)
        eval_file_guard = self._evaluation_file_guard(eval_config)

        # L1 + L3-Cmd 命令意图（仅对 shell / code 类工具有意义；其他工具返回空）
        extra_intents: list[CommandIntent] = []
        if is_command_intent_enabled(eval_config):
            try:
                extra_intents = await collect_command_intents(
                    tool_name,
                    tool_args,
                    eval_file_guard.workspace_root(),
                    eval_config,
                    llm=self._llm,
                    model_name=self._model_name,
                )
            except Exception:  # noqa: BLE001 — never let intent extraction crash policy
                logger.warning(
                    "[PermissionEngine] command_intent.collect_failed tool=%s",
                    tool_name,
                    exc_info=True,
                )
                extra_intents = []

        permission, matched_rule, subcommand_results, file_operations = (
            self.evaluate_global_policy_with_details(
                tool_name,
                tool_args,
                channel_id,
                include_external_directory=True,
                extra_intents=extra_intents or None,
                session_id=session_id,
                config=eval_config,
                file_guard=eval_file_guard,
            )
        )

        if permission is None:
            permission = PermissionLevel.ASK
            matched_rule = matched_rule or "tiered_policy:fallback(no_baseline)"

        external_paths = [op.path for op in file_operations] if file_operations else None

        logger.info(
            "[PermissionEngine] permission.policy.result tool=%s permission=%s matched_rule=%s "
            "subcommand_results=%s file_operations=%s",
            tool_name,
            permission.value, matched_rule,
            [(item.text, item.permission.value) for item in subcommand_results]
            if subcommand_results else [],
            [(op.action, op.path, op.source) for op in file_operations]
            if file_operations else [],
        )

        result = PermissionResult(
            permission=permission,
            matched_rule=matched_rule,
            reason=self._get_reason(permission, tool_name, matched_rule),
            risk=None,
            external_paths=external_paths,
            subcommand_results=subcommand_results,
            file_operations=file_operations,
        )

        logger.info(
            "[PermissionEngine] permission.check.final tool=%s channel=%s permission=%s matched_rule=%s "
            "external_paths=%s",
            tool_name,
            channel_id,
            permission.value,
            matched_rule,
            external_paths or [],
        )
        return result

    @staticmethod
    def _evaluate_builtin_deny_only(
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> str | None:
        """Evaluate builtin dangerous-command deny regardless of enabled toggle.

        Use an empty policy config so only builtin rules can participate.
        """
        permission, matched_rule, _ = evaluate_tiered_policy_detailed({}, tool_name, tool_args)
        if permission != PermissionLevel.DENY:
            return None
        if not isinstance(matched_rule, str):
            return None
        if not matched_rule.startswith("tiered_policy:whole_command_deny:"):
            return None
        if "builtin[" not in matched_rule:
            return None
        return matched_rule

    # ---------- 辅助 ----------

    @staticmethod
    def _get_reason(
        permission: PermissionLevel, tool_name: str, matched_rule: str
    ) -> str:
        if permission == PermissionLevel.ALLOW:
            return f"Allowed by rule: {matched_rule}"
        if permission == PermissionLevel.DENY:
            builtin_reason = PermissionEngine._format_builtin_deny_reason(matched_rule)
            if builtin_reason is not None:
                return builtin_reason
            return f"Denied by rule: {matched_rule}"
        return f"Approval required for {tool_name} (rule: {matched_rule})"

    @staticmethod
    def _format_builtin_deny_reason(matched_rule: str | None) -> str | None:
        if not isinstance(matched_rule, str):
            return None
        if "tiered_policy:whole_command_deny:" not in matched_rule:
            return None
        builtin_ids = re.findall(r"builtin\[([^\]]+)\]", matched_rule)
        if not builtin_ids:
            return None

        descriptions_by_id: dict[str, str] = {}
        for rule in get_builtin_security_rules():
            if not isinstance(rule, dict):
                continue
            rid = str(rule.get("id") or "").strip()
            if not rid:
                continue
            desc = str(rule.get("description") or "").strip()
            if desc:
                descriptions_by_id[rid] = desc

        details = []
        for rid in sorted(set(builtin_ids)):
            desc = descriptions_by_id.get(rid)
            details.append(f"{rid}: {desc}" if desc else rid)
        return f"Denied by builtin dangerous rules: {'; '.join(details)}"


# ----- 全局单例 -----
_permission_engine: PermissionEngine | None = None


def init_permission_engine(config: dict | None = None) -> PermissionEngine:
    """初始化全局权限引擎。"""
    global _permission_engine
    if _permission_engine is None:
        _permission_engine = PermissionEngine(config)
    if config is not None:
        _permission_engine.update_config(config)
    return _permission_engine


def get_permission_engine() -> PermissionEngine:
    """获取全局权限引擎实例（懒初始化）。"""
    global _permission_engine
    if _permission_engine is None:
        _permission_engine = PermissionEngine()
    return _permission_engine


def set_permission_engine(engine: PermissionEngine):
    """替换全局权限引擎（测试用）。"""
    global _permission_engine
    _permission_engine = engine
