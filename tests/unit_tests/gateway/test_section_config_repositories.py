# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""permissions / logging / memory 单文档 Repository。"""

from __future__ import annotations

import pytest

from jiuwenswarm.gateway.config.logging import LoggingConfigRepository, db_logging_codec
from jiuwenswarm.gateway.config.memory import MemoryConfigRepository
from jiuwenswarm.gateway.config.permissions import PermissionsConfigRepository
from jiuwenswarm.gateway.config.section import (
    DbBodySectionCodec,
    DbFlatSectionCodec,
    YamlSectionCodec,
)
from jiuwenswarm.gateway.edition import EDITION_ENTERPRISE, EDITION_PERSONAL
from jiuwenswarm.gateway.storage.backends.file_persistent import FilePersistentBackend
from jiuwenswarm.gateway.storage.backends.memory_persistent import InMemoryPersistentBackend
from jiuwenswarm.gateway.storage_assembly.layouts import build_gateway_store_registry
from jiuwenswarm.gateway.storage_assembly.setup import (
    create_logging_config_repository,
    create_memory_config_repository,
    create_permissions_config_repository,
)


@pytest.mark.asyncio
async def test_permissions_yaml_merge_and_enabled() -> None:
    store = InMemoryPersistentBackend()
    repo = PermissionsConfigRepository(store, YamlSectionCodec())
    await repo.replace({"enabled": False, "tools": {"bash": "ask"}})
    await repo.set_enabled(True)
    body = await repo.get_body()
    assert body["enabled"] is True
    assert body["tools"]["bash"] == "ask"


@pytest.mark.asyncio
async def test_permissions_tool_and_rule_helpers() -> None:
    store = InMemoryPersistentBackend()
    repo = PermissionsConfigRepository(store, YamlSectionCodec())
    await repo.replace({"enabled": True, "tools": {}, "rules": []})

    await repo.set_deny_guidance("blocked")
    tools = await repo.update_tool("bash", "deny")
    assert tools["tools"]["bash"] == "deny"

    rule = await repo.create_rule(
        {"tools": ["bash"], "pattern": "rm -rf", "action": "deny"}
    )
    assert rule["id"]
    updated = await repo.update_rule(rule["id"], {"description": "danger"})
    assert updated["description"] == "danger"
    assert await repo.delete_rule(rule["id"]) is True
    assert await repo.delete_tool("bash") is True

    body = await repo.get_body()
    assert body["deny_guidance_message"] == "blocked"
    assert body["tools"] == {}
    assert body["rules"] == []


@pytest.mark.asyncio
async def test_permissions_db_body_roundtrip() -> None:
    store = InMemoryPersistentBackend()
    repo = PermissionsConfigRepository(
        store, DbBodySectionCodec(), instance_id="inst-1"
    )
    await repo.merge({"enabled": True})
    document = await repo.get()
    assert document is not None
    assert document.body["enabled"] is True
    row = await store.get("permissions_config", {"jiuwenclaw_id": "inst-1"})
    assert row is not None
    assert row["body"]["enabled"] is True


@pytest.mark.asyncio
async def test_logging_yaml_merge_levels() -> None:
    store = InMemoryPersistentBackend()
    repo = LoggingConfigRepository(store, YamlSectionCodec())
    await repo.replace({"level": "INFO", "gateway": "INFO"})
    await repo.merge_levels({"gateway": "DEBUG", "channel": "WARNING"})
    body = await repo.get_body()
    assert body["level"] == "INFO"
    assert body["gateway"] == "DEBUG"
    assert body["channel"] == "WARNING"


@pytest.mark.asyncio
async def test_logging_db_flat_codec() -> None:
    store = InMemoryPersistentBackend()
    repo = LoggingConfigRepository(
        store, db_logging_codec(), instance_id="inst-1"
    )
    await repo.merge_levels({"level": "ERROR", "full": "DEBUG"})
    row = await store.get("logging_config", {"jiuwenclaw_id": "inst-1"})
    assert row is not None
    assert row["level"] == "ERROR"
    assert row["full"] == "DEBUG"
    assert "body" not in row


@pytest.mark.asyncio
async def test_memory_forbidden_helpers() -> None:
    store = InMemoryPersistentBackend()
    repo = MemoryConfigRepository(store, YamlSectionCodec())
    await repo.set_forbidden_enabled(True)
    await repo.merge_forbidden_description({"zh": "禁止", "en": "deny"})
    body = await repo.get_body()
    section = body["forbidden_memory_definition"]
    assert section["enabled"] is True
    assert section["description"]["zh"] == "禁止"


@pytest.mark.asyncio
async def test_memory_replace_and_delete() -> None:
    store = InMemoryPersistentBackend()
    repo = MemoryConfigRepository(store, YamlSectionCodec())
    await repo.replace({"forbidden_memory_definition": {"enabled": True}, "extra": 1})
    body = await repo.get_body()
    assert body["extra"] == 1
    assert await repo.delete() is True
    assert await repo.get_body() == {}


@pytest.mark.asyncio
async def test_logging_replace_and_delete() -> None:
    store = InMemoryPersistentBackend()
    repo = LoggingConfigRepository(store, YamlSectionCodec())
    await repo.replace({"level": "ERROR", "gateway": "DEBUG", "preview_user_content": False})
    body = await repo.get_body()
    assert body["level"] == "ERROR"
    assert body["preview_user_content"] is False
    assert await repo.delete() is True
    assert await repo.get_body() == {}


@pytest.mark.asyncio
async def test_section_yaml_file_overlay(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "permissions:\n  enabled: false\nlogging:\n  level: INFO\nmemory:\n  foo: 1\n",
        encoding="utf-8",
    )
    store = FilePersistentBackend(
        registry=build_gateway_store_registry(config_file=config_path)
    )
    perms = PermissionsConfigRepository(store, YamlSectionCodec())
    await perms.set_enabled(True)
    logging_repo = LoggingConfigRepository(store, YamlSectionCodec())
    await logging_repo.merge_levels({"gateway": "DEBUG"})
    memory = MemoryConfigRepository(store, YamlSectionCodec())
    await memory.merge({"bar": 2})

    text = config_path.read_text(encoding="utf-8")
    assert "enabled: true" in text.lower() or "enabled: True" in text
    assert "gateway: DEBUG" in text or "gateway: debug" in text.lower()
    assert "foo: 1" in text
    assert "bar: 2" in text


def test_factory_codec_selection() -> None:
    store = InMemoryPersistentBackend()
    personal = create_permissions_config_repository(store, EDITION_PERSONAL)
    enterprise = create_permissions_config_repository(
        store, EDITION_ENTERPRISE, instance_id="x"
    )
    assert isinstance(personal._inner._codec, YamlSectionCodec)
    assert isinstance(enterprise._inner._codec, DbBodySectionCodec)

    log_personal = create_logging_config_repository(store, EDITION_PERSONAL)
    log_enterprise = create_logging_config_repository(
        store, EDITION_ENTERPRISE, instance_id="x"
    )
    assert isinstance(log_personal._inner._codec, YamlSectionCodec)
    assert isinstance(log_enterprise._inner._codec, DbFlatSectionCodec)

    mem_personal = create_memory_config_repository(store, EDITION_PERSONAL)
    mem_enterprise = create_memory_config_repository(
        store, EDITION_ENTERPRISE, instance_id="x"
    )
    assert isinstance(mem_personal._inner._codec, YamlSectionCodec)
    assert isinstance(mem_enterprise._inner._codec, DbBodySectionCodec)
