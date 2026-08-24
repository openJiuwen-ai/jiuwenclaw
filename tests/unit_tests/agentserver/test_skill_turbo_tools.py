# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contracts for the skill_acceleration_exec tool wrapper."""

from __future__ import annotations

from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import skill_turbo


def test_skill_acceleration_exec_defers_timeout_to_pipeline() -> None:
    assert skill_turbo.card.properties["resilience"]["timeout_s"] is None
