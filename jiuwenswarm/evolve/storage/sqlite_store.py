# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SQLite storage backend for evolution records."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Path to the OTEL traces database (read-only from evolve's perspective).
DEFAULT_TRACES_DB = "traces.db"


class SqliteStore:
    """SQLite backend for evolution data (evolution.db).

    Maintains tables for proposals, decision_results, apply_records,
    trace_batches, and training_candidates.  Also provides read-only
    access to the OTEL traces database (traces.db).
    """

    def __init__(self, db_path: str, traces_db_path: str | None = None) -> None:
        self._db_path = db_path
        self._traces_db_path = traces_db_path or DEFAULT_TRACES_DB
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _get_traces_conn(self) -> sqlite3.Connection:
        """Return a *read-only* connection to traces.db."""
        import os as _os

        # Use URI mode with forward-slash path for cross-platform compatibility.
        # On Windows, backslashes break the file: URI so we normalize and
        # encode the path properly.
        path = self._traces_db_path.replace("\\", "/")
        uri = f"file:{path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
        except Exception:
            # Fallback: regular connection (writable, but we won't write)
            conn = sqlite3.connect(self._traces_db_path)
            conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id TEXT,
                proposal_type TEXT NOT NULL,
                failure_evidence TEXT NOT NULL,
                root_cause TEXT NOT NULL,
                targeted_fix TEXT NOT NULL,
                predicted_impact TEXT NOT NULL,
                risk TEXT,
                state TEXT NOT NULL DEFAULT 'candidate',
                proposer_name TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version TEXT NOT NULL DEFAULT 'proposal.v1',
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS decision_results (
                decision_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                policy_name TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                score REAL NOT NULL,
                reason TEXT NOT NULL,
                suggestion TEXT NOT NULL,
                blocking INTEGER NOT NULL DEFAULT 0,
                failed_checks TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                schema_version TEXT NOT NULL DEFAULT 'decision_result.v1',
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id)
            );

            CREATE TABLE IF NOT EXISTS apply_records (
                apply_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_store TEXT NOT NULL,
                target_id TEXT,
                status TEXT NOT NULL,
                stored_object_id TEXT,
                reason TEXT NOT NULL,
                applier_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version TEXT NOT NULL DEFAULT 'apply_record.v1',
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id)
            );

            CREATE TABLE IF NOT EXISTS trace_batches (
                batch_id TEXT PRIMARY KEY,
                trace_ids TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS training_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                proposal_id TEXT,
                batch_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_proposals_batch
                ON proposals(batch_id);
            CREATE INDEX IF NOT EXISTS idx_proposals_state
                ON proposals(state);
            CREATE INDEX IF NOT EXISTS idx_decision_results_proposal
                ON decision_results(proposal_id);
            CREATE INDEX IF NOT EXISTS idx_apply_records_proposal
                ON apply_records(proposal_id);
            CREATE INDEX IF NOT EXISTS idx_training_candidates_trace
                ON training_candidates(trace_id);
            CREATE INDEX IF NOT EXISTS idx_training_candidates_status
                ON training_candidates(status);
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # Trace batches
    # ------------------------------------------------------------------

    def save_trace_batch(self, batch: object) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO trace_batches "
            "(batch_id, trace_ids, source, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                batch.batch_id,  # type: ignore[attr-defined]
                json.dumps(batch.trace_ids),  # type: ignore[attr-defined]
                batch.source,  # type: ignore[attr-defined]
                batch.created_at,  # type: ignore[attr-defined]
                json.dumps(getattr(batch, "metadata", {})),
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Proposals
    # ------------------------------------------------------------------

    def save_proposal(self, proposal: object) -> None:
        conn = self._get_conn()
        evidence_json = json.dumps(
            [e.model_dump() for e in proposal.failure_evidence]  # type: ignore[attr-defined]
        )
        conn.execute(
            "INSERT OR REPLACE INTO proposals "
            "(proposal_id, target_type, target_id, proposal_type, "
            "failure_evidence, root_cause, targeted_fix, predicted_impact, "
            "risk, state, proposer_name, batch_id, created_at, "
            "schema_version, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal.proposal_id,  # type: ignore[attr-defined]
                str(proposal.target_type),  # type: ignore[attr-defined]
                proposal.target_id,  # type: ignore[attr-defined]
                proposal.proposal_type,  # type: ignore[attr-defined]
                evidence_json,
                proposal.root_cause,  # type: ignore[attr-defined]
                json.dumps(proposal.targeted_fix),  # type: ignore[attr-defined]
                proposal.predicted_impact,  # type: ignore[attr-defined]
                proposal.risk,  # type: ignore[attr-defined]
                str(proposal.state),  # type: ignore[attr-defined]
                proposal.proposer_name,  # type: ignore[attr-defined]
                getattr(proposal, "batch_id", ""),
                proposal.created_at,  # type: ignore[attr-defined]
                proposal.schema_version,  # type: ignore[attr-defined]
                json.dumps(proposal.metadata),  # type: ignore[attr-defined]
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Decision results
    # ------------------------------------------------------------------

    def save_decision_result(self, dr: object) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO decision_results "
            "(decision_id, proposal_id, policy_name, policy_version, "
            "score, reason, suggestion, blocking, failed_checks, "
            "created_at, schema_version, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dr.decision_id,  # type: ignore[attr-defined]
                dr.proposal_id,  # type: ignore[attr-defined]
                dr.policy_name,  # type: ignore[attr-defined]
                dr.policy_version,  # type: ignore[attr-defined]
                dr.score,  # type: ignore[attr-defined]
                dr.reason,  # type: ignore[attr-defined]
                str(dr.suggestion),  # type: ignore[attr-defined]
                1 if dr.blocking else 0,  # type: ignore[attr-defined]
                json.dumps(dr.failed_checks),  # type: ignore[attr-defined]
                dr.created_at,  # type: ignore[attr-defined]
                dr.schema_version,  # type: ignore[attr-defined]
                json.dumps(dr.metadata),  # type: ignore[attr-defined]
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Apply records
    # ------------------------------------------------------------------

    def save_apply_record(self, ar: object) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO apply_records "
            "(apply_id, proposal_id, target_type, target_store, "
            "target_id, status, stored_object_id, reason, applier_name, "
            "created_at, schema_version, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ar.apply_id,  # type: ignore[attr-defined]
                ar.proposal_id,  # type: ignore[attr-defined]
                str(ar.target_type),  # type: ignore[attr-defined]
                str(ar.target_store),  # type: ignore[attr-defined]
                ar.target_id,  # type: ignore[attr-defined]
                str(ar.status),  # type: ignore[attr-defined]
                ar.stored_object_id,  # type: ignore[attr-defined]
                ar.reason,  # type: ignore[attr-defined]
                ar.applier_name,  # type: ignore[attr-defined]
                ar.created_at,  # type: ignore[attr-defined]
                ar.schema_version,  # type: ignore[attr-defined]
                json.dumps(ar.metadata),  # type: ignore[attr-defined]
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Training candidates
    # ------------------------------------------------------------------

    def save_training_candidate(
        self, trace_id: str, proposal_id: str, batch_id: str
    ) -> None:
        """Insert a trace into training_candidates (idempotent)."""
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM training_candidates WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if existing is not None:
            logger.debug("training_candidates: trace_id=%s already exists, skip", trace_id)
            return
        conn.execute(
            "INSERT INTO training_candidates "
            "(trace_id, status, proposal_id, batch_id, created_at) "
            "VALUES (?, 'pending', ?, ?, ?)",
            (trace_id, proposal_id, batch_id, self._now()),
        )
        conn.commit()
        logger.debug("training_candidates: inserted trace_id=%s", trace_id)

    # ------------------------------------------------------------------
    # Trace reading (read-only from traces.db)
    # ------------------------------------------------------------------

    def read_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """Read all spans for *trace_id* from traces.db."""
        try:
            conn = self._get_traces_conn()
            rows = conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? "
                "ORDER BY start_time_ns",
                (trace_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Failed to read spans for trace_id=%s: %s", trace_id, exc)
            return []

    def get_recent_trace_ids(self, limit: int = 20) -> list[str]:
        """Return the *limit* most recent distinct trace_ids from traces.db."""
        try:
            conn = self._get_traces_conn()
            rows = conn.execute(
                "SELECT DISTINCT trace_id FROM spans "
                "ORDER BY start_time_ns DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [r["trace_id"] for r in rows]
        except Exception as exc:
            logger.warning("Failed to get recent trace ids: %s", exc)
            return []

    def get_trace_ids_since(self, since: str, limit: int = 100) -> list[str]:
        """Return trace_ids with spans after *since* (ISO timestamp)."""
        try:
            conn = self._get_traces_conn()
            rows = conn.execute(
                "SELECT DISTINCT trace_id FROM spans "
                "WHERE created_at >= ? "
                "ORDER BY start_time_ns DESC LIMIT ?",
                (since, limit),
            ).fetchall()
            return [r["trace_id"] for r in rows]
        except Exception as exc:
            logger.warning("Failed to get trace ids since %s: %s", since, exc)
            return []

    def get_trace_ids_by_benchmark(
        self, benchmark_run_id: str, limit: int = 100
    ) -> list[str]:
        """Return trace_ids tagged with *benchmark_run_id*."""
        try:
            conn = self._get_traces_conn()
            # The benchmark_run_id is stored in the resource or attributes JSON.
            # We search across both columns with a LIKE match.
            rows = conn.execute(
                "SELECT DISTINCT trace_id FROM spans "
                "WHERE (attributes LIKE ? OR resource LIKE ?) "
                "ORDER BY start_time_ns DESC LIMIT ?",
                (f"%{benchmark_run_id}%", f"%{benchmark_run_id}%", limit),
            ).fetchall()
            return [r["trace_id"] for r in rows]
        except Exception as exc:
            logger.warning(
                "Failed to get trace ids for benchmark %s: %s",
                benchmark_run_id, exc,
            )
            return []

    def validate_trace_ids(
        self, trace_ids: list[str]
    ) -> tuple[bool, list[str]]:
        """Validate that all trace_ids exist in traces.db.

        Args:
            trace_ids: List of trace IDs to validate

        Returns:
            Tuple of (all_valid, missing_ids):
            - all_valid: True if all trace IDs exist
            - missing_ids: List of trace IDs not found in database
        """
        if not trace_ids:
            return True, []

        try:
            conn = self._get_traces_conn()
            placeholders = ",".join("?" * len(trace_ids))
            rows = conn.execute(
                f"SELECT DISTINCT trace_id FROM spans WHERE trace_id IN ({placeholders})",
                trace_ids,
            ).fetchall()
            found_ids = {r["trace_id"] for r in rows}

            missing_ids = [tid for tid in trace_ids if tid not in found_ids]
            all_valid = len(missing_ids) == 0

            return all_valid, missing_ids
        except Exception as exc:
            logger.warning("Failed to validate trace_ids: %s", exc)
            return False, trace_ids

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_batches(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT batch_id, source, created_at, trace_ids FROM trace_batches "
            "ORDER BY created_at DESC"
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["trace_ids"] = json.loads(d.get("trace_ids", "[]"))
            # Count proposals in this batch
            count = conn.execute(
                "SELECT COUNT(*) FROM proposals WHERE batch_id = ?",
                (d["batch_id"],),
            ).fetchone()
            d["proposal_count"] = count[0] if count else 0
            results.append(d)
        return results

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        conn = self._get_conn()
        batch_row = conn.execute(
            "SELECT * FROM trace_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch_row is None:
            return None

        proposals = conn.execute(
            "SELECT * FROM proposals WHERE batch_id = ?", (batch_id,)
        ).fetchall()
        decisions = conn.execute(
            "SELECT d.* FROM decision_results d "
            "JOIN proposals p ON d.proposal_id = p.proposal_id "
            "WHERE p.batch_id = ?",
            (batch_id,),
        ).fetchall()
        apply_recs = conn.execute(
            "SELECT a.* FROM apply_records a "
            "JOIN proposals p ON a.proposal_id = p.proposal_id "
            "WHERE p.batch_id = ?",
            (batch_id,),
        ).fetchall()

        return {
            "batch": dict(batch_row),
            "proposals": [dict(p) for p in proposals],
            "decision_results": [dict(d) for d in decisions],
            "apply_records": [dict(a) for a in apply_recs],
        }

    def query_by_trace_id(self, trace_id: str) -> dict[str, Any]:
        """Return the full audit chain for *trace_id*."""
        conn = self._get_conn()
        # Find proposals whose failure_evidence JSON contains this trace_id
        proposals = conn.execute(
            "SELECT * FROM proposals WHERE failure_evidence LIKE ?",
            (f"%{trace_id}%",),
        ).fetchall()
        result: dict[str, Any] = {"trace_id": trace_id, "proposals": []}
        for prop in proposals:
            prop_dict = dict(prop)
            pid = prop_dict["proposal_id"]
            decisions = conn.execute(
                "SELECT * FROM decision_results WHERE proposal_id = ?",
                (pid,),
            ).fetchall()
            apply_recs = conn.execute(
                "SELECT * FROM apply_records WHERE proposal_id = ?",
                (pid,),
            ).fetchall()
            result["proposals"].append(
                {
                    "proposal": prop_dict,
                    "decision_results": [dict(d) for d in decisions],
                    "apply_records": [dict(a) for a in apply_recs],
                }
            )
        return result

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_training_candidates(
        self, status: str | None = None
    ) -> list[dict[str, Any]]:
        conn = self._get_conn()
        if status:
            rows = conn.execute(
                "SELECT * FROM training_candidates WHERE status = ? "
                "ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM training_candidates ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
