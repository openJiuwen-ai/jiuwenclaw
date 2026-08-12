# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests: 团队配置变更换岗简报（dissolve roster_change 标记 → leader 简报注入）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw.agentserver.deep_agent import team_helpers


def _make_spec():
    return SimpleNamespace(
        team_name="oc_team_t-demo",
        leader=SimpleNamespace(member_name="commercial-dd", display_name="商业尽调顾问"),
        predefined_members=[
            SimpleNamespace(member_name="user-research", display_name="用户研究专家"),
            SimpleNamespace(member_name="office", display_name="办公助手"),
        ],
    )


def _make_manager():
    return SimpleNamespace(
        build_session_scoped_team_name=lambda name, sid: f"{name}_{sid}",
    )


# ---------------------------------------------------------------------------
# _wrap_team_roster_change_briefing
# ---------------------------------------------------------------------------
def test_wrap_cn_with_removed_members() -> None:
    wrapped = team_helpers._wrap_team_roster_change_briefing(
        "继续完善文档",
        "cn",
        removed=["逻辑大师"],
        current=["商业尽调顾问", "用户研究专家"],
    )
    assert "【团队配置变更简报】" in wrapped
    assert "逻辑大师" in wrapped
    assert "商业尽调顾问" in wrapped
    assert "build_team" in wrapped
    # 禁止向用户泄漏内部机制的指令必须在
    assert "不要向用户提及" in wrapped
    assert wrapped.rstrip().endswith("继续完善文档")


def test_wrap_cn_without_removed_members() -> None:
    """老会话无旧名单数据：降级为只说当前名单，不出现「已被移除」行。"""
    wrapped = team_helpers._wrap_team_roster_change_briefing(
        "hello",
        "cn",
        removed=[],
        current=["商业尽调顾问"],
    )
    assert "【团队配置变更简报】" in wrapped
    assert "已被移除的成员" not in wrapped
    assert "商业尽调顾问" in wrapped


def test_wrap_en_variant() -> None:
    wrapped = team_helpers._wrap_team_roster_change_briefing(
        "go on",
        "en",
        removed=["Logic Master"],
        current=["DD Advisor"],
    )
    assert "[Team roster change briefing]" in wrapped
    assert "Logic Master" in wrapped
    assert "build_team" in wrapped


def test_wrap_idempotent_and_passthrough() -> None:
    once = team_helpers._wrap_team_roster_change_briefing(
        "q", "cn", removed=[], current=["A"]
    )
    twice = team_helpers._wrap_team_roster_change_briefing(
        once, "cn", removed=[], current=["A"]
    )
    assert twice == once
    assert team_helpers._wrap_team_roster_change_briefing("", "cn", removed=[], current=["A"]) == ""
    non_str = {"query": "x"}
    assert team_helpers._wrap_team_roster_change_briefing(non_str, "cn", removed=[], current=["A"]) is non_str


# ---------------------------------------------------------------------------
# _maybe_wrap_roster_change_briefing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_maybe_wrap_injects_briefing_with_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    """有 roster_change 标记：注入简报，removed = 旧名单 - 新 spec 名单。"""
    tried: list[str] = []

    async def _fake_load(session_id: str, team_name: str):
        tried.append(team_name)
        return {
            "old_roster": [
                {"member_name": "commercial-dd", "display_name": "商业尽调顾问"},
                {"member_name": "assistant", "display_name": "逻辑大师"},
                {"member_name": "user-research", "display_name": "用户研究专家"},
            ],
            "dissolved_at": 1786559832000,
        }

    monkeypatch.setattr(team_helpers, "_load_team_roster_change", _fake_load)

    wrapped = await team_helpers._maybe_wrap_roster_change_briefing(
        team_manager=_make_manager(),
        session_id="sess_1",
        team_spec=_make_spec(),
        query="再写一版",
        language="cn",
    )
    # scoped 名优先，命中后不再 fallback 到 base 名
    assert tried == ["oc_team_t-demo_sess_1"]
    assert "【团队配置变更简报】" in wrapped
    # 逻辑大师（assistant）已从新 spec 移除 → 出现在移除名单
    assert "逻辑大师" in wrapped
    # 商业尽调顾问/用户研究专家仍在新 spec → 不在移除名单
    assert "已被移除的成员：商业尽调顾问" not in wrapped
    assert "用户研究专家、办公助手" in wrapped


@pytest.mark.asyncio
async def test_maybe_wrap_no_marker_returns_query_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 roster_change 标记（含 leader 自行 clean_team 的场景）：不注入简报。"""

    async def _fake_load(session_id: str, team_name: str):
        return None

    monkeypatch.setattr(team_helpers, "_load_team_roster_change", _fake_load)

    query = "正常消息"
    wrapped = await team_helpers._maybe_wrap_roster_change_briefing(
        team_manager=_make_manager(),
        session_id="sess_1",
        team_spec=_make_spec(),
        query=query,
        language="cn",
    )
    assert wrapped == query


@pytest.mark.asyncio
async def test_maybe_wrap_empty_old_roster_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """标记存在但 old_roster 为空（首轮未建团就改了配置）：简报不含移除行。"""

    async def _fake_load(session_id: str, team_name: str):
        return {"old_roster": [], "dissolved_at": 1786559832000}

    monkeypatch.setattr(team_helpers, "_load_team_roster_change", _fake_load)

    wrapped = await team_helpers._maybe_wrap_roster_change_briefing(
        team_manager=_make_manager(),
        session_id="sess_1",
        team_spec=_make_spec(),
        query="q",
        language="cn",
    )
    assert "【团队配置变更简报】" in wrapped
    assert "已被移除的成员" not in wrapped


@pytest.mark.asyncio
async def test_maybe_wrap_falls_back_to_base_team_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """scoped 名读不到桶时 fallback 到 base 名。"""

    async def _fake_load(session_id: str, team_name: str):
        if team_name == "oc_team_t-demo":
            return {"old_roster": [], "dissolved_at": 1}
        return None

    monkeypatch.setattr(team_helpers, "_load_team_roster_change", _fake_load)

    wrapped = await team_helpers._maybe_wrap_roster_change_briefing(
        team_manager=_make_manager(),
        session_id="sess_1",
        team_spec=_make_spec(),
        query="q",
        language="cn",
    )
    assert "【团队配置变更简报】" in wrapped


@pytest.mark.asyncio
async def test_maybe_wrap_load_failure_keeps_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """checkpoint 读取抛错不影响发送：原样返回 query。"""

    async def _fake_load(session_id: str, team_name: str):
        raise RuntimeError("checkpoint down")

    monkeypatch.setattr(team_helpers, "_load_team_roster_change", _fake_load)

    query = "q"
    wrapped = await team_helpers._maybe_wrap_roster_change_briefing(
        team_manager=_make_manager(),
        session_id="sess_1",
        team_spec=_make_spec(),
        query=query,
        language="cn",
    )
    assert wrapped == query
