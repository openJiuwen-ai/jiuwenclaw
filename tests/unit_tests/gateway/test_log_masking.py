# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""日志脱敏引擎单测（不依赖 packages/jiuwenclaw-ee）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jiuwenswarm.infrastructure.log_masking.engine import (
    LogMaskingEngine,
    _KV_SENSITIVE_PATTERN,
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
        ]
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


@pytest.mark.asyncio
async def test_reload_from_gateway_db_noop_without_agent_runtime(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    with patch.object(
        LogMaskingEngine, "list_enabled_log_masking_rule_rows", new_callable=AsyncMock
    ) as mock_list:
        await LogMaskingEngine.reload_log_masking_from_gateway_db()
        mock_list.assert_not_called()


@pytest.mark.asyncio
async def test_reload_from_gateway_db_loads_rows(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "1")
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
        await LogMaskingEngine.reload_log_masking_from_gateway_db()
    engine = LogMaskingEngine.get_instance()
    assert engine.uses_external_rules
    assert "[PHONE]" in engine.sanitize("call 13800138000 now")
