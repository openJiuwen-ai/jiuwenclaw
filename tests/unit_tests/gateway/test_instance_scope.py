# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway 企业配置读库相关单测（每网关独立 DB，无实例行级隔离）。"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_gateway_db_list_records_passes_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    monkeypatch.setattr(gateway_db, "use_remote_gateway_db", lambda: False)

    async def _sqlite(table, query, order_by):
        assert table == "extension_config_template"
        assert query == {"enabled": True}
        return [{"template_id": "t1"}]

    monkeypatch.setattr(gateway_db, "_list_records_sqlite", _sqlite)
    rows = await gateway_db.list_records("extension_config_template", filters={"enabled": True})
    assert rows == [{"template_id": "t1"}]


def test_use_remote_gateway_db_requires_enterprise_edition_and_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    monkeypatch.delenv("JIUWENSWARM_EDITION", raising=False)
    monkeypatch.delenv("GATEWAY_DB_HOST", raising=False)
    assert gateway_db.use_remote_gateway_db() is False

    monkeypatch.setenv("GATEWAY_DB_HOST", "127.0.0.1")
    assert gateway_db.use_remote_gateway_db() is False

    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    assert gateway_db.use_remote_gateway_db() is True


def test_is_gateway_db_available_remote_without_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    monkeypatch.setenv("GATEWAY_DB_HOST", "db.example")
    monkeypatch.setattr(gateway_db, "resolve_gateway_db_path", lambda: None)
    assert gateway_db.is_gateway_db_available() is True


@pytest.mark.asyncio
async def test_list_records_remote_does_not_fallback_to_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    monkeypatch.setenv("GATEWAY_DB_HOST", "db.example")

    async def _boom(*_a, **_k):
        raise RuntimeError("remote down")

    monkeypatch.setattr(gateway_db, "_list_records_remote", _boom)

    sqlite_called = {"ok": False}

    async def _sqlite(*_a, **_k):
        sqlite_called["ok"] = True
        return [{"id": 1}]

    monkeypatch.setattr(gateway_db, "_list_records_sqlite", _sqlite)

    with pytest.raises(RuntimeError, match="remote down"):
        await gateway_db.list_records("model_template", filters={"enabled": True})
    assert sqlite_called["ok"] is False


@pytest.mark.asyncio
async def test_get_remote_engine_disposes_old_on_config_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.gateway.storage.backends.db import reader as db_reader

    class _FakeEngine:
        def __init__(self, label: str) -> None:
            self.label = label
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    created: list[_FakeEngine] = []

    def _fake_create_async_engine(_url: str, **_kwargs: object) -> _FakeEngine:
        engine = _FakeEngine(f"engine-{len(created)}")
        created.append(engine)
        return engine

    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    monkeypatch.setenv("GATEWAY_DB_TYPE", "mysql")
    monkeypatch.setenv("GATEWAY_DB_HOST", "host-a")
    monkeypatch.setenv("GATEWAY_DB_PORT", "3306")
    monkeypatch.setenv("GATEWAY_DB_USER", "root")
    monkeypatch.setenv("GATEWAY_DB_NAME", "gateway")
    monkeypatch.setenv("GATEWAY_DB_PASSWORD", "")
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine",
        _fake_create_async_engine,
    )

    db_reader._remote_engine = None
    db_reader._remote_engine_key = None

    first = await db_reader.get_remote_engine()
    assert first is created[0]
    assert first.disposed is False

    monkeypatch.setenv("GATEWAY_DB_HOST", "host-b")
    second = await db_reader.get_remote_engine()
    assert first.disposed is True
    assert second is created[1]
    assert second is not first
    assert second.disposed is False

    await db_reader.dispose_remote_engine()
    assert second.disposed is True
    assert db_reader._remote_engine is None
