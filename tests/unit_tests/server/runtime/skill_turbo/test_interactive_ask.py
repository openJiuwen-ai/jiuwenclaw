# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Guided mode must be readable from SkillTurbo inputs / resume params."""

from __future__ import annotations

from jiuwenswarm.server.runtime.skill_turbo.interactive_ask import (
    apply_interactive_ask_to_inputs,
    resolve_interactive_ask_from_inputs,
)


class TestResolveInteractiveAskFromInputs:
    def test_reads_metadata_flag(self):
        assert resolve_interactive_ask_from_inputs(
            {"metadata": {"interactive_ask": True}}
        ) is True

    def test_reads_top_level_camel_case(self):
        assert resolve_interactive_ask_from_inputs({"interactiveAsk": True}) is True

    def test_false_when_declared_off(self):
        assert resolve_interactive_ask_from_inputs(
            {"metadata": {"interactive_ask": False}}
        ) is False

    def test_none_when_absent(self):
        assert resolve_interactive_ask_from_inputs({"metadata": {"session_id": "x"}}) is None
        assert resolve_interactive_ask_from_inputs(None) is None


class TestApplyInteractiveAskToInputs:
    def test_stamps_metadata_when_param_present(self):
        inputs = {"metadata": {"session_id": "s1"}}
        merged = apply_interactive_ask_to_inputs(inputs, True)
        assert merged["metadata"]["interactive_ask"] is True
        assert merged["metadata"]["session_id"] == "s1"
        assert "interactive_ask" not in inputs["metadata"]

    def test_leaves_inputs_when_param_absent(self):
        inputs = {"metadata": {"session_id": "s1"}}
        merged = apply_interactive_ask_to_inputs(inputs, None)
        assert merged["metadata"] == {"session_id": "s1"}

    def test_resume_overlay_keeps_saved_slots(self):
        saved = {
            "topic": "南京旅游",
            "metadata": {"interactive_ask": True, "session_id": "s1"},
        }
        merged = apply_interactive_ask_to_inputs(saved, True)
        assert merged["topic"] == "南京旅游"
        assert merged["metadata"]["interactive_ask"] is True
