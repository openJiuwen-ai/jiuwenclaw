# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""日志脱敏：引擎、GDB 冷启动、Gateway WS 同步与 AgentServer 通知。"""

from __future__ import annotations

import importlib
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.infrastructure.log_masking import reload_from_rows
from jiuwenclaw.infrastructure.log_masking.engine import _KV_SENSITIVE_PATTERN
from jiuwenclaw.infrastructure.log_masking.engine import LogMaskingEngine
from jiuwenclaw.infrastructure.log_masking.probes import LOG_MASKING_PROBE_SAMPLES

_KV_SENSITIVE_REALISTIC_SAMPLES: list[tuple[str, str]] = [
    ("quoted_ok", "'CAT_CAFE_CALLBACK_TOKEN': 'secret-value'"),
    ("json_snippet", '{"api_key": "sk-abc", "note": "ok"}'),
    ("openjiuwen_warn", "field_pattern: Regex pattern (e.g., r'^##\\s*Title\\s+')"),
    ("parser_log", "Registered parser ImageParser for .png"),
    ("many_quotes", ('x="' * 200) + 'token="'),
]


# ---------------------------------------------------------------------------
# EE manager_ws_client 扩展模块（WS sync / notify）
# ---------------------------------------------------------------------------


def _manager_ws_client_root() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "packages/jiuwenclaw-ee/gateway/extensions/manager_ws_client"
    )


def _ensure_package(name: str, path: str) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    sys.modules[name] = pkg


@pytest.fixture(scope="module")
def log_masking_sync_module():
    root = _manager_ws_client_root()
    base = "jiuwenclaw.loaded_extension.manager_ws_client"
    _ensure_package("jiuwenclaw.loaded_extension", str(root.parent.parent.parent))
    _ensure_package(base, str(root))
    _ensure_package(f"{base}.core", str(root / "core"))
    _ensure_package(f"{base}.core.application_config", str(root / "core" / "application_config"))
    _ensure_package(f"{base}.infrastructure", str(root / "infrastructure"))
    _ensure_package(f"{base}.models", str(root / "models"))
    _ensure_package(f"{base}.schemas", str(root / "schemas"))
    return importlib.import_module(
        "jiuwenclaw.loaded_extension.manager_ws_client.core.application_config.log_masking_rule"
    )


def _make_db_handler(**kwargs) -> AsyncMock:
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=kwargs.get("get_row"))
    handler.create = AsyncMock(return_value=kwargs.get("create_row"))
    handler.update = AsyncMock(return_value=kwargs.get("update_row"))
    handler.delete = AsyncMock(return_value=kwargs.get("delete_ok", True))
    handler.list_records = AsyncMock(return_value=kwargs.get("list_rows", []))
    return handler


def _rule_row(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "jiuwenclaw_id": "sp-test",
        "rule_id": "builtin_email",
        "rule_name": "邮箱",
        "description": None,
        "pattern": r"\b[a-z]+@example\.com\b",
        "replacement": "******",
        "priority": 30,
        "source": "builtin",
        "enabled": True,
        "data": None,
        "created_at": None,
        "updated_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _patch_manager_ws_importer():
    db_mod = SimpleNamespace(ensure_db_handler=AsyncMock(return_value=MagicMock()))

    def _import_module(suffix: str):
        if suffix == "infrastructure.db":
            return db_mod
        raise AssertionError(f"unexpected module suffix: {suffix!r}")

    return patch(
        "jiuwenclaw.infrastructure.module_importer.import_manager_ws_client_module",
        side_effect=_import_module,
    )


# ---------------------------------------------------------------------------
# LogMaskingEngine
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_engine():
    LogMaskingEngine.reset_for_tests()
    yield
    LogMaskingEngine.reset_for_tests()


def test_sanitize_email_and_mobile_defaults():
    out = LogMaskingEngine.get_instance().sanitize(
        "contact: user@example.com phone: 13800138000"
    )
    assert "example.com" not in out
    assert "13800138000" not in out
    assert "******" in out


def test_sanitize_kv_password():
    out = LogMaskingEngine.get_instance().sanitize("api_key=sk-secretvalue")
    assert "api_key=" in out
    assert "sk-secretvalue" not in out


def test_sanitize_kv_sensitive_quoted_value_strips_quotes():
    out = LogMaskingEngine.get_instance().sanitize(
        "'CAT_CAFE_CALLBACK_TOKEN': 'secret-value'"
    )
    assert "CAT_CAFE_CALLBACK_TOKEN" in out
    assert "secret-value" not in out
    assert out.endswith(": ******")
    assert "'******'" not in out


_S10_CAT_CAFE_MCP_JSON = next(
    text for label, text in LOG_MASKING_PROBE_SAMPLES if label == "S10"
)


def test_sanitize_s10_cat_cafe_mcp_json_env():
    """探针 S10：cat_cafe_mcp env JSON 中 TOKEN/USER_ID 掩码，CAT_ID 等非敏感键保留。"""
    out = LogMaskingEngine.get_instance().sanitize(_S10_CAT_CAFE_MCP_JSON)
    assert "bafjdksjfksajf" not in out
    assert "wandhfk" not in out
    assert 'OFFICE_CLAN_CALLBACK_TOKEN":******' in out
    assert 'OFEICE_CIAW_USER_ID":******' in out
    assert '"OFEICE_CIAW_CAT_ID":"assistant"' in out


@pytest.mark.parametrize("name,text", _KV_SENSITIVE_REALISTIC_SAMPLES)
def test_kv_sensitive_realistic_samples_sanitize_quick(name: str, text: str):
    """常见日志行：全引擎 sanitize 应在亚秒级完成。"""
    engine = LogMaskingEngine.get_instance()
    t0 = time.perf_counter()
    engine.sanitize(text)
    assert time.perf_counter() - t0 < 0.5, f"slow on sample {name!r}"


@pytest.mark.parametrize("name,text", _KV_SENSITIVE_REALISTIC_SAMPLES)
def test_kv_sensitive_realistic_samples_quoted_secret_masked(name: str, text: str):
    """带引号敏感 KV 样例应脱敏（quoted_ok）或至少不报错。"""
    out = LogMaskingEngine.get_instance().sanitize(text)
    if name == "quoted_ok":
        assert "secret-value" not in out
        assert "******" in out


@pytest.mark.parametrize(
    ("size", "limit_sec"),
    [(5000, 1.0), (20000, 2.0)],
    ids=["5k", "20k"],
)
def test_kv_sensitive_pattern_unclosed_quote_bounded(size: int, limit_sec: float):
    """builtin_kv_sensitive：未闭合引号 + token 关键词，耗时有界（防 ReDoS 回归）。"""
    poison = "a" * size + ' token="'
    t0 = time.perf_counter()
    _KV_SENSITIVE_PATTERN.sub(r"\g<prefix>***", poison)
    assert time.perf_counter() - t0 < limit_sec


@pytest.mark.parametrize(
    ("size", "limit_sec"),
    [(5000, 1.0), (20000, 2.0)],
    ids=["5k", "20k"],
)
def test_sanitize_kv_sensitive_engine_unclosed_quote_bounded(size: int, limit_sec: float):
    """全引擎路径：同上坏样本不得卡死。"""
    poison = "a" * size + ' token="'
    engine = LogMaskingEngine.get_instance()
    t0 = time.perf_counter()
    engine.sanitize(poison)
    assert time.perf_counter() - t0 < limit_sec


def test_reload_from_rows_priority_and_empty_fallback():
    rows = [
        {
            "id": 2,
            "rule_id": "builtin_test",
            "pattern": r"KEEP_ME",
            "replacement": "MASKED",
            "priority": 10,
            "enabled": True,
        },
        {
            "id": 1,
            "rule_id": "custom_first",
            "pattern": r"KEEP_ME",
            "replacement": "FIRST",
            "priority": 100,
            "enabled": True,
        },
    ]
    reload_from_rows(rows)
    assert LogMaskingEngine.get_instance().sanitize("KEEP_ME") == "FIRST"

    reload_from_rows([])
    assert "******" in LogMaskingEngine.get_instance().sanitize("token=abc123")


def test_compile_masking_rows_skips_disabled():
    compiled = LogMaskingEngine.compile_masking_rows(
        [
            {
                "rule_id": "off",
                "pattern": r"SECRET",
                "replacement": "X",
                "priority": 100,
                "enabled": False,
            }
        ]
    )
    assert compiled == []


@pytest.mark.asyncio
async def test_reload_log_masking_from_gateway_db_reads_gdb_only():
    """GDB 冷启动只 reload 引擎，不 notify AgentServer。"""
    with (
        _patch_manager_ws_importer(),
        patch.object(
            LogMaskingEngine,
            "list_enabled_log_masking_rule_rows",
            new_callable=AsyncMock,
            return_value=[],
        ) as list_rows,
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_from_gateway_db()

    list_rows.assert_awaited_once()
    reload_from_rows.assert_called_once_with([])


# ---------------------------------------------------------------------------
# Manager WS config.push → apply_log_masking_rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_log_masking_rule_update_triggers_reload(log_masking_sync_module):
    apply = log_masking_sync_module.apply_log_masking_rule
    existing = _rule_row(enabled=True)
    updated = _rule_row(enabled=False)
    handler = _make_db_handler(get_row=existing, update_row=updated, list_rows=[updated])

    with (
        patch.object(
            log_masking_sync_module,
            "ensure_db_handler",
            AsyncMock(return_value=handler),
        ),
        patch.object(log_masking_sync_module, "get_jiuwenclaw_id", return_value="sp-test"),
        patch.object(
            log_masking_sync_module.LogMaskingEngine,
            "reload_log_masking_from_gateway_db",
            AsyncMock(),
        ) as reload_mock,
    ):
        result = await apply(
            {
                "op": "update",
                "rule_id": "builtin_email",
                "updates": {"enabled": False},
            },
        )

    assert result == {"rule_id": "builtin_email"}
    reload_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_log_masking_rule_sync_replaces_stale_rows(log_masking_sync_module):
    apply = log_masking_sync_module.apply_log_masking_rule
    stale = _rule_row(rule_id="stale_rule", priority=5)
    handler = _make_db_handler(
        get_row=None,
        create_row=_rule_row(rule_id="builtin_email", enabled=False),
        update_row=_rule_row(rule_id="builtin_email", enabled=False),
        list_rows=[_rule_row(rule_id="builtin_email", enabled=False)],
    )
    handler.get = AsyncMock(
        side_effect=lambda _table, filters: (
            stale if filters.get("rule_id") == "stale_rule" else None
        )
    )
    handler.list_records = AsyncMock(return_value=[stale])

    with (
        patch.object(
            log_masking_sync_module,
            "ensure_db_handler",
            AsyncMock(return_value=handler),
        ),
        patch.object(log_masking_sync_module, "get_jiuwenclaw_id", return_value="sp-test"),
        patch.object(
            log_masking_sync_module.LogMaskingEngine,
            "reload_log_masking_from_gateway_db",
            AsyncMock(),
        ) as reload_mock,
    ):
        result = await apply(
            {
                "op": "sync",
                "rules": [
                    {
                        "rule_id": "builtin_email",
                        "rule_name": "邮箱",
                        "pattern": r"\b[a-z]+@example\.com\b",
                        "replacement": "******",
                        "priority": 30,
                        "source": "builtin",
                        "enabled": False,
                    }
                ],
            },
        )

    assert result == {"synced_count": 1, "deleted_count": 1}
    reload_mock.assert_awaited_once()
    handler.delete.assert_awaited_once()
