# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenswarm.gateway.config.enterprise.catalog import ENTERPRISE_RECORD_SPECS
from jiuwenswarm.gateway.config.enterprise.instance_scope import (
    apply_instance_scope,
    instance_scoped_store_names,
    list_records_requires_bound_instance,
    resolve_gateway_instance_id,
    table_requires_instance_scope,
)


def test_instance_scoped_store_names_from_catalog() -> None:
    expected = {
        name
        for name, spec in ENTERPRISE_RECORD_SPECS.items()
        if spec.scope_field == "jiuwenclaw_id"
    }
    assert instance_scoped_store_names() == frozenset(expected)
    assert "cron_job" in instance_scoped_store_names()
    assert "extension_config_template" in instance_scoped_store_names()
    assert "gateway_enc_keypair" not in instance_scoped_store_names()


def test_apply_instance_scope_adds_jiuwenclaw_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIUWENCLAW_ID", "inst-a")
    scoped = apply_instance_scope(
        "extension_config_template",
        {"enabled": True},
        instance_id=resolve_gateway_instance_id(),
    )
    assert scoped == {"enabled": True, "jiuwenclaw_id": "inst-a"}


def test_apply_instance_scope_respects_explicit_filter() -> None:
    scoped = apply_instance_scope(
        "model_template",
        {"jiuwenclaw_id": "explicit"},
        instance_id="other",
    )
    assert scoped["jiuwenclaw_id"] == "explicit"


def test_apply_instance_scope_skips_global_tables() -> None:
    scoped = apply_instance_scope(
        "gateway_enc_keypair",
        {"id": 1},
        instance_id="inst-a",
    )
    assert scoped == {"id": 1}


def test_list_records_requires_bound_instance_fail_closed() -> None:
    assert list_records_requires_bound_instance("cron_job", None) is True
    assert list_records_requires_bound_instance("cron_job", "jid") is False
    assert list_records_requires_bound_instance("channel_config", None) is False


def test_resolve_gateway_instance_id_env_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JIUWENCLAW_ID", raising=False)
    monkeypatch.delenv("JIUWENSWARM_ID", raising=False)
    monkeypatch.delenv("GATEWAY_INSTANCE_ID", raising=False)
    monkeypatch.setenv("JIUWENCLAW_ID", "from-manager")
    monkeypatch.setenv("GATEWAY_INSTANCE_ID", "from-gateway")
    assert resolve_gateway_instance_id({}) == "from-manager"


def test_resolve_gateway_instance_id_config_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIUWENCLAW_ID", "env-id")
    cfg = {"gateway": {"instance_id": "cfg-id"}}
    assert resolve_gateway_instance_id(cfg) == "cfg-id"


def test_resolve_gateway_instance_id_redis_error_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "JIUWENCLAW_ID",
        "JIUWENSWARM_ID",
        "JIUWENSWARM_PROVISIONED_INSTANCE_ID",
        "JIUWENCLAW_PROVISIONED_INSTANCE_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GATEWAY_INSTANCE_ID", "from-env")

    def _boom() -> str:
        raise ConnectionError("redis unavailable")

    import jiuwenswarm.extensions.redis.redis_runtime as redis_runtime

    monkeypatch.setattr(redis_runtime, "get_gateway_instance_id", _boom)
    assert resolve_gateway_instance_id({}) == "from-env"


@pytest.mark.asyncio
async def test_gateway_db_list_records_fail_closed_without_instance_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    monkeypatch.delenv("JIUWENCLAW_ID", raising=False)
    monkeypatch.delenv("JIUWENSWARM_ID", raising=False)
    monkeypatch.delenv("GATEWAY_INSTANCE_ID", raising=False)
    monkeypatch.setattr(gateway_db, "resolve_gateway_db_path", lambda: "/tmp/fake.db")

    rows = await gateway_db.list_records("extension_config_template", filters={"enabled": True})
    assert rows == []


def test_gateway_db_apply_instance_scope_matches_shared_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    monkeypatch.setenv("JIUWENCLAW_ID", "sp-demo")
    scoped = gateway_db.apply_instance_scope(
        "skill_whitelist_template",
        {"template_id": "w1"},
    )
    assert scoped["jiuwenclaw_id"] == "sp-demo"


def test_table_requires_instance_scope() -> None:
    assert table_requires_instance_scope("service_config_template") is True
    assert table_requires_instance_scope("permissions_config") is False


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
    monkeypatch.setenv("JIUWENCLAW_ID", "inst-a")

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
