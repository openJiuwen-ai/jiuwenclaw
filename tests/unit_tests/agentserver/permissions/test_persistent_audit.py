"""Tests for compact persistent permission audit records."""

from __future__ import annotations

import json
from pathlib import Path

from jiuwenswarm.agents.harness.common.rails.permissions.persistent_audit import (
    PersistentAuditWriter,
    resolve_persistent_audit_root,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import build_tool_decision_facts


def _facts(tmp_path: Path):
    return build_tool_decision_facts(
        "read_file",
        {"path": str(tmp_path / "secret-token.txt")},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )


def test_persistent_audit_has_no_argument_digest_or_secret_state(tmp_path: Path) -> None:
    writer = PersistentAuditWriter(data_root=tmp_path / "data")
    result = writer.write(
        _facts(tmp_path),
        decision="allow",
        reason="reviewer_allow_once",
        degraded=False,
        extra={
            "reviewer_lifecycle": "approved",
            "reviewer_override_token": "must-not-persist",
        },
    )

    content = result.path.read_text(encoding="utf-8")
    record = json.loads(content)
    assert result.persisted is True
    assert "args_digest" not in record
    assert "normalized_args_hash" not in record
    assert "secret-token.txt" not in content
    assert "must-not-persist" not in content
    assert not hasattr(writer, "secret_key")


def test_persistent_audit_keeps_compact_route_provenance(tmp_path: Path) -> None:
    writer = PersistentAuditWriter(data_root=tmp_path / "data")
    result = writer.write(
        _facts(tmp_path),
        decision="ask",
        reason="semantic_review_required",
        degraded=False,
        extra={
            "host_route_reason": "semantic_review_required",
            "host_route_source": "semantic_reviewer",
            "decision_source": "auto_reviewer",
        },
    )
    record = json.loads(result.path.read_text(encoding="utf-8"))
    assert record["host_route_source"] == "semantic_reviewer"
    assert record["decision_source"] == "auto_reviewer"


def test_persistent_audit_keeps_host_effect_uncertainty(tmp_path: Path) -> None:
    writer = PersistentAuditWriter(data_root=tmp_path / "data")
    result = writer.write(
        _facts(tmp_path),
        decision="allow",
        reason="reviewer_allow_once",
        degraded=False,
        extra={
            "filesystem_effect_status": "unknown",
            "network_effect_status": "unknown",
            "reviewer_acknowledged_unknowns": (
                "filesystem_effect",
                "network_effect",
            ),
            "reviewer_required_unknowns": (
                "filesystem_effect",
                "network_effect",
            ),
        },
    )

    record = json.loads(result.path.read_text(encoding="utf-8"))
    assert record["filesystem_effect_status"] == "unknown"
    assert record["network_effect_status"] == "unknown"
    assert record["reviewer_acknowledged_unknowns"] == [
        "filesystem_effect",
        "network_effect",
    ]
    assert record["reviewer_required_unknowns"] == [
        "filesystem_effect",
        "network_effect",
    ]


def test_persistent_audit_redacts_free_text(tmp_path: Path) -> None:
    writer = PersistentAuditWriter(data_root=tmp_path / "data")
    secret_path = tmp_path / ".env"
    result = writer.write(
        _facts(tmp_path),
        decision="ask",
        reason=f"cat {secret_path} && echo sk-secret-token",
        degraded=False,
        grant_reason=f"grant matched {secret_path}",
    )
    record = json.loads(result.path.read_text(encoding="utf-8"))
    assert record["reason"] == "[redacted]"
    assert record["grant_reason"] == "[redacted]"


def test_missing_or_unwritable_root_degrades(tmp_path: Path) -> None:
    missing = PersistentAuditWriter(data_root=None).write(
        _facts(tmp_path),
        decision="manual",
        reason="audit_missing_root",
        degraded=False,
    )
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    failed = PersistentAuditWriter(data_root=blocked).write(
        _facts(tmp_path),
        decision="allow",
        reason="writer_failure",
        degraded=False,
    )
    assert missing.reason == "audit_root_unavailable"
    assert failed.reason == "audit_write_failed"


def test_persistent_audit_root_resolves_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path / "env-data"))
    assert resolve_persistent_audit_root({}) == tmp_path / "env-data"
