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


@pytest.fixture(autouse=True)
def _isolate_log_masking_dotenv(monkeypatch):
    """单测不读仓库 .env，仅依赖 monkeypatch 的环境变量。"""
    monkeypatch.setattr(
        "jiuwenclaw.infrastructure.config._resolve_env_files",
        lambda: (),
    )


def test_expand_sensitive_kw_literals_matches_regex_variants():
    from jiuwenclaw.infrastructure.log_masking.engine import (
        _SENSITIVE_KW,
        _SENSITIVE_KW_LITERALS,
        _expand_sensitive_kw_literals,
    )

    assert _SENSITIVE_KW_LITERALS == _expand_sensitive_kw_literals(_SENSITIVE_KW)
    assert "user-id" in _SENSITIVE_KW_LITERALS
    assert "api_key" in _SENSITIVE_KW_LITERALS
    assert "apikey" in _SENSITIVE_KW_LITERALS


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


def test_sanitize_kv_user_id_hyphen_form():
    out = LogMaskingEngine.get_instance().sanitize("user-id=secret123")
    assert "user-id=" in out
    assert "secret123" not in out


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
def test_sanitize_kv_sensitive_engine_unclosed_quote_bounded(size: int, limit_sec: float):
    """全引擎路径：同上坏样本不得卡死。"""
    poison = "a" * size + ' token="'
    engine = LogMaskingEngine.get_instance()
    t0 = time.perf_counter()
    engine.sanitize(poison)
    assert time.perf_counter() - t0 < limit_sec


def test_reload_from_rows_priority_and_empty_fallback(monkeypatch):
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "")
    monkeypatch.setattr(engine_mod, "settings", Settings())
    LogMaskingEngine.reset_for_tests()

    rows = [
        {
            "id": 2,
            "rule_id": "builtin_test",
            "pattern": r"KEEP_ME",
            "replacement": "MASKED",
            "priority": 10,
            "enabled": True,
            "source": "builtin",
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


def test_reload_from_rows_empty_enterprise_no_builtin_fallback(monkeypatch):
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "jiuwen")
    monkeypatch.setattr(engine_mod, "settings", Settings())
    LogMaskingEngine.reset_for_tests()
    reload_from_rows([], db_authoritative=True)
    out = LogMaskingEngine.get_instance().sanitize("token=abc123")
    assert "abc123" in out
    assert "******" not in out


def test_reload_from_rows_enterprise_uses_builtin_before_db_sync(monkeypatch):
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "jiuwen")
    monkeypatch.setattr(engine_mod, "settings", Settings())
    LogMaskingEngine.reset_for_tests()
    out = LogMaskingEngine.get_instance().sanitize("token=abc123")
    assert "abc123" not in out
    assert "******" in out

    reload_from_rows([])
    out = LogMaskingEngine.get_instance().sanitize("token=abc123")
    assert "abc123" not in out
    assert "******" in out


def test_reload_from_rows_enterprise_uses_db_rules_only(monkeypatch):
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "jiuwen")
    monkeypatch.setattr(engine_mod, "settings", Settings())
    LogMaskingEngine.reset_for_tests()
    reload_from_rows(
        [
            {
                "id": 1,
                "rule_id": "custom_mask",
                "pattern": r"SECRET_VALUE",
                "replacement": "REDACTED",
                "priority": 100,
                "enabled": True,
                "source": "custom",
            }
        ]
    )
    assert LogMaskingEngine.get_instance().sanitize("SECRET_VALUE") == "REDACTED"
    assert "example.com" in LogMaskingEngine.get_instance().sanitize(
        "contact user@example.com"
    )


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


@pytest.mark.parametrize("rows", [None, []])
def test_compile_masking_rows_empty_returns_empty(rows):
    assert LogMaskingEngine.compile_masking_rows(rows) == []


def test_sanitize_long_text_without_sensitive_keywords_is_fast():
    engine = LogMaskingEngine.get_instance()
    text = "x" * 5000 + " user@example.com"
    t0 = time.perf_counter()
    out = engine.sanitize(text)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05, f"slow: {elapsed:.3f}s"
    assert "example.com" not in out


@pytest.mark.parametrize(
    "pattern",
    [
        r"(.*)*",
        r"(.+)+",
    ],
)
def test_validate_pattern_rejects_unsafe_structures(pattern: str):
    from jiuwenclaw.infrastructure.log_masking.engine import validate_pattern

    with pytest.raises(
        ValueError,
        match=r"unsafe nested wildcard|too slow",
    ):
        validate_pattern(pattern)


def test_validate_pattern_allows_simple_custom_pattern():
    from jiuwenclaw.infrastructure.log_masking.engine import validate_pattern

    assert validate_pattern(r"abc") == "abc"
    assert validate_pattern(r"\b\d{4,6}\b") == r"\b\d{4,6}\b"
    assert validate_pattern(
        r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)",
        check_performance=False,
    ) == r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"


def test_validate_pattern_skips_performance_for_builtin_source():
    import time

    from jiuwenclaw.infrastructure.log_masking.engine import (
        LogMaskingEngine,
        validate_pattern,
    )

    kv_pattern = next(
        rule.pattern.pattern
        for rule in LogMaskingEngine.compiled_default_rules()
        if rule.rule_id == "builtin_kv_sensitive"
    )
    t0 = time.perf_counter()
    validate_pattern(kv_pattern, check_structure=False, check_performance=False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05, f"builtin pattern validation too slow: {elapsed:.3f}s"


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, True),
        ("true", True),
        ("false", False),
    ],
)
def test_gateway_log_masking_enabled(monkeypatch, env_value, expected):
    from jiuwenclaw.infrastructure.config import Settings

    if env_value is None:
        monkeypatch.delenv("LOG_MASK_ENABLED", raising=False)
    else:
        monkeypatch.setenv("LOG_MASK_ENABLED", env_value)
    assert Settings().log_masking_enabled is expected


def test_setup_logger_skips_privacy_filter_when_disabled(monkeypatch):
    import logging

    from jiuwenclaw.infrastructure.config import Settings
    from jiuwenclaw.infrastructure.log_masking.filter import SensitiveDataFilter

    monkeypatch.setenv("LOG_MASK_ENABLED", "false")
    import jiuwenclaw.infrastructure.config as infra_config
    import jiuwenclaw.utils as utils_mod

    monkeypatch.setattr(infra_config, "settings", Settings())

    root = logging.getLogger("jiuwenclaw")
    for handler in root.handlers:
        handler.filters[:] = [
            item
            for item in handler.filters
            if not isinstance(item, SensitiveDataFilter)
        ]
    logger = utils_mod.setup_logger("INFO")
    handler_filters = [
        f for h in logger.handlers for f in h.filters if isinstance(f, SensitiveDataFilter)
    ]
    assert not handler_filters


def test_setup_logger_masks_child_logger_via_handlers(monkeypatch, capsys):
    import logging

    from jiuwenclaw.infrastructure.config import Settings
    from jiuwenclaw.infrastructure.log_masking.filter import SensitiveDataFilter

    monkeypatch.setenv("LOG_MASK_ENABLED", "true")
    import jiuwenclaw.infrastructure.config as infra_config
    import jiuwenclaw.utils as utils_mod

    monkeypatch.setattr(infra_config, "settings", Settings())
    utils_mod.setup_logger("INFO")

    child = logging.getLogger("jiuwenclaw.infrastructure.log_masking.engine")
    child.handlers.clear()
    child.info("password=mySecret")

    captured = capsys.readouterr().err
    assert "password=******" in captured
    assert "mySecret" not in captured
    root = logging.getLogger("jiuwenclaw")
    assert any(
        isinstance(f, SensitiveDataFilter)
        for h in root.handlers
        for f in h.filters
    )


@pytest.mark.asyncio
async def test_reload_log_masking_rule_skips_gdb_in_standalone(monkeypatch):
    """单机版不连 GDB，直接回退内置规则。"""
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "")
    monkeypatch.setattr(engine_mod, "settings", Settings())

    with (
        patch(
            "jiuwenclaw.infrastructure.module_importer.import_manager_ws_client_module",
        ) as import_mod,
        patch.object(
            LogMaskingEngine,
            "list_enabled_log_masking_rule_rows",
            new_callable=AsyncMock,
        ) as list_rows,
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule()

    import_mod.assert_not_called()
    list_rows.assert_not_called()
    reload_from_rows.assert_called_once_with([])


@pytest.mark.asyncio
async def test_reload_log_masking_rule_reads_gdb_in_enterprise(monkeypatch):
    """企业版 GDB 冷启动只 reload 引擎，不 notify AgentServer。"""
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "enterprise")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-test")
    monkeypatch.setattr(engine_mod, "settings", Settings())

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
        await LogMaskingEngine.reload_log_masking_rule()

    list_rows.assert_awaited_once()
    reload_from_rows.assert_called_once_with([], db_authoritative=False)


@pytest.mark.asyncio
async def test_reload_log_masking_rule_db_authoritative_when_rows_present(monkeypatch):
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "enterprise")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-test")
    monkeypatch.setattr(engine_mod, "settings", Settings())
    rows = [{"rule_id": "r1", "pattern": r"X", "replacement": "Y", "enabled": True}]

    with (
        _patch_manager_ws_importer(),
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
async def test_reload_log_masking_rule_force_db_authoritative_on_empty(monkeypatch):
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "enterprise")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-test")
    monkeypatch.setattr(engine_mod, "settings", Settings())

    with (
        _patch_manager_ws_importer(),
        patch.object(
            LogMaskingEngine,
            "list_enabled_log_masking_rule_rows",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule(db_authoritative=True)

    reload_from_rows.assert_called_once_with([], db_authoritative=True)


@pytest.mark.asyncio
async def test_reload_log_masking_rule_skips_gdb_without_jiuwenclaw_id(monkeypatch):
    """企业版 ``JIUWENCLAW_ID`` 未就绪时不访问 GDB，保留内置规则。"""
    import jiuwenclaw.infrastructure.log_masking.engine as engine_mod
    from jiuwenclaw.infrastructure.config import Settings

    monkeypatch.setenv("AGENT_RUNTIME", "enterprise")
    monkeypatch.delenv("JIUWENCLAW_ID", raising=False)
    monkeypatch.setattr(engine_mod, "settings", Settings())
    LogMaskingEngine.reset_for_tests()

    with (
        patch(
            "jiuwenclaw.infrastructure.module_importer.import_manager_ws_client_module",
        ) as import_mod,
        patch.object(LogMaskingEngine, "reload_from_rows") as reload_from_rows,
    ):
        await LogMaskingEngine.reload_log_masking_rule()

    import_mod.assert_not_called()
    reload_from_rows.assert_not_called()
    assert "******" in LogMaskingEngine.get_instance().sanitize("token=abc123")


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
            "reload_log_masking_rule",
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
            "reload_log_masking_rule",
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
