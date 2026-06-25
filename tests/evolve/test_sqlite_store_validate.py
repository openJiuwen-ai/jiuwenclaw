# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for SqliteStore.validate_trace_ids method."""

import sqlite3
import tempfile
from pathlib import Path

from jiuwenswarm.evolve.storage.sqlite_store import SqliteStore


def _make_traces_db(db_path: Path, trace_ids: list[str]) -> None:
    """Create a minimal traces.db with the given trace_ids in the spans table."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS spans "
        "(trace_id TEXT, span_id TEXT, start_time_ns INTEGER, "
        "created_at TEXT, attributes TEXT, resource TEXT)"
    )
    for i, tid in enumerate(trace_ids):
        conn.execute(
            "INSERT INTO spans (trace_id, span_id, start_time_ns, created_at, "
            "attributes, resource) VALUES (?, ?, ?, ?, '{}', '{}')",
            (tid, f"span-{i}", 1705308600_000000000 + i, "2024-01-15T10:30:00Z"),
        )
    conn.commit()
    conn.close()


class TestValidateTraceIds:
    """Test validate_trace_ids method on SqliteStore."""

    def _make_store(self, trace_ids: list[str]) -> SqliteStore:
        """Create a SqliteStore wired to a temp traces.db containing the given trace_ids."""
        tmpdir = tempfile.mkdtemp()
        evodb = Path(tmpdir) / "evolution.db"
        tracesdb = Path(tmpdir) / "traces.db"
        _make_traces_db(tracesdb, trace_ids)
        return SqliteStore(str(evodb), traces_db_path=str(tracesdb))

    def test_all_valid(self):
        """All provided trace IDs exist in traces.db."""
        store = self._make_store(["abc123", "def456"])
        all_valid, missing = store.validate_trace_ids(["abc123", "def456"])
        assert all_valid is True
        assert missing == []

    def test_all_invalid(self):
        """No provided trace IDs exist in traces.db."""
        store = self._make_store(["abc123", "def456"])
        all_valid, missing = store.validate_trace_ids(["xxx999", "yyy888"])
        assert all_valid is False
        assert sorted(missing) == sorted(["xxx999", "yyy888"])

    def test_mixed_valid_invalid(self):
        """Some trace IDs exist, some do not."""
        store = self._make_store(["abc123", "def456"])
        all_valid, missing = store.validate_trace_ids(["abc123", "xxx999"])
        assert all_valid is False
        assert missing == ["xxx999"]

    def test_empty_list(self):
        """Empty trace_ids list should return (True, [])."""
        store = self._make_store(["abc123"])
        all_valid, missing = store.validate_trace_ids([])
        assert all_valid is True
        assert missing == []
