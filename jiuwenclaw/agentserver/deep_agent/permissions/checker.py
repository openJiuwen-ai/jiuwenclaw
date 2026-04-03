# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限检查器 - 实现具体的权限检查逻辑

优先级规则:

  deny 绝对否决: 任何匹配到的 deny 规则都具有最高优先级。

  来源优先级 (用于 ask/allow 之间的决断):
    1. tools.<tool_name>.patterns[i]   工具级模式规则
    2. tools.<tool_name>.*             工具级默认
    3. defaults.*                       全局默认

  同一工具的 patterns 内部: deny > ask > allow (最严格者胜出)
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import shlex
from pathlib import Path
from typing import Any, Awaitable, Callable, List

from jiuwenclaw.agentserver.deep_agent.permissions.models import (
    PermissionLevel,
    PermissionResult,
)
from jiuwenclaw.agentserver.deep_agent.permissions.patterns import (
    contains_path,
    match_command,
    match_path,
    match_pattern,
    match_url,
)

logger = logging.getLogger(__name__)

# 当前 asyncio Task 的 channel_id（供工具权限判断）；由 interface 在 run_agent 前 set、结束后 reset。
# 不放入 Runner inputs，避免扩展对外契约。
TOOL_PERMISSION_CHANNEL_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_tool_permission_channel_id",
    default="",
)

# 与 jiuwenclaw.channel.web_channel.WebChannel.name / channel_id 一致。
# 仅当 TOOL_PERMISSION_CHANNEL_ID strip 后等于本常量时才做工具权限与 ask 审批；其它通道跳过。
WEB_TOOL_PERMISSIONS_CHANNEL_ID = "web"


# ---------- 工具调用守卫 ----------


async def check_tool_permissions(
    tool_calls: List[Any],
    channel_id: str = "",
    session_id: str | None = None,
    session: Any = None,
    request_approval_callback: Callable[[Any, Any, Any], Awaitable[str]] | None = None,
) -> tuple[List[Any], List[tuple[Any, str]]]:
    """检查每个工具调用的权限，执行前过滤。

    Args:
        tool_calls: 待执行的工具调用列表
        channel_id: 频道 ID；仅 strip 后等于 WEB_TOOL_PERMISSIONS_CHANNEL_ID 时执行校验，否则全量放行
        session_id: 会话 ID
        session: 会话对象，用于发起审批弹窗
        request_approval_callback: 当 needs_approval 时的回调 -> "allow_always"|"allow_once"|"deny"

    Returns:
        (allowed_tool_calls, denied_results)
        - allowed_tool_calls: 通过权限检查的工具调用
        - denied_results: [(tool_call, denial_message), ...] 被拒绝的调用
    """
    from jiuwenclaw.agentserver.deep_agent.permissions.core import get_permission_engine
    engine = get_permission_engine()
    if not engine.enabled:
        return list(tool_calls), []

    normalized_channel_id = (channel_id or "").strip()
    if normalized_channel_id != WEB_TOOL_PERMISSIONS_CHANNEL_ID:
        logger.info(
            "Tool permissions check skipped for channel=%s",
            normalized_channel_id or "(empty)",
        )
        return list(tool_calls), []

    allowed: List[Any] = []
    denied: List[tuple[Any, str]] = []

    for tc in tool_calls:
        tool_name = getattr(tc, "name", "")
        tool_args = getattr(tc, "arguments", {})
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {}

        result = await engine.check_permission(
            tool_name=tool_name,
            tool_args=tool_args,
            channel_id=normalized_channel_id,
            session_id=session_id,
        )

        if result.is_allowed:
            allowed.append(tc)
            logger.warning(
                "Permission ALLOWED: tool=%s, rule=%s",
                tool_name, result.matched_rule,
            )
        elif result.is_denied:
            deny_msg = f"[PERMISSION_DENIED] {result.reason or 'Operation not allowed'}"
            denied.append((tc, deny_msg))
            logger.warning(
                "Permission DENIED: tool=%s, rule=%s",
                tool_name, result.matched_rule,
            )
        elif result.needs_approval:
            logger.warning(
                "Permission needs_approval: tool=%s, rule=%s",
                tool_name, result.matched_rule,
            )
            if session is not None and request_approval_callback is not None:
                decision = await request_approval_callback(session, tc, result)
                if decision == "allow_always":
                    allowed.append(tc)
                    logger.info(
                        "Permission ALWAYS-ALLOW persisted: tool=%s (rule written to config)",
                        tool_name,
                    )
                elif decision == "allow_once":
                    allowed.append(tc)
                    logger.info(
                        "Permission ALLOW-ONCE: tool=%s (no rule persisted)",
                        tool_name,
                    )
                else:
                    denied.append(
                        (tc, "[PERMISSION_REJECTED] User rejected the request.")
                    )
            else:
                denied.append(
                    (tc, f"[APPROVAL_REQUIRED] {result.reason}")
                )

    return allowed, denied


# ---------- 命令风险评估 ----------

_RISK_EVALUATION_PROMPT = (
    """你是一个安全审计专家。请评估以下工具调用的安全风险等级。
    1. 工具名称: {tool_name}。
    2. 调用参数: {tool_args}。
    3. 请严格按照以下 JSON 格式返回（不要输出其他内容）：
    '{{"level": "高|中|低", "explanation": "一句话先解释一下指令功能，再解释风险原因，面向普通用户"}}'
    4. 判断标准：
    - 高风险：从未知URL获取数据、向外部地址发送数据、请求用户凭证/令牌、访问MEMORY.md/USER.md等记忆数据、
             对任意数据进行base64解码、使用exec()/eval()执行外部数据、修改工作目录外的其它文件、
             执行混淆/编码的代码, 请求提权、读取浏览器的cookies/会话数据等、访问凭证相关的文件。
    - 中风险：读取workspace目录外的文件、操作浏览器、操作本地应用、执行代码/脚本。
    - 低风险：读取workspace目录下的文件、查看天气/格式化输出/计算器等不会改变系统状态的操作。
    """
)

_RISK_ICON_MAP = {"高": "\U0001f534", "中": "\U0001f7e1", "低": "\U0001f7e2"}


def assess_command_risk_static(tool_name: str, tool_args: dict | str) -> dict:
    """静态启发式风险评估（LLM 不可用时回退）.

    Returns:
        {"level": "高|中|低", "explanation": "...", "icon": "🔴|🟡|🟢"}
    """
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except Exception:
            tool_args = {}

    cmd = str(tool_args.get("command", tool_args.get("cmd", "")))
    if re.search(
        r"\b(rm\s+-rf|del\s+/[fsq]|format|shutdown|reboot|mkfs|dd\s+if=|>\s*/dev/)",
        cmd, re.IGNORECASE,
    ):
        return {"level": "高", "explanation": "该命令可能造成不可逆的数据丢失或系统损坏", "icon": "\U0001f534"}
    if re.search(
        r"\b(sudo|pip\s+install|npm\s+install|curl.*\|\s*sh|wget.*\|\s*sh|chmod|chown)",
        cmd, re.IGNORECASE,
    ):
        return {"level": "中", "explanation": "该命令涉及权限变更或软件安装", "icon": "\U0001f7e1"}
    if tool_name == "mcp_exec_command":
        return {"level": "中", "explanation": "该命令需要用户确认后执行", "icon": "\U0001f7e1"}
    return {"level": "低", "explanation": "该操作风险较低", "icon": "\U0001f7e2"}


async def assess_command_risk_with_llm(
    llm: Any,
    model_name: str,
    tool_name: str,
    tool_args: dict | str,
) -> dict:
    """使用 LLM 评估工具调用的安全风险.

    Args:
        llm: LLM 实例
        model_name: 模型名
        tool_name: 工具名
        tool_args: 工具参数

    Returns:
        {"level": "高|中|低", "explanation": "...", "icon": "🔴|🟡|🟢"}
        失败时回退到 assess_command_risk_static
    """
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except Exception:
            tool_args = {}

    args_str = ""
    try:
        args_str = json.dumps(tool_args, ensure_ascii=False)[:800]
    except Exception:
        args_str = str(tool_args)[:800]

    prompt = _RISK_EVALUATION_PROMPT.format(tool_name=tool_name, tool_args=args_str)

    try:
        from openjiuwen.core.foundation.llm import UserMessage
        ai_msg = await llm.invoke(
            model=model_name,
            messages=[UserMessage(content=prompt)],
        )
        raw = ai_msg.content if hasattr(ai_msg, "content") else str(ai_msg)

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            level = parsed.get("level", "中")
            explanation = parsed.get("explanation", "")
            return {
                "level": level,
                "explanation": explanation,
                "icon": _RISK_ICON_MAP.get(level, "\U0001f7e1"),
            }
    except Exception:
        logger.debug("LLM risk assessment failed, using static fallback", exc_info=True)

    return assess_command_risk_static(tool_name, tool_args)


# ---------- 内部检查器 ----------

# Shell operators that indicate command chaining / injection.
# If a command matches an allow pattern but also contains these operators,
# the permission is escalated from ALLOW → ASK as a safety net.
_SHELL_OPERATORS_RE = re.compile(
    r'[;&|`<>]'    # ; & | ` < > (covers &&, ||, pipes, redirects, backticks)
    r'|\$[({]'     # $( or ${ — command / variable substitution
    r'|\r?\n'      # newline injection
)
_COMMAND_EXEC_TOOLS = frozenset({"mcp_exec_command"})

# 会操作路径的命令（需做外部目录检测）
_PATH_AWARE_COMMANDS = frozenset({
    "cd", "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown", "cat",
    "ls", "dir", "type", "del", "rd", "copy", "move", "md", "rd",
    "head", "tail", "more", "less", "vim", "nano", "gedit", "notepad",
})


def _extract_paths_from_command(command: str, workdir: str | Path) -> list[Path]:
    """从命令字符串中提取可能为路径的参数，并解析为绝对路径."""
    if not command or not isinstance(command, str):
        return []
    try:
        tokens = shlex.split(command.strip(), posix=False)
    except ValueError:
        tokens = command.strip().split()
    if not tokens:
        return []
    cmd = tokens[0].lower()
    logger.info("[_extract_paths_from_command] command=%s cmd=%s _PATH_AWARE_COMMANDS=%s", command, cmd, cmd in _PATH_AWARE_COMMANDS)
    if cmd not in _PATH_AWARE_COMMANDS:
        return []
    base = Path(workdir).resolve()
    logger.info("[_extract_paths_from_command] base=%s", base)
    paths: list[Path] = []
    for tok in tokens[1:]:
        tok = tok.strip().strip('"').strip("'")
        if not tok or tok.startswith("-"):
            continue
        if not _looks_like_path(tok):
            continue
        p = Path(tok)
        if not p.is_absolute():
            p = base / tok
        paths.append(p.resolve())
    logger.info("[_extract_paths_from_command] extracted paths=%s", paths)
    return paths


def _looks_like_path(token: str) -> bool:
    if token.startswith(("\\\\", "./", "../")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    return "\\" in token or "/" in token


# ---------- 外部目录检查器 ----------


class ExternalDirectoryChecker:
    """检查命令是否访问 workspace 外路径，若越界则触发 external_directory 权限."""

    def __init__(self, config: dict, workspace_root: Path | None = None):
        self.config = config
        self._workspace_root = workspace_root

    def check_external_paths(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PermissionResult | None:
        """若访问了 workspace 外路径，根据 external_directory 配置返回 DENY/ASK；否则返回 None."""
        if tool_name not in ("mcp_exec_command", "bash"):
            return None
        workspace = self._workspace_root
        if workspace is None:
            try:
                from jiuwenclaw.utils import get_agent_workspace_dir
                workspace = get_agent_workspace_dir()
                logger.info("[ExternalDirectoryChecker] workspace from get_agent_workspace_dir: %s", workspace)
            except ImportError:
                logger.error("[ExternalDirectoryChecker] Failed to import get_agent_workspace_dir")
                return None
        else:
            logger.info("[ExternalDirectoryChecker] workspace from _workspace_root: %s", workspace)
        workdir = tool_args.get("workdir", "")
        try:
            workdir_resolved = (workspace / workdir).resolve()
        except (OSError, RuntimeError):
            workdir_resolved = workspace
        cmd = str(tool_args.get("command", "") or tool_args.get("cmd", ""))
        logger.info("[ExternalDirectoryChecker] tool_name=%s cmd=%s workdir=%s", tool_name, cmd, workdir_resolved)
        paths = _extract_paths_from_command(cmd, workdir_resolved)
        logger.info("[ExternalDirectoryChecker] extracted paths: %s", paths)
        external = [p for p in paths if not contains_path(workspace, p)]
        logger.info("[ExternalDirectoryChecker] external paths: %s", external)
        if not external:
            return None
        ext_paths_str = [str(p).replace("\\", "/") for p in external]
        ext_cfg = self.config.get("external_directory", {})
        if isinstance(ext_cfg, str):
            action = ext_cfg
        else:
            action = ext_cfg.get("*", "ask")
            # 若所有外部路径都在某条 allow 规则下，则放行
            # 使用 contains_path 做路径包含判断，避免 "C:" 等短前缀误匹配任意路径
            all_allowed = True
            for path_str in ext_paths_str:
                path_covered = False
                for cfg_path, cfg_action in ext_cfg.items():
                    if cfg_path == "*" or cfg_action != "allow":
                        continue
                    cfg_path_norm = str(cfg_path).replace("\\", "/").rstrip("/")
                    # 跳过过短前缀（如 "C:" 会匹配 C 盘下任意路径）
                    if "/" not in cfg_path_norm:
                        continue
                    if contains_path(cfg_path_norm, path_str):
                        path_covered = True
                        break
                if not path_covered:
                    all_allowed = False
                    break
            if all_allowed:
                action = "allow"
        if action == "deny":
            return PermissionResult(
                permission=PermissionLevel.DENY,
                reason=f"Access to paths outside workspace is denied: {external[0]}",
                matched_rule="external_directory.*",
                external_paths=ext_paths_str,
            )
        if action == "ask":
            return PermissionResult(
                permission=PermissionLevel.ASK,
                reason=f"Access to paths outside workspace requires approval: {external[0]}",
                matched_rule="external_directory.*",
                external_paths=ext_paths_str,
            )
        return None


# ---------- 工具权限检查器 ----------

class ToolPermissionChecker:
    """按 deny > ask > allow 优先级匹配工具权限规则."""

    _LEVEL_STRICTNESS: dict[PermissionLevel, int] = {
        PermissionLevel.DENY: 0,
        PermissionLevel.ASK: 1,
        PermissionLevel.ALLOW: 2,
    }

    def __init__(self, config: dict):
        self.config = config

    def check_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_id: str = "web",
    ) -> tuple[PermissionLevel | None, str | None]:
        """按来源优先级匹配，deny 拥有绝对否决权.

        1. 收集所有来源的匹配结果
        2. 如果任何来源返回 DENY → 立即返回 DENY（绝对否决）
        3. 否则返回来源优先级最高的结果（ask/allow 按来源排序）
        """
        checks = [
            lambda: self._check_tool_pattern_rules(tool_name, tool_args),
            lambda: self._check_tool_default(tool_name),
            lambda: self._check_global_default(),
        ]

        first_match: tuple[PermissionLevel, str] | None = None
        deny_match: tuple[PermissionLevel, str] | None = None

        for fn in checks:
            result, rule = fn()
            if result is not None:
                if first_match is None:
                    first_match = (result, rule)
                if result == PermissionLevel.DENY and deny_match is None:
                    deny_match = (result, rule)

        if deny_match is not None:
            return deny_match

        return first_match if first_match is not None else (None, None)

    # -- 工具级模式规则 --
    def _check_tool_pattern_rules(
        self, tool_name: str, tool_args: dict[str, Any]
    ) -> tuple[PermissionLevel | None, str | None]:
        tools_cfg = self.config.get("tools", {})
        if tool_name not in tools_cfg:
            return None, None
        return self._check_tool_config(tools_cfg[tool_name], tool_args, f"tools.{tool_name}")

    # -- 工具级默认 --
    def _check_tool_default(self, tool_name: str) -> tuple[PermissionLevel | None, str | None]:
        tools_cfg = self.config.get("tools", {})
        if tool_name in tools_cfg and isinstance(tools_cfg[tool_name], str):
            return PermissionLevel(tools_cfg[tool_name]), f"tools.{tool_name}"
        return None, None

    # -- 全局默认 --
    def _check_global_default(self) -> tuple[PermissionLevel | None, str | None]:
        defaults_cfg = self.config.get("defaults", {})
        if "*" in defaults_cfg:
            return PermissionLevel(defaults_cfg["*"]), "defaults.*"
        return None, None

    # -- 辅助方法 --
    def _check_tool_config(
        self, tool_config: Any, tool_args: dict[str, Any], rule_prefix: str
    ) -> tuple[PermissionLevel | None, str | None]:
        """解析工具配置 (字符串或字典)."""
        if isinstance(tool_config, str):
            return PermissionLevel(tool_config), rule_prefix

        if isinstance(tool_config, dict):
            if "patterns" in tool_config:
                matched: list[tuple[PermissionLevel, str]] = []
                patterns_raw = tool_config["patterns"]
                if isinstance(patterns_raw, dict):
                    # 格式: "git status *": "allow"
                    for pattern, perm in patterns_raw.items():
                        if self._match_args_pattern(pattern, tool_args):
                            matched.append(
                                (PermissionLevel(perm), f"{rule_prefix}.patterns[{pattern!r}]")
                            )
                if matched:
                    matched.sort(
                        key=lambda r: self._LEVEL_STRICTNESS.get(r[0], 99)
                    )
                    return matched[0]

            if "*" in tool_config:
                return PermissionLevel(tool_config["*"]), f"{rule_prefix}.*"

        return None, None

    def _match_pattern_config(
        self, pattern_config: dict, tool_args: dict[str, Any]
    ) -> PermissionLevel | None:
        pattern = pattern_config.get("pattern", "")
        permission = pattern_config.get("permission")
        if not pattern or not permission:
            return None
        if self._match_args_pattern(pattern, tool_args):
            return PermissionLevel(permission)
        return None

    @staticmethod
    def _match_args_pattern(pattern: str, tool_args: dict[str, Any]) -> bool:
        """根据参数类型选择不同匹配器."""
        for key, value in tool_args.items():
            if not isinstance(value, str):
                continue
            if key in ("command", "cmd") and match_command(pattern, value):
                return True
            if key == "url" and match_url(pattern, value):
                return True
            if key in {"path", "file_path"} and match_path(pattern, value):
                return True
            if match_pattern(pattern, value):
                return True
        return match_pattern(pattern, str(tool_args))
