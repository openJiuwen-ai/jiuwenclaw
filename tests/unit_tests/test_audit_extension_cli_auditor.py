# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jiuwenswarm.extensions.audit as audit_package
from jiuwenswarm.extensions.audit.alert_engine import AlertEngine
from jiuwenswarm.extensions.audit.auditor import (
    Auditor,
    _extract_context,
    _extract_memory_context,
    _extract_memory_metadata,
)
from jiuwenswarm.extensions.audit.cli import (
    _build_parser,
    _cmd_alert_state,
    _cmd_cleanup,
    _cmd_export,
    _cmd_query,
    _cmd_report,
    _cmd_session_summary,
    _cmd_sessions,
    _cmd_status,
    _cmd_timeline,
)
from jiuwenswarm.extensions.audit.config import AuditConfig
from jiuwenswarm.extensions.audit.extension import (
    _HOOK_REGISTRY,
    AuditExtension,
    register_extensions,
)
from jiuwenswarm.extensions.audit.log_store import LogStore
from jiuwenswarm.extensions.audit.models import Alert, AuditEvent, AuditEventType
from jiuwenswarm.extensions.types import ExtensionConfig


class _FakeRegistry:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = ExtensionConfig(config=config, logger=None)
        self.registered: list[tuple[str, object, int]] = []
        self.unregistered: list[tuple[str, object]] = []

    def register(self, event: str, callback: object, priority: int = 100) -> None:
        self.registered.append((event, callback, priority))

    def unregister(self, event: str, callback: object) -> None:
        self.unregistered.append((event, callback))


@pytest.mark.asyncio
async def test_register_extensions_initializes_before_registering(tmp_path: Path) -> None:
    registry = _FakeRegistry({
        "audit": {
            "audit_dir": str(tmp_path / "audit"),
            "enabled": True,
        },
    })

    extensions = await register_extensions(registry)

    assert len(extensions) == 1
    extension = extensions[0]
    assert extension.store is not None
    assert extension.store.initialized is True
    assert extension.auditor is not None
    assert extension.alert_engine is not None
    assert len(registry.registered) == len(_HOOK_REGISTRY)
    assert {event for event, _, _ in registry.registered} == set(_HOOK_REGISTRY)
    assert all(priority == 200 for _, _, priority in registry.registered)

    await extension.shutdown()


@pytest.mark.asyncio
async def test_shutdown_unregisters_hooks_and_is_idempotent(tmp_path: Path) -> None:
    registry = _FakeRegistry({"audit": {"audit_dir": str(tmp_path / "audit")}})
    extension = (await register_extensions(registry))[0]
    callbacks = [(event, callback) for event, callback, _ in registry.registered]

    await extension.shutdown()
    await extension.shutdown()

    assert registry.unregistered == callbacks
    assert extension.store is None
    assert extension.auditor is None
    assert extension.alert_engine is None


@pytest.mark.asyncio
async def test_disabled_extension_registers_no_hooks(tmp_path: Path) -> None:
    registry = _FakeRegistry({
        "audit": {
            "audit_dir": str(tmp_path / "audit"),
            "enabled": "false",
        },
    })

    extension = (await register_extensions(registry))[0]

    assert extension.audit_config is not None
    assert extension.audit_config.enabled is False
    assert extension.store is None
    assert registry.registered == []
    await extension.shutdown()


def test_register_before_initialize_fails_explicitly() -> None:
    extension = AuditExtension()
    extension._config = AuditConfig(enabled=True)

    with pytest.raises(RuntimeError, match="initialized"):
        extension.register(_FakeRegistry({}))


@pytest.mark.asyncio
async def test_register_is_idempotent(tmp_path: Path) -> None:
    registry = _FakeRegistry({"audit": {"audit_dir": str(tmp_path / "audit")}})
    extension = AuditExtension()
    await extension.initialize(registry.config)

    extension.register(registry)
    extension.register(registry)

    assert len(registry.registered) == len(_HOOK_REGISTRY)
    await extension.shutdown()


@pytest.mark.asyncio
async def test_reinitialize_replaces_store_connection(tmp_path: Path) -> None:
    extension = AuditExtension()
    first_config = ExtensionConfig(
        config={"audit": {"audit_dir": str(tmp_path / "first")}},
        logger=None,
    )
    second_config = ExtensionConfig(
        config={"audit": {"audit_dir": str(tmp_path / "second")}},
        logger=None,
    )
    await extension.initialize(first_config)
    first_store = extension.store

    await extension.initialize(second_config)

    assert first_store is not None
    assert first_store.initialized is False
    assert extension.store is not first_store
    assert extension.store is not None
    assert extension.store.audit_dir == (tmp_path / "second").resolve()
    await extension.shutdown()


def test_extension_entry_can_load_under_loader_style_module_name() -> None:
    entry = Path(audit_package.__file__).parent / "extension.py"
    spec = importlib.util.spec_from_file_location("jiuwenswarm.loaded_extension.audit", entry)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert module.AuditExtension is not None
    assert module.register_extensions is not None


@pytest.mark.asyncio
async def test_get_audit_store_returns_initialized_owned_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AuditConfig(audit_dir=str(tmp_path / "api-store"))
    monkeypatch.setattr(audit_package, "load_audit_config", lambda: config)

    store = await audit_package.get_audit_store()

    assert store.initialized is True
    await store.write_event(AuditEvent(event_id="api-event"))
    await store.close()


@pytest.mark.asyncio
async def test_open_audit_store_closes_on_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AuditConfig(audit_dir=str(tmp_path / "context-store"))
    monkeypatch.setattr(audit_package, "load_audit_config", lambda: config)
    captured: LogStore | None = None

    with pytest.raises(ValueError, match="boom"):
        async with audit_package.open_audit_store() as store:
            captured = store
            raise ValueError("boom")

    assert captured is not None
    assert captured.initialized is False


@pytest.mark.asyncio
async def test_auditor_records_request_response_error_and_session_end(tmp_path: Path) -> None:
    async with LogStore(tmp_path / "audit") as store:
        config = AuditConfig(alert_cooldown_seconds=0)
        engine = AlertEngine(store, [], config)
        auditor = Auditor(store, engine, config)

        await auditor.on_gateway_chat_request({
            "session_id": "session-1",
            "channel_id": "channel-1",
            "request_id": "request-1",
            "req_method": "chat",
            "params": {"query": "secret content"},
        })
        response = await auditor.record_chat_response(
            session_id="session-1",
            channel_id="channel-1",
            request_id="request-1",
            token_usage={"total_tokens": 7},
        )

        await auditor.on_gateway_chat_request({
            "session_id": "session-1",
            "channel_id": "channel-1",
            "request_id": "request-2",
            "params": {},
        })
        error = await auditor.record_chat_error(
            session_id="session-1",
            channel_id="channel-1",
            request_id="request-2",
            error_type="RuntimeError",
            error_detail="failed",
        )
        ended = await auditor.end_session("session-1")

        events = await store.query_events({"session_id": "session-1", "limit": 10})

    assert response.event_type == AuditEventType.CHAT_RESPONSE
    assert response.duration_ms is not None
    assert error.event_type == AuditEventType.CHAT_ERROR
    assert error.duration_ms is not None
    assert ended.event_type == AuditEventType.SESSION_END
    assert ended.metadata["request_count"] == 2
    assert ended.metadata["error_count"] == 1
    assert "session-1" not in auditor.get_session_tracker()
    assert len(events) == 5
    request_metadata = next(
        event.metadata for event in events
        if event.metadata.get("params_keys") == ["query"]
    )
    assert request_metadata["params_keys"] == ["query"]
    assert "secret content" not in json.dumps(request_metadata)


@pytest.mark.asyncio
async def test_auditor_memory_after_extracts_usage(tmp_path: Path) -> None:
    async with LogStore(tmp_path / "audit") as store:
        config = AuditConfig()
        auditor = Auditor(store, AlertEngine(store, [], config), config)
        await auditor.on_agent_server_chat_request({
            "session_id": "session",
            "channel_id": "channel",
            "request_id": "request",
            "params": {},
        })
        await auditor.on_memory_after_chat({
            "session_id": "session",
            "channel_id": "channel",
            "request_id": "request",
            "agent_name": "main",
            "metadata": {
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
        })
        events = await store.query_events({"event_type": "memory_after_chat"})

    assert len(events) == 1
    assert events[0].token_usage == {
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 5,
    }
    assert events[0].duration_ms is not None


@pytest.mark.asyncio
async def test_disabled_auditor_does_not_write(tmp_path: Path) -> None:
    async with LogStore(tmp_path / "audit") as store:
        config = AuditConfig(enabled=False)
        auditor = Auditor(store, AlertEngine(store, [], config), config)

        await auditor.on_gateway_started()

        assert await store.query_events({}) == []


class _ToDictContext:
    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": "session",
            "channel_id": "channel",
            "request_id": "request",
            "req_method": "chat",
            "params": {"key": "value"},
            "agent_name": "agent",
            "workspace_dir": "/workspace",
        }


def test_context_extractors_support_dict_object_and_to_dict() -> None:
    expected = ("session", "channel", "request", "chat", {"key": "value"})
    assert _extract_context(_ToDictContext()) == expected
    assert _extract_context(SimpleNamespace(
        session_id="session",
        channel_id="channel",
        request_id="request",
        req_method="chat",
        params={"key": "value"},
    )) == expected
    assert _extract_context(None) == (None, None, None, None, {})

    assert _extract_memory_context(_ToDictContext()) == (
        "session",
        "channel",
        "request",
        "agent",
        "/workspace",
    )


def test_memory_metadata_extractor_only_accepts_mapping() -> None:
    assert _extract_memory_metadata({"metadata": {"safe": True}}) == {"safe": True}
    assert _extract_memory_metadata({"metadata": []}) == {}
    assert _extract_memory_metadata(None) == {}


def test_cli_parser_supports_new_commands_and_filters() -> None:
    parser = _build_parser()

    query = parser.parse_args([
        "query",
        "--type", "chat_error",
        "--request", "request-1",
        "--agent", "main",
        "--error-type", "RuntimeError",
        "--only-errors",
        "--offset", "4",
        "--json",
    ])
    export = parser.parse_args(["export", "--format", "csv", "--output", "out.csv"])
    resolve = parser.parse_args(["resolve-alert", "alert-1"])

    assert query.command == "query"
    assert query.request_id == "request-1"
    assert query.agent_name == "main"
    assert query.error_type == "RuntimeError"
    assert query.only_errors is True
    assert query.offset == 4
    assert query.json is True
    assert export.format == "csv"
    assert resolve.alert_id == "alert-1"


@pytest.mark.asyncio
async def test_cli_status_report_sessions_and_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit_dir = tmp_path / "audit"
    async with LogStore(audit_dir) as store:
        await store.write_event(AuditEvent(
            event_id="event",
            event_type=AuditEventType.CHAT_REQUEST,
            session_id="session",
        ))

    assert await _cmd_status(audit_dir) == 0
    assert json.loads(capsys.readouterr().out)["total_events"] == 1

    assert await _cmd_report(audit_dir, argparse.Namespace(hours=24)) == 0
    assert json.loads(capsys.readouterr().out)["total_sessions"] == 1

    assert await _cmd_timeline(
        audit_dir,
        argparse.Namespace(hours=24, bucket_minutes=60),
    ) == 0
    assert json.loads(capsys.readouterr().out)[0]["total_events"] == 1

    assert await _cmd_sessions(
        audit_dir,
        argparse.Namespace(hours=24, limit=10, offset=0),
    ) == 0
    assert json.loads(capsys.readouterr().out)[0]["session_id"] == "session"

    assert await _cmd_session_summary(
        audit_dir,
        argparse.Namespace(session_id="session"),
    ) == 0
    assert json.loads(capsys.readouterr().out)["session_id"] == "session"


@pytest.mark.asyncio
async def test_cli_query_json_and_csv_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit_dir = tmp_path / "audit"
    async with LogStore(audit_dir) as store:
        await store.write_event(AuditEvent(
            event_id="error-event",
            event_type=AuditEventType.CHAT_ERROR,
            session_id="session",
            request_id="request",
            agent_name="agent",
            error_type="RuntimeError",
        ))

    query_args = argparse.Namespace(
        hours=24,
        limit=10,
        offset=0,
        event_type="chat_error",
        session_id="session",
        channel_id=None,
        request_id="request",
        agent_name="agent",
        error_type="RuntimeError",
        only_errors=True,
        json=True,
    )
    assert await _cmd_query(audit_dir, query_args) == 0
    assert json.loads(capsys.readouterr().out)[0]["event_id"] == "error-event"

    output = tmp_path / "export.csv"
    export_args = argparse.Namespace(
        output=str(output),
        hours=24,
        event_type=None,
        format="csv",
    )
    assert await _cmd_export(audit_dir, export_args) == 0
    assert "Exported 1 events" in capsys.readouterr().out
    assert output.exists()


@pytest.mark.asyncio
async def test_cli_alert_state_cleanup_and_missing_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit_dir = tmp_path / "audit"
    async with LogStore(audit_dir) as store:
        await store.write_alert(Alert(alert_id="alert", rule_name="rule"))

    assert await _cmd_alert_state(audit_dir, "alert", "resolve-alert") == 0
    assert "resolved" in capsys.readouterr().out
    assert await _cmd_alert_state(audit_dir, "missing", "suppress-alert") == 1
    assert "not found" in capsys.readouterr().err

    assert await _cmd_session_summary(
        audit_dir,
        argparse.Namespace(session_id="missing"),
    ) == 1
    assert "No audit data" in capsys.readouterr().out

    assert await _cmd_cleanup(audit_dir, argparse.Namespace(days=30)) == 0
    assert "Cleaned up" in capsys.readouterr().out
