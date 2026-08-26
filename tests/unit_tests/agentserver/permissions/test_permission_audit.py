"""Tests for compact auto-permission audit events."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jiuwenswarm.agents.harness.common.rails.permissions.audit import (
    emit_permission_audit,
    logger as audit_logger,
)
from jiuwenswarm.agents.harness.common.rails.permissions.persistent_audit import PersistentAuditWriter
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import build_tool_decision_facts


def _facts(tmp_path: Path):
    return build_tool_decision_facts(
        "read_file",
        {"path": str(tmp_path / "secret-token.txt")},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )


def _shell_facts(tmp_path: Path):
    return build_tool_decision_facts(
        "mcp_exec_command",
        {"command": "uv pip install -e ."},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )


def _capture(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO, logger=audit_logger.name)
    monkeypatch.setattr(audit_logger, "handlers", [*audit_logger.handlers, caplog.handler])
    monkeypatch.setattr(
        logging.getLogger("jiuwenswarm.agents.harness.common.rails.permissions"),
        "propagate",
        True,
    )


def test_audit_has_no_raw_args_or_argument_digest(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture(caplog, monkeypatch)
    emit_permission_audit(
        _facts(tmp_path),
        decision="deny",
        reason="policy_deny",
        degraded=False,
    )

    assert "secret-token.txt" not in caplog.text
    assert "args_digest" not in caplog.text
    assert "normalized_args_hash" not in caplog.text
    assert '"accesses_known":true' in caplog.text


def test_audit_persists_only_compact_record(tmp_path: Path) -> None:
    writer = PersistentAuditWriter(data_root=tmp_path / "data")
    result = emit_permission_audit(
        _facts(tmp_path),
        decision="allow",
        reason="reviewer_allow_once",
        degraded=False,
        persistent_writer=writer,
    )

    assert result is not None and result.persisted is True
    content = result.path.read_text(encoding="utf-8")
    assert "secret-token.txt" not in content
    assert "args_digest" not in content


def test_shell_audit_records_incomplete_accesses_in_log_and_persistence(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture(caplog, monkeypatch)
    writer = PersistentAuditWriter(data_root=tmp_path / "data")

    result = emit_permission_audit(
        _shell_facts(tmp_path),
        decision="ask",
        reason="reviewer_manual",
        degraded=False,
        persistent_writer=writer,
    )

    assert '"accesses_known":false' in caplog.text
    assert result is not None and result.persisted is True
    content = result.path.read_text(encoding="utf-8")
    assert '"accesses_known":false' in content


def test_audit_keeps_allowed_route_metadata(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture(caplog, monkeypatch)
    emit_permission_audit(
        _facts(tmp_path),
        decision="ask",
        reason="browser_network_guard_unverified",
        degraded=True,
        extra={
            "decision_source": "host_route",
            "host_route_source": "manual_only",
        },
    )
    assert '"decision_source":"host_route"' in caplog.text
    assert '"host_route_source":"manual_only"' in caplog.text
