# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task 3 (teammate-user-mediated-approval): chunk-passthrough gate rules.

Covers the two pure decision functions refactored out of
``_consume_stream_with_query``'s inline chunk filter:

- ``_should_passthrough_teammate_ask`` — (b1) ask 放行. Under
  ``user-mediated`` a teammate ``chat.ask_user_question`` with
  ``source=permission_interrupt`` (the permission-approval interrupt) is
  forwarded to the frontend; every other teammate ask is still filtered
  (routed internally via the leader). Leader asks always pass.
- ``_should_drop_under_hide`` — (b2) hide 调序. ``JIUWENSWARM_TEAM_HIDE_TEAMMATE``
  drops teammate non-ask frames but exempts ``chat.ask_user_question`` so
  teammate approval asks survive hide. Env default OFF; leader never hidden.

``leader-mediated`` (the opt-out) keeps the legacy behaviour: all teammate
asks filtered, hide OFF.
"""

import inspect
import re
from types import SimpleNamespace

from jiuwenswarm.server.runtime.agent_adapter.team_helpers import (
    _should_drop_under_hide,
    _should_passthrough_teammate_ask,
)


def test_user_mediated_passes_permission_interrupt_ask() -> None:
    """user-mediated: teammate 审批 ask (source=permission_interrupt) 放行。"""
    assert _should_passthrough_teammate_ask(
        is_leader=False, chunk_event_type="chat.ask_user_question",
        source="permission_interrupt", team_approval_mode="user-mediated") is True


def test_user_mediated_filters_normal_ask() -> None:
    """普通 teammate ask_user 仍过滤（防误穿透未来非审批 interrupt）。"""
    assert _should_passthrough_teammate_ask(
        is_leader=False, chunk_event_type="chat.ask_user_question",
        source="other", team_approval_mode="user-mediated") is False


def test_leader_mediated_filters_all_teammate_ask() -> None:
    """leader-mediated: 全过滤（逐字不变，opt-out）。"""
    assert _should_passthrough_teammate_ask(
        is_leader=False, chunk_event_type="chat.ask_user_question",
        source="permission_interrupt", team_approval_mode="leader-mediated") is False


def test_hide_teammate_exempts_ask(monkeypatch) -> None:
    """hide ON + user-mediated: teammate 审批 ask 不被 hide 丢。"""
    monkeypatch.setenv("JIUWENSWARM_TEAM_HIDE_TEAMMATE", "true")
    assert _should_drop_under_hide(
        is_leader=False, chunk_event_type="chat.ask_user_question") is False
    assert _should_drop_under_hide(
        is_leader=False, chunk_event_type="chat.message") is True


# ---------------------------------------------------------------------------
# Default-value guard (二轮评审 Important #2): the explicit-mode tests above
# all pass ``team_approval_mode`` explicitly, so flipping the ``team_helpers``
# getattr fallback back to ``leader-mediated`` would leave the suite green
# while silently splitting behaviour. The guard below pins that fallback.
# ---------------------------------------------------------------------------


def test_team_spec_without_approval_mode_defaults_to_passthrough() -> None:
    """Default guard: the ``team_helpers`` call site resolves the approval mode
    via ``getattr(team_spec, "team_approval_mode", "user-mediated")``. When
    ``team_spec`` lacks the field (a mock/legacy spec), the fallback must be
    ``"user-mediated"`` so a teammate ``chat.ask_user_question`` with
    ``source=permission_interrupt`` is forwarded to the frontend (放行), not
    silently routed to the leader.

    A source-level tripwire pins the ``getattr`` fallback literal to
    ``"user-mediated"``; the functional mirror pins the resulting behaviour.
    """
    import jiuwenswarm.server.runtime.agent_adapter.team_helpers as team_helpers_module

    # Source tripwire: team_helpers getattr fallback literal must be user-mediated.
    source = inspect.getsource(team_helpers_module)
    assert re.search(
        r'getattr\(\s*team_spec,\s*"team_approval_mode",\s*"user-mediated"\s*\)',
        source,
        re.DOTALL,
    ), "team_helpers must read team_spec.team_approval_mode with a 'user-mediated' fallback"

    # Behaviour mirror: a team_spec without the field resolves to user-mediated,
    # which放行 a permission_interrupt ask (matches the call site at team_helpers.py).
    team_spec = SimpleNamespace()  # 不带 team_approval_mode 字段
    resolved_mode = getattr(team_spec, "team_approval_mode", "user-mediated")
    assert resolved_mode == "user-mediated"
    assert _should_passthrough_teammate_ask(
        is_leader=False, chunk_event_type="chat.ask_user_question",
        source="permission_interrupt", team_approval_mode=resolved_mode) is True
