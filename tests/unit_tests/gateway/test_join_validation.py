# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for /join mismatch 校验与对外文案（路线 B：文案全在 gateway）.

替代被删的 test_team_member_lookup.py。路线 B 下 mismatch 校验上移到 gateway
本地（join_slash_handler），文案单一真相源在 join_exit_handlers 模块级
_join_err_*。mismatch 判定复用 TeamManager.build_session_scoped_team_name：
team_name 已是 scoped 形式 ⟺ 等于拼接结果。

覆盖用户给出的三 case：
- CASE1/CASE2：team_name 与 session 后缀不一致 → 报"不匹配"
- CASE3：后缀一致但查不到 member → 报"不存在"
"""

from __future__ import annotations

import pytest

from jiuwenswarm.agents.harness.team.team_manager import TeamManager
from jiuwenswarm.gateway.message_handler.join_exit_handlers import (
    _join_err_mismatch,
    _join_err_team_not_exist,
)
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler


def _is_mismatch(team_name: str, session_id: str) -> bool:
    """复刻 join_slash_handler 的本地 mismatch 判定（路线 B）。"""
    if not team_name:
        return True
    return team_name != TeamManager.build_session_scoped_team_name(team_name, session_id)


# ── 文案 helper ──

def test_mismatch_message_contains_team_and_session() -> None:
    msg = _join_err_mismatch("jiuwen_team_sess_X", "sess_Y")
    assert "jiuwen_team_sess_X" in msg
    assert "sess_Y" in msg
    assert "不匹配" in msg
    assert "session_ref" in msg


def test_not_exist_message_contains_team() -> None:
    assert "jiuwen_team_sess_X" in _join_err_team_not_exist("jiuwen_team_sess_X")


def test_not_exist_message_fallback_for_empty_team_name() -> None:
    """team_name 空 → 文案用"未知"兜底，不崩。"""
    msg = _join_err_team_not_exist("")
    assert "未知" in msg


# ── mismatch 判定三 case（用用户给的真实 session_ref）──

_REAL_REF = (
    "team_jiuwen_team_sess_19f608d7a9c_f5ef621_session_sess_19f608d7a9c_f5ef621"
)


def _parse(ref: str) -> tuple[str, str]:
    """从 session_ref 解析 (team_name, session_id)，同 join_slash_handler。"""
    sid = MessageHandler.extract_session_id_from_ref(ref)
    team_name = MessageHandler.extract_team_name_from_ref(ref)
    assert sid is not None
    return team_name, sid


@pytest.mark.parametrize(
    "ref,expect_mismatch",
    [
        # CASE1: team_name 后缀 X ≠ session Y
        ("team_jiuwen_team_sess_19f608d7a9c_f5ef621_session_sess_19f608d7a9c_f5ef62", True),
        # CASE2: 反向 X/Y 互换
        ("team_jiuwen_team_sess_19f608d7a9c_f5ef62_session_sess_19f608d7a9c_f5ef621", True),
        # CASE3: 后缀一致 → 不是 mismatch（走"查 member"，查不到才报"不存在"）
        (_REAL_REF, False),
    ],
)
def test_mismatch_branch(ref: str, expect_mismatch: bool) -> None:
    team_name, sid = _parse(ref)
    assert _is_mismatch(team_name, sid) is expect_mismatch


def test_case3_matched_renders_not_exist_message() -> None:
    """CASE3 后缀一致：mismatch=False，文案应是"不存在"（不是"不匹配"）。"""
    team_name, _sid = _parse(_REAL_REF)
    assert not _is_mismatch(team_name, _sid)
    # 此时查不到 member → join_slash_handler 拼 _join_err_team_not_exist
    msg = _join_err_team_not_exist(team_name)
    assert team_name in msg
    assert "不存在" in msg
