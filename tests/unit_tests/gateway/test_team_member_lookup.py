# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for TeamMemberLookup (session_id↔team_name consistency check).

Covers the branch selection after commit fix: deleting the ``team_name is None``
short-circuit makes ``team_name_matches`` the single intercept for team_name
mismatch. The qqq-style misconfiguration must report "mismatch", not be
swallowed into "team not ready".
"""

from __future__ import annotations

from jiuwenswarm.gateway.message_handler.join_exit_handlers import TeamMemberLookup


def test_matches_when_scoped_names_equal() -> None:
    """server 原样回传入参 team_name → 一致，校验通过。"""
    lookup = TeamMemberLookup(
        member_names=["counter-human-2"],
        team_name="jiuwen_team_sess_19f5959b707_4ec56a",
        expected_team_name="jiuwen_team_sess_19f5959b707_4ec56a",
    )
    assert lookup.team_name_matches is True


def test_mismatch_qqq_does_not_match() -> None:
    """qqq 错配：expected 含 qqq，team_name 不含 → 不一致（走"不匹配"拒绝分支，非"未就绪"）。"""
    lookup = TeamMemberLookup(
        member_names=["counter-human-1"],
        team_name="jiuwen_team_sess_19f5959b707_4ec56a",
        expected_team_name="jiuwen_team_sess_19f5959b707_4ec56aqqq",
    )
    assert lookup.team_name_matches is False


def test_empty_expected_does_not_match() -> None:
    """简化格式 /join 解析不出 team_name → expected 为空 → 不判通过。"""
    lookup = TeamMemberLookup(
        member_names=["counter-human-1"],
        team_name="jiuwen_team_sess_19f5959b707_4ec56a",
        expected_team_name="",
    )
    assert lookup.team_name_matches is False


def test_none_team_name_does_not_match() -> None:
    """team_name 入参缺失 → server 回 None → 不判通过（由调用方走"未就绪"）。"""
    lookup = TeamMemberLookup(
        member_names=["counter-human-1"],
        team_name=None,
        expected_team_name="jiuwen_team_sess_19f5959b707_4ec56a",
    )
    assert lookup.team_name_matches is False


def test_malformed_session_ref_sess_in_team_name() -> None:
    """_sess_ 混入 team_name 的畸形 session_ref 不通过一致性校验。

    输入 ``team_jiuwen_team_sess_xxx_session_sess_yyy`` 时解析器按
    ``_session_`` 切分会得到含 ``_sess_xxx`` 的 team_name，与 monitor
    返回的真实 team_name 不匹，/join 应被拒绝。
    """
    lookup = TeamMemberLookup(
        member_names=["human-collaborator"],
        team_name="jiuwen_team",  # monitor 返回的真实 team_name
        expected_team_name="jiuwen_team_sess_19f5959b707_4ec56a",  # 畸形 session_ref 解析出
    )
    assert lookup.team_name_matches is False
