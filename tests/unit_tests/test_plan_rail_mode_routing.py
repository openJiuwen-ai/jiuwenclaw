# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""P4 门控：rail 路由谓词对新 canonical 串的接通测试。

计划 PLAN_mode_refactor_phased.md P4.4。核心改动在
``code_rails.build_code_agent_mode``：旧 ``ctx.mode == TEAM_PLAN_NORMAL_MODE``
改成 ``is_team_plan_mode(ctx.mode) and not is_code_profile_mode(ctx.mode)``
组合谓词，使新串 ``team.work.plan`` / ``team.code.plan`` 能正确路由到
WorkAgentModeRail / CodeAgentModeRail，而非被静默跳过。

本文件只验路由分发（build_code_agent_mode 的返回类型）+ 铁律：
- work-profile team plan leader（team.work.plan / team.plan.normal）→ WorkAgentModeRail
- code-profile team plan leader（team.code.plan / team.plan.code）→ CodeAgentModeRail
- code 普通成员（agent.code.plan / code.plan）→ CodeAgentModeRail
- teammate 角色一律不挂 rail（返回 None）
- 旧串路由不回归（team.plan.normal 仍走 WorkAgentModeRail）
"""

# pylint: disable=protected-access

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jiuwenswarm.agents.harness.code.rails.code_agent_mode_rail import CodeAgentModeRail
from jiuwenswarm.agents.harness.work.rails.work_agent_mode_rail import WorkAgentModeRail
from jiuwenswarm.agents.swarm import register_swarm_providers
from jiuwenswarm.agents.swarm.context import SwarmBuildContext
from jiuwenswarm.agents.swarm.providers import code_rails


# ── 路由分发：新串 → 正确 rail ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "mode",
    ["team.work.plan", "team.plan.normal"],
)
def test_work_profile_team_plan_leader_routes_to_work_rail(mode: str) -> None:
    """work-profile team plan leader（含新串 team.work.plan）→ WorkAgentModeRail。

    旧 ``== TEAM_PLAN_NORMAL_MODE`` 只认 team.plan.normal，新串 team.work.plan
    会被静默跳过。P4 改用 is_team_plan_mode 谓词（P1.5 已扩集合含新串）后
    新串也能命中。
    """
    register_swarm_providers()
    ctx = SwarmBuildContext(
        mode=mode,
        role="leader",
        config={"preferred_language": "zh"},
    )

    rail = code_rails.build_code_agent_mode({}, ctx)

    assert isinstance(rail, WorkAgentModeRail)


@pytest.mark.parametrize(
    "mode",
    ["team.code.plan", "team.plan.code"],
)
def test_code_profile_team_plan_leader_routes_to_code_rail(mode: str) -> None:
    """code-profile team plan leader（含新串 team.code.plan）→ CodeAgentModeRail。

    is_team_plan_mode 对 team.code.plan 也返 True，但 is_code_profile_mode 同样
    返 True，组合谓词 ``is_team_plan and not is_code_profile`` 为 False，
    落到 :379 ``is_code_profile_mode`` 分支走 CodeAgentModeRail——不能误挂
    WorkAgentModeRail。
    """
    register_swarm_providers()
    ctx = SwarmBuildContext(mode=mode, role="leader")

    rail = code_rails.build_code_agent_mode({}, ctx)

    assert isinstance(rail, CodeAgentModeRail)


@pytest.mark.parametrize(
    "mode",
    ["agent.code.plan", "agent.code.normal", "code.plan", "code.normal", "code.team"],
)
def test_code_profile_modes_route_to_code_rail(mode: str) -> None:
    """agent.code.* / code.* 的 leader 走 CodeAgentModeRail。"""
    register_swarm_providers()
    ctx = SwarmBuildContext(mode=mode, role="leader")

    rail = code_rails.build_code_agent_mode({}, ctx)

    assert isinstance(rail, CodeAgentModeRail)


@pytest.mark.parametrize(
    "mode",
    [
        # 新串 work-profile team plan
        "team.work.plan",
        # 旧串 work-profile team plan
        "team.plan.normal",
    ],
)
def test_teammate_never_gets_work_team_plan_rail(mode: str) -> None:
    """work-profile team plan 的 teammate 不挂 WorkAgentModeRail。

    WorkAgentModeRail 是 team plan leader 专属（含 switch_mode 拦截 + Team
    退出通知），:362 组合谓词带 ``role == "leader"``，teammate 不命中。
    code profile 的 teammate 仍挂 CodeAgentModeRail（见下方用例），那是
    plain code.team teammate 本就有的 rail，不是 team plan 专属。
    """
    register_swarm_providers()
    ctx = SwarmBuildContext(mode=mode, role="teammate")

    rail = code_rails.build_code_agent_mode({}, ctx)

    assert not isinstance(rail, WorkAgentModeRail)


@pytest.mark.parametrize(
    "mode",
    ["team.code.plan", "team.plan.code", "agent.code.plan", "code.team"],
)
def test_code_profile_teammate_keeps_plain_code_rail(mode: str) -> None:
    """code profile teammate 挂的是 plain CodeAgentModeRail（与 code.team teammate 等价）。

    这不是 team plan leader 专属 rail——build_code_agent_mode 的 :379
    ``is_code_profile_mode`` 分支不区分 role，teammate 也挂。对应
    test_swarm_assembly.py:1660 ``test_code_team_plan_teammate_spec_equals_code_team``
    的契约：code team plan 只改 Leader，teammate spec 等于 plain code.team。
    """
    register_swarm_providers()
    ctx = SwarmBuildContext(mode=mode, role="teammate")

    rail = code_rails.build_code_agent_mode({}, ctx)

    assert isinstance(rail, CodeAgentModeRail)


def test_non_code_non_team_plan_leader_returns_none() -> None:
    """agent.work.* / team.work.normal 等非 plan 非 code 模式不挂 rail。"""
    register_swarm_providers()
    for mode in ["agent.work.normal", "agent.work.plan", "team.work.normal", "agent", "team"]:
        ctx = SwarmBuildContext(mode=mode, role="leader")
        assert code_rails.build_code_agent_mode({}, ctx) is None, (
            f"mode={mode} 不该挂 rail"
        )


# ── 复用现有拦截能力（不新建平行机制）──────────────────────────────────────
# 计划 P4.4 test_new_plan_still_blocks_switch_mode_exit / test_new_plan_blocks_non_readonly_tools
# 的本意是验证"新串下 rail 仍拦截 switch_mode 退出 / 非只读写操作"。但 rail
# 实例的拦截行为由 CodeAgentModeRail.before_tool_call 内部读 agent 的
# plan_mode.mode=="plan" 决定，与 build_code_agent_mode 路由用的 mode 串无关——
# 只要路由出的实例是 CodeAgentModeRail（上方 test_code_profile_*_routes_to_code_rail
# 已断言），switch_mode 拦截 / 非只读写拦截就自然继承，不重复造用例。
# 拦截行为本身的覆盖在 tests/unit_tests/agentserver/test_code_agent_mode_rail.py。


def test_new_canonical_routes_same_rail_class_as_legacy() -> None:
    """新旧串路由出的 rail 是同一个类——P4 谓词化不改变 rail 类型，只扩接受的输入。

    这条等价于计划 P4.4 的"新串仍被拦截"：拦截能力是 rail 类的属性，不是
    mode 串的属性；路由对 → rail 类对 → 拦截自然在。
    """
    register_swarm_providers()

    new_work = code_rails.build_code_agent_mode(
        {}, SwarmBuildContext(mode="team.work.plan", role="leader")
    )
    legacy_work = code_rails.build_code_agent_mode(
        {}, SwarmBuildContext(mode="team.plan.normal", role="leader")
    )
    assert type(new_work) is type(legacy_work) is WorkAgentModeRail

    new_code = code_rails.build_code_agent_mode(
        {}, SwarmBuildContext(mode="team.code.plan", role="leader")
    )
    legacy_code = code_rails.build_code_agent_mode(
        {}, SwarmBuildContext(mode="team.plan.code", role="leader")
    )
    assert type(new_code) is type(legacy_code) is CodeAgentModeRail
