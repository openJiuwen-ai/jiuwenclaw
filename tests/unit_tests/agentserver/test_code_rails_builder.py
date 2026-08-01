"""Tests for enterprise Code-mode rail assembly."""

from __future__ import annotations

import builtins

from jiuwenclaw.agentserver.deep_agent.code_rails_builder import (
    build_code_mode_extra_rails,
)


class _Adapter:
    def _build_coding_memory_rail(self):
        return None


class _MemoryAdapter:
    def _build_coding_memory_rail(self):
        return type("CodingMemoryRail", (), {})()


def test_memory_enabled_builds_project_and_coding_memory_before_plan_rails() -> None:
    rails = build_code_mode_extra_rails(
        _MemoryAdapter(),
        {"modes": {"code": {"memory": {"enabled": True}}}},
        project_dir=".",
        workspace_dir=".",
    )

    assert [type(rail).__name__ for rail in rails] == [
        "ProjectMemoryRail",
        "CodingMemoryRail",
        "CodeAgentModeRail",
        "CodeConfirmInterruptRail",
        "PlanApprovalInterruptRail",
    ]


def test_memory_disabled_keeps_plan_rails() -> None:
    rails = build_code_mode_extra_rails(
        _Adapter(),
        {"modes": {"code": {"memory": {"enabled": False}}}},
        project_dir=".",
        workspace_dir=".",
    )

    assert [type(rail).__name__ for rail in rails] == [
        "CodeAgentModeRail",
        "CodeConfirmInterruptRail",
        "PlanApprovalInterruptRail",
    ]


def test_coding_memory_failure_does_not_block_plan_rails() -> None:
    class FailingAdapter:
        def _build_coding_memory_rail(self):
            raise RuntimeError("embedding unavailable")

    rails = build_code_mode_extra_rails(
        FailingAdapter(),
        {"modes": {"code": {"memory": {"enabled": True}}}},
        project_dir=".",
        workspace_dir=".",
    )

    assert [type(rail).__name__ for rail in rails] == [
        "ProjectMemoryRail",
        "CodeAgentModeRail",
        "CodeConfirmInterruptRail",
        "PlanApprovalInterruptRail",
    ]


def test_project_memory_failure_does_not_block_plan_rails(monkeypatch) -> None:
    class FailingProjectMemory:
        def __init__(self, **_kwargs):
            raise RuntimeError("workspace unavailable")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.code_rails_builder.ProjectMemoryRail",
        FailingProjectMemory,
    )
    rails = build_code_mode_extra_rails(
        _Adapter(),
        {"modes": {"code": {"memory": {"enabled": True}}}},
        project_dir=".",
        workspace_dir=".",
    )

    assert [type(rail).__name__ for rail in rails] == [
        "CodeAgentModeRail",
        "CodeConfirmInterruptRail",
        "PlanApprovalInterruptRail",
    ]


def test_agent_mode_import_failure_disables_only_plan_rail_group(monkeypatch) -> None:
    original_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == (
            "jiuwenclaw.agentserver.deep_agent.rails.code.code_agent_mode_rail"
        ):
            raise ImportError("AgentModeRail unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    rails = build_code_mode_extra_rails(
        _Adapter(),
        {"modes": {"code": {"memory": {"enabled": False}}}},
        project_dir=".",
        workspace_dir=".",
    )

    assert rails == []


def test_single_plan_rail_failure_does_not_drop_other_plan_rails(monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.code_rails_builder._build_code_confirm_rail",
        lambda: (_ for _ in ()).throw(RuntimeError("confirm unavailable")),
    )

    rails = build_code_mode_extra_rails(
        _Adapter(),
        {"modes": {"code": {"memory": {"enabled": False}}}},
        project_dir=".",
        workspace_dir=".",
    )

    assert [type(rail).__name__ for rail in rails] == [
        "CodeAgentModeRail",
        "PlanApprovalInterruptRail",
    ]
