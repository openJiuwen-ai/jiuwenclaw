# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""P3.4：/mode 分发改查表 + 前置校验改单一事实源。

覆盖：
- 新 canonical 直通 /mode <new>；
- 旧 canonical / 旧 alias 经 deprecate_mode 静默转译到新 ChannelMode；
- 未知输入被判"非法指令"；
- slash command 解析层对 /mode <新串> 给出 MODE_OK。
"""

from __future__ import annotations

import pytest

from jiuwenswarm.gateway.message_handler.message_handler import (
    ChannelMode,
    _VALID_MODE_INPUTS,
    is_valid_mode_input,
    resolve_channel_mode,
)
from jiuwenswarm.gateway.message_handler.command_parser.slash_command import (
    ParsedControlAction,
    parse_channel_control_text,
)


# ── 前置校验：单一事实源 _VALID_MODE_INPUTS ────────────────────────────────


@pytest.mark.parametrize(
    "mode",
    [
        # 新 canonical
        "agent.work.normal",
        "agent.work.plan",
        "agent.code.normal",
        "agent.code.plan",
        "team.work.normal",
        "team.work.plan",
        "team.code.normal",
        "team.code.plan",
        # 旧 canonical / alias（静默转译源）
        "agent",
        "agent.plan",
        "agent.fast",
        "code.normal",
        "code.plan",
        "code.team",
        "team",
        "team.plan",
        "team.plan.normal",
        "team.plan.code",
    ],
)
def test_is_valid_mode_input_accepts_new_and_legacy(mode):
    assert is_valid_mode_input(mode) is True


@pytest.mark.parametrize("mode", ["", "unknown", "AGEN", "code.rust", "team.devops"])
def test_is_valid_mode_input_rejects_unknown(mode):
    assert is_valid_mode_input(mode) is False


def test_valid_mode_inputs_is_new_union_legacy_union_aliases():
    """单一事实源 = 新 canonical ∪ 旧 canonical（DEPRECATION_MAP.keys）∪ 正式别名（MODE_ALIASES.keys）。"""
    from jiuwenswarm.common.mode_matrix import (
        DEPRECATION_MAP,
        MODE_ALIASES,
        NEW_CANONICAL_MODES,
    )

    assert _VALID_MODE_INPUTS == (
        NEW_CANONICAL_MODES | DEPRECATION_MAP.keys() | MODE_ALIASES.keys()
    )


# ── 分发改查表：旧串→新 ChannelMode，新串直通 ─────────────────────────────


@pytest.mark.parametrize(
    ("input_mode", "expected_mode"),
    [
        # 新串直通
        ("agent.work.plan", ChannelMode.AGENT_WORK_PLAN),
        ("agent.code.normal", ChannelMode.AGENT_CODE_NORMAL),
        ("team.work.plan", ChannelMode.TEAM_WORK_PLAN),
        ("team.code.normal", ChannelMode.TEAM_CODE_NORMAL),
        ("agent.work.normal", ChannelMode.AGENT_WORK_NORMAL),
        # 旧串 → 新 ChannelMode（deprecate_mode 静默转译）
        ("agent", ChannelMode.AGENT_WORK_NORMAL),
        ("agent.plan", ChannelMode.AGENT_WORK_PLAN),
        ("agent.fast", ChannelMode.AGENT_WORK_NORMAL),
        ("code.normal", ChannelMode.AGENT_CODE_NORMAL),
        ("code.plan", ChannelMode.AGENT_CODE_PLAN),
        ("code.team", ChannelMode.TEAM_CODE_NORMAL),
        ("team", ChannelMode.TEAM_WORK_NORMAL),
        ("team.plan", ChannelMode.TEAM_WORK_PLAN),
        ("team.plan.normal", ChannelMode.TEAM_WORK_PLAN),
        ("team.plan.code", ChannelMode.TEAM_CODE_PLAN),
    ],
)
def test_resolve_channel_mode(input_mode, expected_mode):
    assert resolve_channel_mode(input_mode) == expected_mode


def test_resolve_channel_mode_fallback_for_unknown():
    """未命中时回落 ChannelMode.AGENT（调用方应先用 is_valid_mode_input 拦截）。"""
    # 注意：调用方应先用 is_valid_mode_input 拦截未知串，
    # resolve_channel_mode 仅作兜底，不抛异常。
    assert resolve_channel_mode("totally-unknown") == ChannelMode.AGENT


# ── slash 命令解析层：/mode <新串> 走 MODE_OK ───────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "/mode agent.work.plan",
        "/mode team.code.normal",
        "/mode agent.work.normal",
        "/mode team.work.plan",
    ],
)
def test_parse_mode_new_canonical_is_ok(line):
    parsed = parse_channel_control_text(line)
    assert parsed.action is ParsedControlAction.MODE_OK
    assert parsed.mode_subcommand == line.split(" ", 1)[1]


@pytest.mark.parametrize(
    "line",
    [
        "/mode agent",
        "/mode team.plan",
        "/mode code.team",
        "/mode team.plan.code",
    ],
)
def test_parse_mode_legacy_is_ok(line):
    parsed = parse_channel_control_text(line)
    assert parsed.action is ParsedControlAction.MODE_OK


def test_parse_mode_unknown_is_bad():
    parsed = parse_channel_control_text("/mode not.a.mode")
    assert parsed.action is ParsedControlAction.MODE_BAD


# ── ChannelMode 旧成员保留（兼容旧持久化 channel state 反序列化）────────────


@pytest.mark.parametrize(
    "value",
    [
        "agent",
        "agent.plan",
        "agent.fast",
        "code.plan",
        "code.normal",
        "code.team",
        "team",
        "team.plan.normal",
        "team.plan.code",
    ],
)
def test_channel_mode_keeps_legacy_members(value):
    """旧持久化 channel state 的 mode 值仍能反序列化成 ChannelMode。"""
    assert ChannelMode(value).value == value
