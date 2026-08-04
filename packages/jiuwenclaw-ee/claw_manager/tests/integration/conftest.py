# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""日志脱敏集成测试：Manager REST ↔ Gateway GDB ↔ LogMaskingEngine。"""

from __future__ import annotations

import importlib
import sys
import types
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from sqlalchemy.exc import SAWarning

from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.models.application_config_models import LOG_MASKING_RULE_TABLE_DEF
from jiuwenclaw_manager.models.instance_models import INSTANCE_INFO_TABLE_DEF
from jiuwenclaw_manager.routers.register import router_register

from jiuwenclaw.infrastructure.log_masking.engine import LogMaskingEngine
from jiuwenclaw.infrastructure.log_masking.probes import LOG_MASKING_PROBE_SAMPLES
from jiuwenclaw.infrastructure.module_importer import (
    import_manager_ws_client_module,
)

pytestmark = pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")

_EE_ROOT = Path(__file__).resolve().parents[3]
_MANAGER_WS_CLIENT_ROOT = _EE_ROOT / "gateway/extensions/manager_ws_client"


def _ensure_package(name: str, path: str) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    sys.modules[name] = pkg


def _load_gateway_extension_modules() -> tuple[Any, Any, Any]:
    root = _MANAGER_WS_CLIENT_ROOT
    base = "jiuwenclaw.loaded_extension.manager_ws_client"
    _ensure_package("jiuwenclaw.loaded_extension", str(root.parent.parent.parent))
    _ensure_package(base, str(root))
    _ensure_package(f"{base}.core", str(root / "core"))
    _ensure_package(f"{base}.core.application_config", str(root / "core" / "application_config"))
    _ensure_package(f"{base}.infrastructure", str(root / "infrastructure"))
    _ensure_package(f"{base}.models", str(root / "models"))
    _ensure_package(f"{base}.schemas", str(root / "schemas"))
    db_mod = importlib.import_module(f"{base}.infrastructure.db")
    models_mod = importlib.import_module(f"{base}.models.application_config_models")
    rule_mod = importlib.import_module(f"{base}.core.application_config.log_masking_rule")
    return db_mod, models_mod, rule_mod


async def _init_manager_tables(handler: DBHandler) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SAWarning)
        await handler.init_table(LOG_MASKING_RULE_TABLE_DEF)
        await handler.init_table(INSTANCE_INFO_TABLE_DEF)


async def _init_gateway_log_masking_table(handler: DBHandler, gateway_models_mod: Any) -> None:
    # 同一进程内 MDB/GDB 各持 SQLiteHandler 时会重复注册 ORM 类，忽略 SAWarning。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SAWarning)
        await handler.init_table(gateway_models_mod.LOG_MASKING_RULE_TABLE_DEF)


async def _open_sqlite(path: Path) -> SQLiteHandler:
    handler = SQLiteHandler(str(path))
    await handler.init_database()
    await handler.connect()
    return handler


def probe_sample(label: str) -> str:
    for sample_label, text in LOG_MASKING_PROBE_SAMPLES:
        if sample_label == label:
            return text
    raise KeyError(f"unknown probe sample: {label!r}")


@dataclass
class LogMaskingIntegrationHarness:
    """串联 Manager MDB、Gateway GDB 与脱敏引擎的测试夹具。"""

    jiuwenclaw_id: str
    manager_handler: DBHandler
    gateway_handler: DBHandler
    gateway_db_mod: Any
    gateway_rule_mod: Any
    http: AsyncClient

    def api_prefix(self) -> str:
        return f"/api/v1/instances/{self.jiuwenclaw_id}/log-masking-rules"

    @staticmethod
    def sanitize(text: str) -> str:
        return LogMaskingEngine.get_instance().sanitize(text)

    async def bootstrap_builtin_and_sync(self) -> None:
        from jiuwenclaw_manager.core.application_config.log_masking_rule import (
            push_log_masking_rules_sync_to_gateway,
            seed_builtin_log_masking_rules,
        )

        await seed_builtin_log_masking_rules(self.manager_handler, self.jiuwenclaw_id)
        await push_log_masking_rules_sync_to_gateway(
            self.manager_handler, self.jiuwenclaw_id
        )

    async def agentserver_cold_start_reload(self) -> None:
        async def _ensure_db_handler(**_kwargs: Any) -> DBHandler:
            return self.gateway_handler

        def _import_module(suffix: str) -> Any:
            if suffix == "infrastructure.db":
                return types.SimpleNamespace(ensure_db_handler=_ensure_db_handler)
            raise AssertionError(f"unexpected module suffix: {suffix!r}")

        original = import_manager_ws_client_module
        try:
            import jiuwenclaw.infrastructure.module_importer.manager_ws_client_importer as importer

            importer.import_manager_ws_client_module = _import_module  # type: ignore[method-assign]
            await LogMaskingEngine.reload_log_masking_rule()
        finally:
            importer.import_manager_ws_client_module = original  # type: ignore[method-assign]

    async def gdb_rule_ids(self) -> set[str]:
        rows = await self.gateway_handler.list_records(
            LOG_MASKING_RULE_TABLE_DEF.table_name,
            {"jiuwenclaw_id": self.jiuwenclaw_id},
        )
        return {str(getattr(row, "rule_id", "") or "") for row in rows}

    async def mdb_rule_ids(self) -> set[str]:
        rows = await self.manager_handler.list_records(
            LOG_MASKING_RULE_TABLE_DEF.table_name,
            {"jiuwenclaw_id": self.jiuwenclaw_id},
        )
        return {str(getattr(row, "rule_id", "") or "") for row in rows}


@pytest.fixture
async def log_masking_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Manager REST → push 桥接 → Gateway apply → LogMaskingEngine。"""
    LogMaskingEngine.reset_for_tests()
    jid = "sp-log-masking-integration"
    gateway_db_mod, gateway_models_mod, gateway_rule_mod = _load_gateway_extension_modules()

    manager_handler = await _open_sqlite(tmp_path / "manager.db")
    gateway_handler = await _open_sqlite(tmp_path / "gateway.db")
    await _init_manager_tables(manager_handler)
    await _init_gateway_log_masking_table(gateway_handler, gateway_models_mod)

    monkeypatch.setenv("JIUWENCLAW_ID", jid)

    async def _ensure_db_handler(**_kwargs: Any) -> DBHandler:
        return gateway_handler

    monkeypatch.setattr(gateway_db_mod, "ensure_db_handler", _ensure_db_handler)
    monkeypatch.setattr(gateway_rule_mod, "ensure_db_handler", _ensure_db_handler)
    monkeypatch.setattr(gateway_rule_mod, "get_jiuwenclaw_id", lambda: jid)

    async def _bridge_push_config_op(
        jiuwenclaw_id: str,
        config: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        payload = config.get("log_masking_rule")
        if payload is not None:
            await gateway_rule_mod.apply_log_masking_rule(payload)
        return {"revision": "rev-integration", "success_flag": True}

    monkeypatch.setattr(
        "jiuwenclaw_manager.core.application_config.log_masking_rule.push_config_op",
        _bridge_push_config_op,
    )

    from fastapi import FastAPI

    app = FastAPI()
    router_register(app)

    def _override_get_db_handler() -> DBHandler:
        return manager_handler

    app.dependency_overrides[get_db_handler] = _override_get_db_handler

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        harness = LogMaskingIntegrationHarness(
            jiuwenclaw_id=jid,
            manager_handler=manager_handler,
            gateway_handler=gateway_handler,
            gateway_db_mod=gateway_db_mod,
            gateway_rule_mod=gateway_rule_mod,
            http=client,
        )
        yield harness

    app.dependency_overrides.clear()
    await manager_handler.disconnect()
    await gateway_handler.disconnect()
    LogMaskingEngine.reset_for_tests()
