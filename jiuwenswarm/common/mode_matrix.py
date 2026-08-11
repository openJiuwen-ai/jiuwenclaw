# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""统一的运行模式解析（Web 组合模式 + 历史完整模式）。

背景：仓库里长期存在两个正交概念。

- ``work_mode``：``work`` / ``code``，表示工作环境（项目归属、Git 上下文）。
- ``mode``：决定 Adapter（Deep / Code）、单 agent 还是集群、以及是否 plan。

Web 前端只表达 ``agent`` / ``team`` / ``agent.plan`` 三个值。其中单 agent 的两个
由 ``work_mode`` 决定使用 work profile 还是 code profile，本模块负责把这两个字段
组合成后端既有的 manager mode / sub_mode / canonical mode。Plan 只在单 agent 上
开放，``team`` 不参与组合、走历史解析，集群行为保持原样。

TUI、CLI、IM、cron 等客户端可以直接发送完整模式串。本模块同时负责将正式别名
``team.plan`` 归一为 ``team.plan.normal``，并把两个 Team Plan profile 分别路由到
DeepAdapter（normal）和 CodeAdapter（code）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from jiuwenswarm.common.work_mode import SUPPORTED_WORK_MODES

# Web 组合模式只覆盖单 agent。其余取值一律按历史完整模式处理。
#
# 集群刻意不在这里：Plan 只对单 agent 开放，而集群的 Adapter 选型沿用历史规则
# （``team`` → DeepAdapter），不随 ``work_mode`` 变化。把 ``team`` 交给
# legacy 分支，Web 集群的行为就与改造前逐字节一致。
WEB_BASE_AGENT: str = "agent"
WEB_PLAN_AGENT: str = "agent.plan"

# Team plan 的规范模式与兼容别名。
TEAM_PLAN_NORMAL_MODE: str = "team.plan.normal"
TEAM_PLAN_CODE_MODE: str = "team.plan.code"
MODE_ALIASES: dict[str, str] = {
    "team.plan": TEAM_PLAN_NORMAL_MODE,
}

# ── 新三段命名：8 种 canonical 模式 ──
# <角色>.<环境>.<状态>：work/code 已折叠进 mode 串，不再依赖 work_mode 组合。
# 旧串经 :func:`deprecate_mode` 静默转译到这里，详见模式重构 P1。
NEW_AGENT_WORK_NORMAL = "agent.work.normal"
NEW_AGENT_WORK_PLAN = "agent.work.plan"
NEW_AGENT_CODE_NORMAL = "agent.code.normal"
NEW_AGENT_CODE_PLAN = "agent.code.plan"
NEW_TEAM_WORK_NORMAL = "team.work.normal"
NEW_TEAM_WORK_PLAN = "team.work.plan"
NEW_TEAM_CODE_NORMAL = "team.code.normal"
NEW_TEAM_CODE_PLAN = "team.code.plan"

NEW_CANONICAL_MODES: frozenset[str] = frozenset(
    {
        NEW_AGENT_WORK_NORMAL,
        NEW_AGENT_WORK_PLAN,
        NEW_AGENT_CODE_NORMAL,
        NEW_AGENT_CODE_PLAN,
        NEW_TEAM_WORK_NORMAL,
        NEW_TEAM_WORK_PLAN,
        NEW_TEAM_CODE_NORMAL,
        NEW_TEAM_CODE_PLAN,
    }
)

# 旧 canonical → 新 canonical，静默转译（决策 3：不提示用户）。
# 仅给持久化层把旧串升级用，路由谓词一律走 :func:`is_*` 集合判断，方向不能反。
DEPRECATION_MAP: dict[str, str] = {
    "agent": NEW_AGENT_WORK_NORMAL,
    "agent.plan": NEW_AGENT_WORK_PLAN,
    "agent.fast": NEW_AGENT_WORK_NORMAL,  # 历史已归一 agent
    "code.normal": NEW_AGENT_CODE_NORMAL,
    "code.plan": NEW_AGENT_CODE_PLAN,
    "code.team": NEW_TEAM_CODE_NORMAL,
    "team": NEW_TEAM_WORK_NORMAL,
    TEAM_PLAN_NORMAL_MODE: NEW_TEAM_WORK_PLAN,  # team.plan.normal
    TEAM_PLAN_CODE_MODE: NEW_TEAM_CODE_PLAN,  # team.plan.code
}

# 所有表示"集群"的 canonical 模式。
# P1.5：新 team.* 四个成员就地扩展进来，旧成员保留（现有用例只断言旧串）。
TEAM_CANONICAL_MODES: frozenset[str] = frozenset(
    {
        "team",
        TEAM_PLAN_NORMAL_MODE,
        "code.team",
        TEAM_PLAN_CODE_MODE,
        NEW_TEAM_WORK_NORMAL,
        NEW_TEAM_WORK_PLAN,
        NEW_TEAM_CODE_NORMAL,
        NEW_TEAM_CODE_PLAN,
    }
)

# 所有表示"正处于 plan"的 canonical 模式。
# P1.5：新 4 个 plan 变体就地扩展进来，旧成员保留。
PLAN_CANONICAL_MODES: frozenset[str] = frozenset(
    {
        "agent.plan",
        "code.plan",
        TEAM_PLAN_NORMAL_MODE,
        TEAM_PLAN_CODE_MODE,
        NEW_AGENT_WORK_PLAN,
        NEW_AGENT_CODE_PLAN,
        NEW_TEAM_WORK_PLAN,
        NEW_TEAM_CODE_PLAN,
    }
)

# canonical plan 模式退出 plan 后应回到的普通模式。
# P1.5：新 plan 变体的退出映射就地扩展进来，旧映射保留。
_PLAN_EXIT_MODES: dict[str, str] = {
    "agent.plan": "agent",
    "code.plan": "code.normal",
    TEAM_PLAN_NORMAL_MODE: "team",
    TEAM_PLAN_CODE_MODE: "code.team",
    NEW_AGENT_WORK_PLAN: NEW_AGENT_WORK_NORMAL,
    NEW_AGENT_CODE_PLAN: NEW_AGENT_CODE_NORMAL,
    NEW_TEAM_WORK_PLAN: NEW_TEAM_WORK_NORMAL,
    NEW_TEAM_CODE_PLAN: NEW_TEAM_CODE_NORMAL,
}

# P6.4：Web 组合分支接受的 mode 值集合。
# 旧值 agent / agent.plan（work_mode 决定 profile）+ 新三段 canonical
# （work/code 已折叠进 mode 串，组合分支直通 canonical）。
WEB_COMPOSABLE_MODES: frozenset[str] = frozenset(
    {
        WEB_BASE_AGENT,
        WEB_PLAN_AGENT,
        NEW_AGENT_WORK_NORMAL,
        NEW_AGENT_WORK_PLAN,
        NEW_AGENT_CODE_NORMAL,
        NEW_AGENT_CODE_PLAN,
    }
)

# (mode, work_mode) -> (manager_mode, sub_mode, canonical_mode)
# P6.4：新三段 canonical 串已折叠 work/code，canonical 取 mode_text 自身。
# work_mode 仍作为键参与查表（决策 1 保留作分桶键），但新串的 profile
# 由串本身决定——agent.code.* 配 work_mode=work 时仍走 code profile
# （串优先于 work_mode）。为避免 work_mode 与串自带 profile 冲突时落 None，
# 新串对两种 work_mode 都映射到同一 canonical。
_WEB_MODE_TABLE: dict[tuple[str, str], tuple[str, str | None, str]] = {
    # 旧 Web 组合
    (WEB_BASE_AGENT, "work"): ("agent", None, "agent"),
    (WEB_PLAN_AGENT, "work"): ("agent", "plan", "agent.plan"),
    (WEB_BASE_AGENT, "code"): ("code", "normal", "code.normal"),
    (WEB_PLAN_AGENT, "code"): ("code", "plan", "code.plan"),
    # P6.4：新串直通 canonical（work profile）
    (NEW_AGENT_WORK_NORMAL, "work"): ("agent", None, NEW_AGENT_WORK_NORMAL),
    (NEW_AGENT_WORK_PLAN, "work"): ("agent", "plan", NEW_AGENT_WORK_PLAN),
    (NEW_AGENT_WORK_NORMAL, "code"): ("agent", None, NEW_AGENT_WORK_NORMAL),
    (NEW_AGENT_WORK_PLAN, "code"): ("agent", "plan", NEW_AGENT_WORK_PLAN),
    # P6.4：新串直通 canonical（code profile）
    (NEW_AGENT_CODE_NORMAL, "work"): ("code", None, NEW_AGENT_CODE_NORMAL),
    (NEW_AGENT_CODE_PLAN, "work"): ("code", "plan", NEW_AGENT_CODE_PLAN),
    (NEW_AGENT_CODE_NORMAL, "code"): ("code", None, NEW_AGENT_CODE_NORMAL),
    (NEW_AGENT_CODE_PLAN, "code"): ("code", "plan", NEW_AGENT_CODE_PLAN),
}


@dataclass(frozen=True)
class ResolvedMode:
    """一次请求解析出的完整运行模式视图。

    Attributes:
        manager_mode: AgentManager / adapter 选型用的一级模式。
        sub_mode: 子模式（``normal`` / ``plan`` / ``team`` / None）。
        canonical_mode: 写回 ``params["mode"]`` 的规范值。
        work_mode: 归一化后的 ``work`` / ``code``；历史请求为 None。
        is_plan: 本次请求是否要求处于 plan。
        is_team: 本次请求是否集群模式。
        is_code_profile: 是否使用 CodeAdapter / code 团队 profile。
        profile: 本次请求使用的 profile（``normal`` / ``code``）。
        normal_mode: 退出 plan 后应回到的 canonical 模式。
        from_web_composition: 是否由 Web 的 mode + work_mode 组合而来。
    """

    manager_mode: str
    sub_mode: str | None
    canonical_mode: str
    work_mode: str | None
    is_plan: bool
    is_team: bool
    is_code_profile: bool
    profile: str
    normal_mode: str
    from_web_composition: bool


def normalize_mode_text(raw_mode: Any) -> str:
    """把请求里的 mode 归一成小写字符串，空值回落到 ``agent``。"""
    raw_value = getattr(raw_mode, "value", raw_mode)
    text = raw_value.strip().lower() if isinstance(raw_value, str) else ""
    return text or "agent"


def canonicalize_mode_text(raw_mode: Any) -> str:
    """归一化 mode 文本并解析正式别名。"""
    text = normalize_mode_text(raw_mode)
    return MODE_ALIASES.get(text, text)


def normalize_work_mode(raw: Any) -> str | None:
    """把任意来源的 ``work_mode`` 归一成 ``work`` / ``code``；非法时返回 None。"""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in SUPPORTED_WORK_MODES else None


def read_request_work_mode(params: Mapping[str, Any] | None) -> str | None:
    """读取请求中显式携带的 ``work_mode``；缺失或非法时返回 None。

    只有 Web 会携带该字段，因此它同时充当"这是 Web 组合模式请求"的判据。
    TUI / CLI / IM / cron 都不发送 ``work_mode``，会走历史解析分支。
    """
    if not isinstance(params, Mapping):
        return None
    return normalize_work_mode(params.get("work_mode"))


def is_web_composable_mode(mode_text: str) -> bool:
    """给定 mode 是否参与 Web 的 mode + work_mode 组合。"""
    return mode_text in WEB_COMPOSABLE_MODES


def is_plan_mode(canonical_mode: Any) -> bool:
    """canonical 模式是否处于 plan。"""
    return canonicalize_mode_text(canonical_mode) in PLAN_CANONICAL_MODES


def is_team_mode(canonical_mode: Any) -> bool:
    """canonical 模式是否为集群。"""
    return canonicalize_mode_text(canonical_mode) in TEAM_CANONICAL_MODES


def is_team_plan_mode(mode: Any) -> bool:
    """Return whether *mode* is either normal or code Team Plan.

    P1.5：新 ``team.work.plan`` / ``team.code.plan`` 就地扩展进集合，
    旧成员保留——P4 rail 路由谓词（``code_rails.build_code_agent_mode``）
    依赖此函数对新串返回 True，否则 team plan leader 分支被静默跳过。
    """
    return canonicalize_mode_text(mode) in {
        TEAM_PLAN_NORMAL_MODE,
        TEAM_PLAN_CODE_MODE,
        NEW_TEAM_WORK_PLAN,
        NEW_TEAM_CODE_PLAN,
    }


def is_code_profile_mode(mode: Any) -> bool:
    """Return whether *mode* selects the code profile.

    P1.5：新 ``agent.code.*`` / ``team.code.*`` 四个就地产 code profile 的变体
    就地扩展进集合，旧成员保留。P4 rail 路由依赖此函数对新串返回 True。
    """
    return canonicalize_mode_text(mode) in {
        "code.normal",
        "code.plan",
        "code.team",
        TEAM_PLAN_CODE_MODE,
        NEW_AGENT_CODE_NORMAL,
        NEW_AGENT_CODE_PLAN,
        NEW_TEAM_CODE_NORMAL,
        NEW_TEAM_CODE_PLAN,
    }


def base_mode_without_plan(canonical_mode: Any) -> str:
    """canonical plan 模式退出后应回到的普通模式。非 plan 模式原样返回。"""
    text = canonicalize_mode_text(canonical_mode)
    return _PLAN_EXIT_MODES.get(text, text)


def is_new_canonical_mode(mode: Any) -> bool:
    """是否为新三段命名 canonical（``<角色>.<环境>.<状态>``）。"""
    return canonicalize_mode_text(mode) in NEW_CANONICAL_MODES


def deprecate_mode(mode: Any) -> str:
    """旧 canonical 静默映射到新 canonical；非旧串原样返回。

    方向固定为旧→新，仅用于持久化层把旧串升级。路由谓词请直接走
    :func:`is_team_mode` / :func:`is_plan_mode` 等集合判断——
    P1.5 已把新串加进这些集合，不要在这里把新串降级回旧串。
    """
    text = canonicalize_mode_text(mode)
    return DEPRECATION_MAP.get(text, text)


def is_plan_mode_new(mode: Any) -> bool:
    """新命名下是否 plan（第三段为 ``plan``）。

    与 :func:`is_plan_mode` 的区别：仅认新三段命名串，旧串不在其中。
    """
    text = canonicalize_mode_text(mode)
    return text in NEW_CANONICAL_MODES and text.endswith(".plan")


def base_mode_without_plan_new(canonical_mode: Any) -> str:
    """新命名 plan 退出后的 normal 变体。非新 plan 模式原样返回。"""
    text = canonicalize_mode_text(canonical_mode)
    if text in NEW_CANONICAL_MODES and text.endswith(".plan"):
        return text.removesuffix(".plan") + ".normal"
    return text


def compose_web_mode(mode_text: str, work_mode: str) -> tuple[str, str | None, str] | None:
    """把 Web 的 ``mode`` + ``work_mode`` 组合成后端三元组。

    Args:
        mode_text: 归一化后的 Web mode（agent / agent.plan）。
        work_mode: 归一化后的 work / code。

    Returns:
        ``(manager_mode, sub_mode, canonical_mode)``；不是 Web 组合时返回 None。
    """
    return _WEB_MODE_TABLE.get((mode_text, work_mode))


def resolve_request_mode(
    params: Mapping[str, Any] | None,
    legacy_resolver: Callable[..., tuple[str, str | None, str]],
    *,
    work_mode: Any = None,
) -> ResolvedMode:
    """解析一次请求的运行模式。

    优先走 Web 组合分支；只有当请求同时满足"存在合法 ``work_mode``"和
    "mode 是 ``agent`` / ``agent.plan``"时才生效。其余取值（``team`` 以及
    ``code.plan`` / ``code.team`` / ``team.plan.*`` / ``agent.fast`` 等完整模式串）
    都交给 *legacy_resolver*。

    Args:
        params: 请求 params。
        legacy_resolver: 历史解析函数（``resolve_agent_request_mode``）。
        work_mode: 调用方已解析出的 ``work_mode``（如来自 session metadata），
            优先于 ``params["work_mode"]``；同时透传给 *legacy_resolver*。

    Returns:
        解析后的 :class:`ResolvedMode`。
    """
    raw_mode = params.get("mode") if isinstance(params, Mapping) else None
    mode_text = canonicalize_mode_text(raw_mode)
    work_mode = normalize_work_mode(work_mode) or read_request_work_mode(params)

    if work_mode is not None and is_web_composable_mode(mode_text):
        composed = compose_web_mode(mode_text, work_mode)
        if composed is not None:
            manager_mode, sub_mode, canonical_mode = composed
            # P6.4：新串已折叠 work/code，profile 从串本身解析（agent.code.*
            # 永远是 code profile，即便 work_mode=work）。旧串（agent / agent.plan）
            # 仍按 work_mode 决定 profile。
            is_code = (
                is_code_profile_mode(canonical_mode)
                if is_new_canonical_mode(canonical_mode)
                else work_mode == "code"
            )
            return ResolvedMode(
                manager_mode=manager_mode,
                sub_mode=sub_mode,
                canonical_mode=canonical_mode,
                work_mode=work_mode,
                is_plan=canonical_mode in PLAN_CANONICAL_MODES,
                is_team=canonical_mode in TEAM_CANONICAL_MODES,
                is_code_profile=is_code,
                profile="code" if is_code else "normal",
                normal_mode=base_mode_without_plan(canonical_mode),
                from_web_composition=True,
            )

    manager_mode, sub_mode, canonical_mode = legacy_resolver(
        mode_text, work_mode=work_mode
    )
    return ResolvedMode(
        manager_mode=manager_mode,
        sub_mode=sub_mode,
        canonical_mode=canonical_mode,
        work_mode=work_mode,
        is_plan=canonical_mode in PLAN_CANONICAL_MODES,
        is_team=canonical_mode in TEAM_CANONICAL_MODES,
        is_code_profile=manager_mode == "code",
        profile="code" if manager_mode == "code" else "normal",
        normal_mode=base_mode_without_plan(canonical_mode),
        from_web_composition=False,
    )


__all__ = [
    "PLAN_CANONICAL_MODES",
    "MODE_ALIASES",
    "ResolvedMode",
    "TEAM_PLAN_CODE_MODE",
    "TEAM_PLAN_NORMAL_MODE",
    "TEAM_CANONICAL_MODES",
    "WEB_COMPOSABLE_MODES",
    "NEW_CANONICAL_MODES",
    "DEPRECATION_MAP",
    "NEW_AGENT_WORK_NORMAL",
    "NEW_AGENT_WORK_PLAN",
    "NEW_AGENT_CODE_NORMAL",
    "NEW_AGENT_CODE_PLAN",
    "NEW_TEAM_WORK_NORMAL",
    "NEW_TEAM_WORK_PLAN",
    "NEW_TEAM_CODE_NORMAL",
    "NEW_TEAM_CODE_PLAN",
    "base_mode_without_plan",
    "base_mode_without_plan_new",
    "canonicalize_mode_text",
    "compose_web_mode",
    "deprecate_mode",
    "is_plan_mode",
    "is_plan_mode_new",
    "is_new_canonical_mode",
    "is_code_profile_mode",
    "is_team_plan_mode",
    "is_team_mode",
    "is_web_composable_mode",
    "normalize_mode_text",
    "normalize_work_mode",
    "read_request_work_mode",
    "resolve_request_mode",
]
