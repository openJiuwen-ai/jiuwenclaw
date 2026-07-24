# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""检视问题修复回归：短路 when、scenario 守卫、异常不得 success:True 等。"""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.skill_turbo.online import (
    context_store,
    flow_scheduler,
)
from jiuwenclaw.agentserver.skill_turbo.online.flow_scheduler import (
    WhenKeyMissingError,
    eval_when,
)
from jiuwenclaw.agentserver.skill_turbo.online.skill_name_guard import (
    InvalidScenarioError,
    validate_scenario,
)
from jiuwenclaw.agentserver.skill_turbo.validator import PlanCodeValidator


@pytest.mark.unit
class TestWhenShortCircuit:
    def test_and_short_circuits_missing_key(self) -> None:
        assert eval_when("false and missing_key", {}) is False

    def test_or_short_circuits_missing_key(self) -> None:
        assert eval_when("true or missing_key", {}) is True

    def test_missing_key_still_raises_when_needed(self) -> None:
        with pytest.raises(WhenKeyMissingError):
            eval_when("missing_key == true", {})


@pytest.mark.unit
class TestScenarioGuard:
    def test_rejects_path_chars(self) -> None:
        with pytest.raises(InvalidScenarioError):
            validate_scenario("../etc")
        with pytest.raises(InvalidScenarioError):
            validate_scenario("foo.bar")

    def test_accepts_normal(self) -> None:
        assert validate_scenario("create_ppt") == "create_ppt"


@pytest.mark.unit
class TestFromDictGuards:
    def test_bad_accumulator_type(self) -> None:
        ctx = context_store.TurboContext.from_dict({
            "task_id": "t1",
            "skill_name": "s",
            "scenario": "create_ppt",
            "turbo_dir": "/tmp",
            "accumulator": "bad",
            "completed": [],
            "retry_count": 1,
            "fallback_nodes": "x",
            "task_progress": [],
        })
        assert ctx.accumulator == {}
        assert ctx.retry_count == {}
        assert ctx.fallback_nodes == []
        assert ctx.task_progress == {}


@pytest.mark.unit
class TestValidatorRelativeImport:
    def test_level2_relative_denied(self) -> None:
        v = PlanCodeValidator.for_turbo_skill_code()
        errors = v.validate("from ..x import y\n")
        assert any("多级相对 import" in e for e in errors)

    def test_level1_relative_allowed(self) -> None:
        v = PlanCodeValidator.for_turbo_skill_code()
        errors = v.validate("from .ppt_common import NODE_DISPLAY_NAMES\n")
        assert not any("相对 import" in e for e in errors)

    def test_file_handler_denied(self) -> None:
        v = PlanCodeValidator.for_turbo_skill_code()
        errors = v.validate("import logging\nlogging.FileHandler('/tmp/x')\n")
        assert any("FileHandler" in e for e in errors)


@pytest.mark.unit
class TestBoolStepGrouping:
    def test_bool_step_treated_as_zero(self) -> None:
        flow = [
            {"plan_name": "a", "step": True},
            {"plan_name": "b", "step": 1},
        ]
        groups = flow_scheduler._group_by_step(flow)
        assert groups[0][0]["plan_name"] == "a"
        assert groups[1][0]["plan_name"] == "b"
