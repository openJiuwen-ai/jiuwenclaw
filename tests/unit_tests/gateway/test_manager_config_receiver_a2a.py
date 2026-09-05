"""Enterprise Manager Config Receiver A2A projection tests."""

# ruff: noqa: E402 -- install optional openjiuwen-runtime DB stubs before imports

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _install_runtime_db_stubs() -> None:
    """Supply table-definition types when the lightweight test env lacks runtime DB."""
    try:
        __import__("openjiuwen_runtime.foundation.db.table_def")
        return
    except ModuleNotFoundError:
        pass

    class _Definition:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            for key, value in kwargs.items():
                setattr(self, key, value)

    handler = ModuleType("openjiuwen_runtime.foundation.db.handler")
    handler.DBHandler = object  # type: ignore[attr-defined]
    table_def = ModuleType("openjiuwen_runtime.foundation.db.table_def")
    table_def.ColumnDefinition = _Definition  # type: ignore[attr-defined]
    table_def.IndexDefinition = _Definition  # type: ignore[attr-defined]
    table_def.TableDefinition = _Definition  # type: ignore[attr-defined]
    sys.modules[handler.__name__] = handler
    sys.modules[table_def.__name__] = table_def
    db_package = sys.modules["openjiuwen_runtime.foundation.db"]
    for module_name, class_name in (
        ("mysql_handler", "MySQLHandler"),
        ("postgresql_handler", "PostgreSQLHandler"),
        ("sqlite_handler", "SQLiteHandler"),
    ):
        module = ModuleType(f"openjiuwen_runtime.foundation.db.{module_name}")
        setattr(module, class_name, type(class_name, (), {}))
        sys.modules[module.__name__] = module
        setattr(db_package, module_name, module)
    runtime_log = ModuleType("openjiuwen_runtime.foundation.log")
    runtime_log.get_logger = logging.getLogger  # type: ignore[attr-defined]
    sys.modules[runtime_log.__name__] = runtime_log


_install_runtime_db_stubs()

from jiuwenswarm.common.secrets import SecretStore
from jiuwenswarm.gateway.config.enterprise import (
    clear_enterprise_record_repositories,
    set_enterprise_record_repositories,
)
from jiuwenswarm.gateway.config.enterprise.tables.table_init import (
    ALL_TABLE_DEFINITIONS,
)
from jiuwenswarm.gateway.storage.backends.memory_persistent import (
    InMemoryPersistentBackend,
)
from jiuwenswarm.gateway.storage_assembly.setup import (
    create_enterprise_record_repositories,
)
from jiuwenswarm.infrastructure.module_importer import (
    import_manager_config_receiver_module,
)


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str:
        return self.values.get(key, "")

    def set(self, key: str, value: str, *, algorithm: str | None = None) -> None:
        del algorithm
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def _outbound_payload(
    *, operation: str = "replace", value: str | None = "secret"
) -> dict[str, Any]:
    credential: dict[str, Any] = {"operation": operation}
    if value is not None:
        credential["value"] = value
    return {
        "template_id": "a2a-weather",
        "template_name": "Weather Agent",
        "description": "weather",
        "a2a_tags": ["weather"],
        "source_url": "https://example.test/a2a",
        "card_path": "/.well-known/agent-card.json",
        "agent_card": {"name": "Weather Agent"},
        "card_fingerprint": "sha256:card",
        "card_revision": 1,
        "selected_interface": {
            "url": "https://example.test/a2a",
            "protocol_binding": "JSONRPC",
            "protocol_version": "1.0",
        },
        "connect_timeout_seconds": 10,
        "sync_wait_seconds": 120,
        "enabled": True,
        "credential": credential,
        "data": {},
        "updated_at": "2026-09-05T00:00:00Z",
    }


@pytest.fixture
def a2a_receiver() -> Iterator[tuple[TestClient, dict[str, Any], _Secrets]]:
    store = InMemoryPersistentBackend()
    repos = create_enterprise_record_repositories(store)
    set_enterprise_record_repositories(repos)
    secrets = _Secrets()
    SecretStore.reset_for_tests(secrets)  # type: ignore[arg-type]
    app_module = import_manager_config_receiver_module("http.app")
    try:
        with TestClient(
            app_module.create_app(), base_url="https://gateway.test"
        ) as client:
            yield client, repos, secrets
    finally:
        SecretStore.reset_for_tests()
        clear_enterprise_record_repositories()


def test_a2a_outbound_receiver_crud_and_secret_store(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
) -> None:
    client, repos, secrets = a2a_receiver
    path = "/api/v1/a2a-outbound-templates"
    credential_ref = "a2a/outbound/a2a-weather.api_key"

    response = client.post(path, json=_outbound_payload())
    assert response.status_code == 200
    stored = repos["a2a_outbound_template"]
    projection = client.portal.call(lambda: stored.get(template_id="a2a-weather"))
    assert projection["credential_ref"] == credential_ref
    assert "credential" not in projection
    assert secrets.values[credential_ref] == "secret"

    sanitized = _outbound_payload()
    sanitized["agent_card"]["authorization"] = "Bearer card-secret"
    sanitized["selected_interface"]["api_key"] = "interface-secret"
    sanitized["data"]["access_token"] = "metadata-secret"
    assert client.post(path, json=sanitized).status_code == 200
    projection = client.portal.call(lambda: stored.get(template_id="a2a-weather"))
    assert projection["agent_card"]["authorization"] == "******"
    assert projection["selected_interface"]["api_key"] == "******"
    assert projection["data"]["access_token"] == "******"

    replay = _outbound_payload()
    replay["template_name"] = "Weather Agent v2"
    assert client.post(path, json=replay).status_code == 200
    projection = client.portal.call(lambda: stored.get(template_id="a2a-weather"))
    assert projection["template_name"] == "Weather Agent v2"
    assert secrets.values[credential_ref] == "secret"

    assert (
        client.patch(
            f"{path}/a2a-weather",
            json={"enabled": False, "credential": {"operation": "keep"}},
        ).status_code
        == 200
    )
    projection = client.portal.call(lambda: stored.get(template_id="a2a-weather"))
    assert projection["enabled"] is False
    assert secrets.values[credential_ref] == "secret"

    assert (
        client.patch(
            f"{path}/a2a-weather",
            json={"credential": {"operation": "clear"}},
        ).status_code
        == 200
    )
    projection = client.portal.call(lambda: stored.get(template_id="a2a-weather"))
    assert projection["credential_ref"] is None
    assert credential_ref not in secrets.values

    assert client.request("DELETE", f"{path}/a2a-weather", json={}).status_code == 200
    assert client.request("DELETE", f"{path}/a2a-weather", json={}).status_code == 200


def test_a2a_access_policy_receiver_is_idempotent(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
) -> None:
    client, repos, _ = a2a_receiver
    path = "/api/v1/a2a-access-policies"
    payload = {
        "policy_id": "policy-1",
        "policy_name": "Production",
        "description": None,
        "mode": "allowlist",
        "member_template_ids": ["a2a-weather", "a2a-weather"],
        "enabled": True,
        "revision": 1,
        "data": {"source": "manager"},
        "updated_at": "2026-09-05T00:00:00Z",
        "sig": "legacy-signature",
        "enc": {"version": "legacy"},
    }
    assert client.post(path, json=payload).status_code == 200
    payload.update({"mode": "denylist", "revision": 2})
    assert client.post(path, json=payload).status_code == 200

    repo = repos["a2a_access_policy_template"]
    row = client.portal.call(lambda: repo.get(policy_id="policy-1"))
    assert row["mode"] == "denylist"
    assert row["member_template_ids"] == ["a2a-weather"]
    assert row["data"] == {"source": "manager"}
    assert "sig" not in row
    assert "enc" not in row

    assert (
        client.patch(
            f"{path}/policy-1", json={"enabled": False, "revision": 3}
        ).status_code
        == 200
    )
    assert client.portal.call(lambda: repo.get(policy_id="policy-1"))["revision"] == 3
    assert client.request("DELETE", f"{path}/policy-1", json={}).status_code == 200
    assert client.request("DELETE", f"{path}/policy-1", json={}).status_code == 200


def test_a2a_receiver_rejects_undecryptable_envelope(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
) -> None:
    client, _, secrets = a2a_receiver
    payload = _outbound_payload(value="ENC:v1:dek:wrapped:ciphertext")
    response = client.post("/api/v1/a2a-outbound-templates", json=payload)
    assert response.status_code == 422
    assert secrets.values == {}

    payload = _outbound_payload(operation="keep", value=None)
    response = client.post("/api/v1/a2a-outbound-templates", json=payload)
    assert response.status_code == 422


def test_a2a_receiver_requires_complete_selected_interface(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
) -> None:
    client, _, _ = a2a_receiver
    path = "/api/v1/a2a-outbound-templates"
    for missing in ("protocol_binding", "protocol_version", "url"):
        payload = _outbound_payload()
        payload["selected_interface"].pop(missing)
        assert client.post(path, json=payload).status_code == 422


@pytest.mark.parametrize(
    "card_path",
    ["../agent-card.json", "//evil.test/card", "/card?source=manager"],
)
def test_a2a_receiver_rejects_invalid_card_path(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
    card_path: str,
) -> None:
    client, _, _ = a2a_receiver
    payload = _outbound_payload()
    payload["card_path"] = card_path
    assert client.post("/api/v1/a2a-outbound-templates", json=payload).status_code == 422


def test_a2a_credential_replace_requires_https(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
) -> None:
    _, _, secrets = a2a_receiver
    app_module = import_manager_config_receiver_module("http.app")
    with TestClient(app_module.create_app(), base_url="http://gateway.test") as client:
        response = client.post(
            "/api/v1/a2a-outbound-templates",
            json=_outbound_payload(),
        )
    assert response.status_code == 400
    assert secrets.values == {}


def test_a2a_credential_is_restored_when_projection_update_fails(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos, secrets = a2a_receiver
    path = "/api/v1/a2a-outbound-templates"
    credential_ref = "a2a/outbound/a2a-weather.api_key"
    assert client.post(path, json=_outbound_payload()).status_code == 200

    repo = repos["a2a_outbound_template"]

    async def _fail_update(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("write failed")

    monkeypatch.setattr(repo, "update", _fail_update)
    with pytest.raises(RuntimeError, match="write failed"):
        client.patch(
            f"{path}/a2a-weather",
            json={"credential": {"operation": "replace", "value": "new-secret"}},
        )
    assert secrets.values[credential_ref] == "secret"


def test_a2a_credential_is_removed_when_projection_create_fails(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos, secrets = a2a_receiver
    repo = repos["a2a_outbound_template"]

    async def _fail_create(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("write failed")

    monkeypatch.setattr(repo, "create", _fail_create)
    with pytest.raises(RuntimeError, match="write failed"):
        client.post(
            "/api/v1/a2a-outbound-templates",
            json=_outbound_payload(),
        )
    assert secrets.values == {}


def test_a2a_outbound_purge_removes_projection_and_secret(
    a2a_receiver: tuple[TestClient, dict[str, Any], _Secrets],
) -> None:
    client, repos, secrets = a2a_receiver
    path = "/api/v1/a2a-outbound-templates"
    assert client.post(path, json=_outbound_payload()).status_code == 200

    lifecycle = import_manager_config_receiver_module(
        "core.instance.instance_data_lifecycle"
    )
    count = client.portal.call(
        lambda: lifecycle._purge_enterprise_table("a2a_outbound_template")
    )
    assert count == 1
    assert (
        client.portal.call(
            lambda: repos["a2a_outbound_template"].get(
                template_id="a2a-weather"
            )
        )
        is None
    )
    assert secrets.values == {}
    assert "a2a_outbound_template" in lifecycle.INSTANCE_PURGE_TABLES
    assert "a2a_access_policy_template" in lifecycle.INSTANCE_PURGE_TABLES


def test_a2a_tables_are_in_enterprise_init_and_catalog() -> None:
    table_names = {definition.table_name for definition in ALL_TABLE_DEFINITIONS}
    assert "a2a_outbound_template" in table_names
    assert "a2a_access_policy_template" in table_names

    repos = create_enterprise_record_repositories(InMemoryPersistentBackend())
    assert "a2a_outbound_template" in repos
    assert "a2a_access_policy_template" in repos


def test_manager_config_public_endpoint_can_advertise_https() -> None:
    config = import_manager_config_receiver_module("infrastructure.config")
    public_endpoint = import_manager_config_receiver_module(
        "infrastructure.public_endpoint"
    )
    settings = config.Settings(
        GATEWAY_CONFIG_PUBLIC_SCHEME="https",
        GATEWAY_CONFIG_PUBLIC_HOST="gateway.test",
        GATEWAY_CONFIG_HTTP_PORT=443,
    )
    assert public_endpoint.resolve_public_endpoint(settings) == (
        "https://gateway.test:443"
    )
