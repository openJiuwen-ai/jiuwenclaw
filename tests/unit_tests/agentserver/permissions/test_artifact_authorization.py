"""Focused contracts for file-delivery authorization decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.agents.harness.common.rails.permissions._auto_permission.artifact_authorization import (
    AutoPermissionArtifactAuthorizationMixin,
    _send_file_execution_grant_for_allow,
    has_user_file_delivery_prohibition,
    is_file_delivery_action,
)
from jiuwenswarm.agents.harness.common.rails.permissions.generated_artifact_delivery import (
    clear_send_file_execution_grant,
    consume_send_file_execution_grant,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    build_tool_decision_facts,
)


class _AuthorizationOwner(AutoPermissionArtifactAuthorizationMixin):
    pass


@pytest.fixture(autouse=True)
def _clear_grant() -> None:
    clear_send_file_execution_grant()
    yield
    clear_send_file_execution_grant()


@pytest.mark.parametrize(
    "intent",
    [
        "Do not send or attach any file.",
        "不要发送附件，只说明结果。",
    ],
)
def test_explicit_file_delivery_prohibition_is_a_hard_signal(intent: str) -> None:
    assert has_user_file_delivery_prohibition(intent)


def test_structural_file_delivery_detection_does_not_depend_on_product_name(
    tmp_path: Path,
) -> None:
    send_facts = build_tool_decision_facts(
        "send_message",
        {"attachments": [str(tmp_path / "report.md")]},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )
    ordinary_facts = build_tool_decision_facts(
        "send_message",
        {"message": "done"},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )

    assert is_file_delivery_action(send_facts)
    assert not is_file_delivery_action(ordinary_facts)


def test_allow_publishes_one_exact_path_and_target_grant(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    facts = build_tool_decision_facts(
        "send_file_to_user",
        {
            "abs_file_path_list": [str(path)],
            "target_channels": ["web", "feishu"],
        },
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
        send_paths=(str(path),),
    )

    assert _AuthorizationOwner()._issue_send_file_authorization(facts) == ""
    grant, error = _send_file_execution_grant_for_allow(facts)
    assert error == ""
    assert grant is not None
    assert consume_send_file_execution_grant(
        (path,), target_channels=["feishu", "web"]
    ) == grant.items


def test_send_allow_without_exact_path_fails_closed(tmp_path: Path) -> None:
    facts = build_tool_decision_facts(
        "send_file_to_user",
        {"abs_file_path_list": []},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )

    assert (
        _AuthorizationOwner()._issue_send_file_authorization(facts)
        == "send_file_authorization_missing_path"
    )
