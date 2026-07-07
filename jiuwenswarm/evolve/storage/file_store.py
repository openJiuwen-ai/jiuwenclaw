# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File system storage backend for evolution records (JSON).

Organises records by batch_id under the evolution workspace directory
so developers can inspect evolution output without querying SQLite.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

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

    def save_trace_batch(self, batch: object) -> None:
        d = self._batch_dir(batch.batch_id)  # type: ignore[attr-defined]
        data = {
            "batch_id": batch.batch_id,  # type: ignore[attr-defined]
            "trace_ids": batch.trace_ids,  # type: ignore[attr-defined]
            "source": batch.source,  # type: ignore[attr-defined]
            "created_at": batch.created_at,  # type: ignore[attr-defined]
            "metadata": getattr(batch, "metadata", {}),
        }
        (d / "batch.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._index[batch.batch_id] = {  # type: ignore[attr-defined]
            "created_at": batch.created_at,  # type: ignore[attr-defined]
            "source": batch.source,  # type: ignore[attr-defined]
            "trace_count": len(batch.trace_ids),  # type: ignore[attr-defined]
        }
        self._save_index()

    def save_proposal(self, proposal: object) -> None:
        # Proposals are associated with a batch via the batch_id attribute
        # added by the pipeline. If not set, we can't store it in batch dir.
        batch_id = getattr(proposal, "batch_id", None)
        if not batch_id:
            logger.warning(
                "Proposal %s has no batch_id, using fallback",
                proposal.proposal_id,  # type: ignore[attr-defined]
            )
            batch_id = "unknown"
        d = self._batch_dir(batch_id)
        data = {
            "proposal_id": proposal.proposal_id,  # type: ignore[attr-defined]
            "target_type": str(proposal.target_type),  # type: ignore[attr-defined]
            "target_id": proposal.target_id,  # type: ignore[attr-defined]
            "proposal_type": proposal.proposal_type,  # type: ignore[attr-defined]
            "failure_evidence": [
                e.model_dump() for e in proposal.failure_evidence  # type: ignore[attr-defined]
            ],
            "root_cause": proposal.root_cause,  # type: ignore[attr-defined]
            "targeted_fix": proposal.targeted_fix,  # type: ignore[attr-defined]
            "predicted_impact": proposal.predicted_impact,  # type: ignore[attr-defined]
            "risk": proposal.risk,  # type: ignore[attr-defined]
            "state": str(proposal.state),  # type: ignore[attr-defined]
            "proposer_name": proposal.proposer_name,  # type: ignore[attr-defined]
            "created_at": proposal.created_at,  # type: ignore[attr-defined]
            "schema_version": proposal.schema_version,  # type: ignore[attr-defined]
            "metadata": proposal.metadata,  # type: ignore[attr-defined]
        }
        self._append_to_json_list(d / "proposals.json", data)
        # Update index
        if batch_id in self._index:
            self._index[batch_id]["proposal_count"] = (
                self._index[batch_id].get("proposal_count", 0) + 1
            )
            self._save_index()

    def save_decision_result(self, dr: object) -> None:
        # We need to find the batch_id. Store decisions alongside proposals.
        # For simplicity, we write them to a batch-level file.
        # The pipeline will have set the proposal's batch_id.
        # We approximate by writing to the latest batch dir.
        # A better approach: the pipeline calls save_decision_results with batch context.
        # For now, write to a flat file.
        data = {
            "decision_id": dr.decision_id,  # type: ignore[attr-defined]
            "proposal_id": dr.proposal_id,  # type: ignore[attr-defined]
            "policy_name": dr.policy_name,  # type: ignore[attr-defined]
            "policy_version": dr.policy_version,  # type: ignore[attr-defined]
            "score": dr.score,  # type: ignore[attr-defined]
            "reason": dr.reason,  # type: ignore[attr-defined]
            "suggestion": str(dr.suggestion),  # type: ignore[attr-defined]
            "blocking": dr.blocking,  # type: ignore[attr-defined]
            "failed_checks": dr.failed_checks,  # type: ignore[attr-defined]
            "created_at": dr.created_at,  # type: ignore[attr-defined]
            "schema_version": dr.schema_version,  # type: ignore[attr-defined]
            "metadata": dr.metadata,  # type: ignore[attr-defined]
        }
        # Use proposal_id to locate the batch — but we don't have that mapping easily.
        # Store in a decisions.jsonl at root level for simplicity.
        decisions_file = self._root / "decisions.json"
        self._append_to_json_list(decisions_file, data)

    def save_apply_record(self, ar: object) -> None:
        data = {
            "apply_id": ar.apply_id,  # type: ignore[attr-defined]
            "proposal_id": ar.proposal_id,  # type: ignore[attr-defined]
            "target_type": str(ar.target_type),  # type: ignore[attr-defined]
            "target_store": str(ar.target_store),  # type: ignore[attr-defined]
            "target_id": ar.target_id,  # type: ignore[attr-defined]
            "status": str(ar.status),  # type: ignore[attr-defined]
            "stored_object_id": ar.stored_object_id,  # type: ignore[attr-defined]
            "reason": ar.reason,  # type: ignore[attr-defined]
            "applier_name": ar.applier_name,  # type: ignore[attr-defined]
            "created_at": ar.created_at,  # type: ignore[attr-defined]
            "schema_version": ar.schema_version,  # type: ignore[attr-defined]
            "metadata": ar.metadata,  # type: ignore[attr-defined]
        }
        apply_file = self._root / "apply_records.json"
        self._append_to_json_list(apply_file, data)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_batch_dir(self, batch_id: str) -> Path:
        return self._batch_dir(batch_id)
