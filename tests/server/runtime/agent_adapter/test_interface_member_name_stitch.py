# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task 4 (teammate-user-mediated-approval): member_name return-path stitch.

``JiuWenSwarm._build_interactive_input_from_answers`` is the sidecar half of
the member_name round-trip: relay (Task 9, c') returns ``member_name`` in the
chat.send params, and this function must stitch it into
``InteractiveInput.member_name`` (sibling field added in Task 1) so the
downstream manager (Task 5) can route by member.

The function is a ``@staticmethod``; these tests call it on the class to stay
isolated from ``JiuWenSwarm`` constructor side effects (SkillManager /
SessionManager / workspace resolution) — semantically identical for a static
method. ``InteractiveInput.__init__`` is custom (no kwargs), so member_name is
assigned after construction.
"""

import pytest

from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

# 被缝字段由 agent-core 侧同一提交引入；CI 锁定的 upstream openjiuwen 可能早于
# 该提交。生产代码在字段缺失时降级为空操作，故缝入断言仅在字段存在时适用。
pytestmark = pytest.mark.skipif(
    not hasattr(InteractiveInput(), "member_name"),
    reason="installed openjiuwen lacks InteractiveInput.member_name; stitch degrades to a guarded no-op",
)


def _permission_answers() -> list[dict]:
    """Permission-approval answer shape (source=permission_interrupt)."""
    return [{"selected_options": ["approve"], "custom_input": ""}]


def test_member_name_stitched_into_interactive_input() -> None:
    """params.member_name 缝进 InteractiveInput.member_name（构造后赋值）。"""
    interactive_input = JiuWenSwarm._build_interactive_input_from_answers(
        request_id="tcid-1",
        answers=_permission_answers(),
        source="permission_interrupt",
        member_name="teammate-1",
    )
    assert isinstance(interactive_input, InteractiveInput)
    assert interactive_input.member_name == "teammate-1"


def test_member_name_none_when_absent() -> None:
    """缺省 member_name → InteractiveInput.member_name 为 None（不用空串）。"""
    interactive_input = JiuWenSwarm._build_interactive_input_from_answers(
        request_id="tcid-1",
        answers=_permission_answers(),
        source="permission_interrupt",
    )
    assert isinstance(interactive_input, InteractiveInput)
    assert interactive_input.member_name is None
