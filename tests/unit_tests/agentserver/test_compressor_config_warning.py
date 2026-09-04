# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unsupported compressor config keys must be audible, not silent."""

from __future__ import annotations

import logging

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_deep


@pytest.fixture
def warnings() -> list[str]:
    """Capture straight off the module logger.

    ``caplog`` attaches to the root logger, and the project installs its own
    logging setup with propagation disabled, so root never sees these records.
    Attaching here tests the logger the code actually writes to.
    """
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    logger = interface_deep.logger
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def test_stale_dialogue_config_is_reported(warnings: list[str]) -> None:
    """The exact shape found in a live ~/.jiuwenswarm/config/config.yaml.

    The compressor tree moved to ratio-based triggering; this section was never
    migrated, so every key below is dropped and the processor runs on defaults.
    Before this warning the loss was completely silent.
    """
    stale = {
        "messages_threshold": None,
        "tokens_threshold": 100000,
        "messages_to_keep": 10,
        "keep_last_round": False,
        "compression_target_tokens": 1800,
        "offload_writeback_enabled": False,
    }

    interface_deep._warn_unknown_processor_keys("DialogueCompressor", stale)

    assert len(warnings) == 1
    message = warnings[0]
    for key in stale:
        assert key in message, f"{key} not named in the warning"
    # The user must be told those settings are inert, not merely that keys are odd.
    assert "not applied" in message


def test_partial_stale_config_does_not_claim_whole_section_is_inert(
    warnings: list[str],
) -> None:
    """Valid keys still apply; only the unsupported ones are ignored."""
    interface_deep._warn_unknown_processor_keys(
        "DialogueCompressor",
        {"trigger_context_ratio": 0.8, "tokens_threshold": 100000},
    )

    assert len(warnings) == 1
    message = warnings[0]
    assert "tokens_threshold" in message
    assert "This section is not applied" not in message
    assert "other keys in this section still apply" in message


def test_supported_keys_are_not_reported(warnings: list[str]) -> None:
    interface_deep._warn_unknown_processor_keys(
        "DialogueCompressor",
        {"trigger_context_ratio": 0.8, "min_target_context_ratio": 0.1},
    )
    assert warnings == []


def test_partially_stale_config_reports_only_the_dropped_keys(warnings: list[str]) -> None:
    """A section can be half-migrated; the warning must name only what is lost."""
    interface_deep._warn_unknown_processor_keys(
        "RoundLevelCompressor",
        {"trigger_context_ratio": 0.9, "rounds_threshold": 2},
    )

    assert len(warnings) == 1
    ignored = warnings[0].split("Supported keys:")[0]
    assert "rounds_threshold" in ignored
    assert "trigger_context_ratio" not in ignored


def test_unknown_processor_is_ignored(warnings: list[str]) -> None:
    interface_deep._warn_unknown_processor_keys("NotAProcessor", {"anything": 1})
    assert warnings == []


@pytest.mark.skipif(
    "SessionMemoryCompressor" not in interface_deep._COMPRESSOR_CONFIG_CLASSES,
    reason="SessionMemoryCompressor config class unavailable",
)
def test_session_memory_unknown_keys_are_reported(warnings: list[str]) -> None:
    interface_deep._warn_unknown_processor_keys(
        "SessionMemoryCompressor",
        {"trigger_context_ratio": 0.8, "totally_fake_key": 1},
    )

    assert len(warnings) == 1
    assert "totally_fake_key" in warnings[0]
    assert "trigger_context_ratio" not in warnings[0].split("Supported keys:")[0]


def test_a_diagnostic_never_blocks_startup(monkeypatch, warnings: list[str]) -> None:
    """If the class cannot be imported, stay quiet rather than raise."""
    monkeypatch.setitem(
        interface_deep._COMPRESSOR_CONFIG_CLASSES,
        "DialogueCompressor",
        "no.such.module:NoSuchConfig",
    )
    interface_deep._warn_unknown_processor_keys("DialogueCompressor", {"x": 1})
    assert warnings == []
