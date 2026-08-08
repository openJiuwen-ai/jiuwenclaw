# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""日志脱敏引擎单测（不依赖 packages/jiuwenclaw-ee）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jiuwenswarm.infrastructure.log_masking.engine import (
    LogMaskingEngine,
    _KV_SENSITIVE_PATTERN,
    validate_pattern,
)
from jiuwenswarm.infrastructure.log_masking.probes import LOG_MASKING_PROBE_SAMPLES


@pytest.fixture(autouse=True)
def _reset_engine():
    LogMaskingEngine.reset_for_tests()
    yield
    LogMaskingEngine.reset_for_tests()


def test_builtin_sanitize_masks_email_and_kv():
    engine = LogMaskingEngine.get_instance()
    assert "******" in engine.sanitize("contact user@example.com")
    assert "******" in engine.sanitize("password=mySecret&user=alice")
    assert not engine.uses_external_rules


def test_reload_from_rows_sets_external_flag():
    LogMaskingEngine.reload_from_rows(
        [
            {
                "id": 1,
                "rule_id": "custom_ord",
                "rule_name": "order",
                "pattern": r"ORD-\d+",
                "replacement": "[ORD]",
                "priority": 50,
                "enabled": True,
            }
        ],
        db_authoritative=True,
    )
    engine = LogMaskingEngine.get_instance()
    assert engine.uses_external_rules
    assert engine.sanitize("order ORD-1234567890 shipped") == "order [ORD] shipped"


def test_reload_from_rows_empty_falls_back_to_builtin():
    LogMaskingEngine.reload_from_rows([])
    engine = LogMaskingEngine.get_instance()
    assert not engine.uses_external_rules
    assert "******" in engine.sanitize("user@example.com")


def test_kv_sensitive_pattern_handles_realistic_samples():
    samples = [
        "'CAT_CAFE_CALLBACK_TOKEN': 'secret-value'",
        '{"api_key": "sk-abc", "note": "ok"}',
        'refresh_token: "eyJhbGciOiJIUzI1NiJ9.payload.sig"',
    ]
    for text in samples:
        assert _KV_SENSITIVE_PATTERN.search(text), text


def test_probe_samples_are_non_empty():
    assert len(LOG_MASKING_PROBE_SAMPLES) >= 5


def test_validate_pattern_rejects_unsafe_structures():
    with pytest.raises(
        ValueError,
        match=r"unsafe nested wildcard|too slow",
    ):
        validate_pattern(r"(.*)*")


def test_validate_pattern_allows_simple_custom_pattern():
    assert validate_pattern(r"abc") == "abc"
    assert validate_pattern(r"\b\d{4,6}\b") == r"\b\d{4,6}\b"


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, True),
        ("true", True),
        ("false", False),
    ],
)
def test_log_masking_enabled(monkeypatch, env_value, expected):
    from jiuwenswarm.infrastructure.config import Settings

    monkeypatch.delenv("GATEWAY_LOG_MASKING_ENABLED", raising=False)
    if env_value is None:
        monkeypatch.delenv("LOG_MASK_ENABLED", raising=False)
    else:
        monkeypatch.setenv("LOG_MASK_ENABLED", env_value)
    assert Settings().log_masking_enabled is expected


def test_log_masking_enabled_falls_back_to_gateway_env(monkeypatch):
    from jiuwenswarm.infrastructure.config import Settings

    monkeypatch.delenv("LOG_MASK_ENABLED", raising=False)
    monkeypatch.setenv("GATEWAY_LOG_MASKING_ENABLED", "false")
    assert Settings().log_masking_enabled is False


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, True),
        ("true", True),
        ("false", False),
    ],
)
def test_log_to_file_enabled(monkeypatch, env_value, expected):
    from jiuwenswarm.infrastructure.config import Settings

    if env_value is None:
        monkeypatch.delenv("LOG_TO_FILE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("LOG_TO_FILE_ENABLED", env_value)
    assert Settings().log_to_file_enabled is expected


@pytest.mark.asyncio
async def test_reload_log_masking_rule_skips_gdb_in_standalone(monkeypatch):
    """单机版不连 GDB，直接回退内置规则。"""
    import jiuwenswarm.infrastructure.log_masking.engine as engine_mod
    from jiuwenswarm.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "")
    monkeypatch.setattr(engine_mod, "settings", Settings())

    with (
        patch.object(
            LogMaskingEngine,
            "list_enabled_log_masking_rule_rows",
            new_callable=AsyncMock,
        ) as list_rows,
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule()

    list_rows.assert_not_called()
    reload_from_rows.assert_called_once_with([])


@pytest.mark.asyncio
async def test_reload_log_masking_rule_reads_gdb_in_enterprise(monkeypatch):
    """企业版 GDB 冷启动只 reload 引擎。"""
    import jiuwenswarm.infrastructure.log_masking.engine as engine_mod
    from jiuwenswarm.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "enterprise")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-test")
    monkeypatch.setattr(engine_mod, "settings", Settings())

    with (
        patch.object(
            LogMaskingEngine,
            "list_enabled_log_masking_rule_rows",
            new_callable=AsyncMock,
            return_value=[],
        ) as list_rows,
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule()

    list_rows.assert_awaited_once()
    reload_from_rows.assert_called_once_with([], db_authoritative=False)


@pytest.mark.asyncio
async def test_reload_log_masking_rule_db_authoritative_when_rows_present(monkeypatch):
    import jiuwenswarm.infrastructure.log_masking.engine as engine_mod
    from jiuwenswarm.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "enterprise")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-test")
    monkeypatch.setattr(engine_mod, "settings", Settings())
    rows = [{"rule_id": "r1", "pattern": r"X", "replacement": "Y", "enabled": True}]

    with (
        patch.object(
            LogMaskingEngine,
            "list_enabled_log_masking_rule_rows",
            new_callable=AsyncMock,
            return_value=rows,
        ),
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule()

    reload_from_rows.assert_called_once_with(rows, db_authoritative=True)


@pytest.mark.asyncio
async def test_reload_log_masking_rule_skips_gdb_without_jiuwenclaw_id(monkeypatch):
    """企业版 ``JIUWENCLAW_ID`` 未就绪时不访问 GDB，保留内置规则。"""
    import jiuwenswarm.infrastructure.log_masking.engine as engine_mod
    from jiuwenswarm.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "enterprise")
    monkeypatch.delenv("JIUWENCLAW_ID", raising=False)
    monkeypatch.delenv("JIUWENSWARM_ID", raising=False)
    monkeypatch.setattr(engine_mod, "settings", Settings())
    LogMaskingEngine.reset_for_tests()

    with (
        patch.object(
            LogMaskingEngine,
            "list_enabled_log_masking_rule_rows",
            new_callable=AsyncMock,
        ) as list_rows,
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule()

    list_rows.assert_not_called()
    reload_from_rows.assert_not_called()
    assert "******" in LogMaskingEngine.get_instance().sanitize("token=abc123")


@pytest.mark.asyncio
async def test_reload_from_gateway_db_loads_rows(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "1")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-1")
    import jiuwenswarm.infrastructure.log_masking.engine as engine_mod
    from jiuwenswarm.infrastructure.config import Settings

    monkeypatch.setattr(engine_mod, "settings", Settings())
    rows = [
        {
            "id": 2,
            "jiuwenclaw_id": "sp-1",
            "rule_id": "custom_phone",
            "rule_name": "phone",
            "pattern": r"1[3-9]\d{9}",
            "replacement": "[PHONE]",
            "priority": 80,
            "enabled": True,
        }
    ]
    with patch.object(
        LogMaskingEngine,
        "list_enabled_log_masking_rule_rows",
        new_callable=AsyncMock,
        return_value=rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule()
    engine = LogMaskingEngine.get_instance()
    assert engine.uses_external_rules
    assert "[PHONE]" in engine.sanitize("call 13800138000 now")
