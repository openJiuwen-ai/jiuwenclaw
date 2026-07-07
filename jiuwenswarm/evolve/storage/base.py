# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Abstract base for the dual-backend EvolutionStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenswarm.evolve.models import (
        ApplyRecord,
        DecisionResult,
        Proposal,
        TraceBatch,
    )
    from jiuwenswarm.evolve.storage.file_store import FileStore
    from jiuwenswarm.evolve.storage.sqlite_store import SqliteStore


class EvolutionStore:
    """Dual-backend store that writes to both SQLite and file system.

    All ``save_*`` methods delegate to both backends so records are
    available for structured query (SQLite) and human inspection (files).
    """

    def __init__(self, sqlite_backend: SqliteStore, file_backend: FileStore) -> None:
        self._sqlite = sqlite_backend
        self._file = file_backend

    # -- Trace batches ----------------------------------------------------

    def save_trace_batch(self, batch: TraceBatch) -> None:
        self._sqlite.save_trace_batch(batch)
        self._file.save_trace_batch(batch)

    # -- Proposals --------------------------------------------------------

    def save_proposal(self, proposal: Proposal) -> None:
        self._sqlite.save_proposal(proposal)
        self._file.save_proposal(proposal)

    def save_proposals(self, proposals: list[Proposal]) -> None:
        for p in proposals:
            self.save_proposal(p)

    # -- Decision results -------------------------------------------------

    def save_decision_result(self, dr: DecisionResult) -> None:
        self._sqlite.save_decision_result(dr)
        self._file.save_decision_result(dr)

    def save_decision_results(
        self, results: list[DecisionResult]
    ) -> None:
        for r in results:
            self.save_decision_result(r)

    # -- Apply records ----------------------------------------------------

    def save_apply_record(self, ar: ApplyRecord) -> None:
        self._sqlite.save_apply_record(ar)
        self._file.save_apply_record(ar)

    def save_apply_records(
        self, records: list[ApplyRecord]
    ) -> None:
        for r in records:
            self.save_apply_record(r)

    # -- Training candidates ----------------------------------------------

    def save_training_candidate(
        self, trace_id: str, proposal_id: str, batch_id: str
    ) -> None:
        self._sqlite.save_training_candidate(trace_id, proposal_id, batch_id)

    # -- Queries ----------------------------------------------------------

    def read_spans(self, trace_id: str) -> list[dict]:
        """Read OTEL spans for *trace_id* from traces.db (read-only)."""
        return self._sqlite.read_spans(trace_id)

    def list_batches(self) -> list[dict]:
        return self._sqlite.list_batches()

    def get_batch(self, batch_id: str) -> dict | None:
        return self._sqlite.get_batch(batch_id)

    def query_by_trace_id(self, trace_id: str) -> dict:
        """Return all records linked to *trace_id*."""
        return self._sqlite.query_by_trace_id(trace_id)

    def get_proposal(self, proposal_id: str) -> dict | None:
        return self._sqlite.get_proposal(proposal_id)

    # -- Trace discovery (read-only, delegated to SQLite backend) ---------

    def get_recent_trace_ids(self, limit: int = 20) -> list[str]:
        return self._sqlite.get_recent_trace_ids(limit=limit)

    def get_trace_ids_since(self, since: str, limit: int = 100) -> list[str]:
        return self._sqlite.get_trace_ids_since(since, limit=limit)

    def get_trace_ids_by_benchmark(
        self, benchmark_run_id: str, limit: int = 100
    ) -> list[str]:
        return self._sqlite.get_trace_ids_by_benchmark(
            benchmark_run_id, limit=limit
        )

    def validate_trace_ids(self, trace_ids: list[str]) -> tuple[bool, list[str]]:
        return self._sqlite.validate_trace_ids(trace_ids)
