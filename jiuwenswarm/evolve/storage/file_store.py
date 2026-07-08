# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File system storage backend for evolution records (JSON).

Organises records by batch_id under the evolution workspace directory
so developers can inspect evolution output without querying SQLite.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenswarm.evolve.models import (
        ApplyRecord,
        DecisionResult,
        Proposal,
        TraceBatch,
    )

logger = logging.getLogger(__name__)


class FileStore:
    """File system backend that writes evolution records as JSON files.

    Layout::

        <root_dir>/
        ├── batches/
        │   └── <batch_id>/
        │       ├── batch.json
        │       ├── proposals.json
        │       ├── decisions.json
        │       └── apply_records.json
        └── index.json
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)
        self._batches_dir = self._root / "batches"
        self._batches_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "index.json"
        self._index: dict[str, dict] = self._load_index()

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _load_index(self) -> dict[str, dict]:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Failed to load index.json, starting fresh")
        return {}

    def _save_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _batch_dir(self, batch_id: str) -> Path:
        d = self._batches_dir / batch_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _read_json_list(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read %s", path)
            return []

    def _append_to_json_list(self, path: Path, item: dict) -> None:
        items = self._read_json_list(path)
        items.append(item)
        path.write_text(
            json.dumps(items, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Save methods
    # ------------------------------------------------------------------

    def save_trace_batch(self, batch: TraceBatch) -> None:
        d = self._batch_dir(batch.batch_id)
        data = {
            "batch_id": batch.batch_id,
            "trace_ids": batch.trace_ids,
            "source": batch.source,
            "created_at": batch.created_at,
            "metadata": getattr(batch, "metadata", {}),
        }
        (d / "batch.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._index[batch.batch_id] = {
            "created_at": batch.created_at,
            "source": batch.source,
            "trace_count": len(batch.trace_ids),
        }
        self._save_index()

    def save_proposal(self, proposal: Proposal) -> None:
        # Proposals are associated with a batch via the batch_id attribute
        # added by the pipeline. If not set, we can't store it in batch dir.
        batch_id = getattr(proposal, "batch_id", None)
        if not batch_id:
            logger.warning(
                "Proposal %s has no batch_id, using fallback",
                proposal.proposal_id,
            )
            batch_id = "unknown"
        d = self._batch_dir(batch_id)
        data = {
            "proposal_id": proposal.proposal_id,
            "target_type": str(proposal.target_type),
            "target_id": proposal.target_id,
            "proposal_type": proposal.proposal_type,
            "failure_evidence": [
                e.model_dump() for e in proposal.failure_evidence
            ],
            "root_cause": proposal.root_cause,
            "targeted_fix": proposal.targeted_fix,
            "predicted_impact": proposal.predicted_impact,
            "risk": proposal.risk,
            "state": str(proposal.state),
            "proposer_name": proposal.proposer_name,
            "created_at": proposal.created_at,
            "schema_version": proposal.schema_version,
            "metadata": proposal.metadata,
        }
        self._append_to_json_list(d / "proposals.json", data)
        # Update index
        if batch_id in self._index:
            self._index[batch_id]["proposal_count"] = (
                self._index[batch_id].get("proposal_count", 0) + 1
            )
            self._save_index()

    def save_decision_result(self, dr: DecisionResult) -> None:
        data = {
            "decision_id": dr.decision_id,
            "proposal_id": dr.proposal_id,
            "policy_name": dr.policy_name,
            "policy_version": dr.policy_version,
            "score": dr.score,
            "reason": dr.reason,
            "suggestion": str(dr.suggestion),
            "blocking": dr.blocking,
            "failed_checks": dr.failed_checks,
            "created_at": dr.created_at,
            "schema_version": dr.schema_version,
            "metadata": dr.metadata,
        }
        # Use proposal_id to locate the batch — but we don't have that mapping easily.
        # Store in a decisions.jsonl at root level for simplicity.
        decisions_file = self._root / "decisions.json"
        self._append_to_json_list(decisions_file, data)

    def save_apply_record(self, ar: ApplyRecord) -> None:
        data = {
            "apply_id": ar.apply_id,
            "proposal_id": ar.proposal_id,
            "target_type": str(ar.target_type),
            "target_store": str(ar.target_store),
            "target_id": ar.target_id,
            "status": str(ar.status),
            "stored_object_id": ar.stored_object_id,
            "reason": ar.reason,
            "applier_name": ar.applier_name,
            "created_at": ar.created_at,
            "schema_version": ar.schema_version,
            "metadata": ar.metadata,
        }
        apply_file = self._root / "apply_records.json"
        self._append_to_json_list(apply_file, data)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_batch_dir(self, batch_id: str) -> Path:
        return self._batch_dir(batch_id)
