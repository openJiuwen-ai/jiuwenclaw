# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Atomic JSON storage for evidence, claims, and audit traces."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from jiuwenswarm.research_evidence.schemas import Claim, Evidence


class EvidenceStore:
    """Small, inspectable evidence store rooted in a project directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.evidence_path = self.root / "evidence.json"
        self.claims_path = self.root / "claims.json"
        self.events_path = self.root / "events.jsonl"

    def list_evidence(self) -> list[Evidence]:
        payload = self._read_json(self.evidence_path, default=[])
        if not isinstance(payload, list):
            raise ValueError(f"invalid evidence store: {self.evidence_path}")
        return [Evidence.from_dict(item) for item in payload]

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        target = str(evidence_id).strip()
        return next((item for item in self.list_evidence() if item.evidence_id == target), None)

    def upsert_evidence(self, evidence: Evidence) -> None:
        items = {item.evidence_id: item for item in self.list_evidence()}
        items[evidence.evidence_id] = evidence
        ordered = [items[key].to_dict() for key in sorted(items)]
        self._atomic_write_json(self.evidence_path, ordered)

    def list_claims(self) -> list[Claim]:
        payload = self._read_json(self.claims_path, default=[])
        if not isinstance(payload, list):
            raise ValueError(f"invalid claim store: {self.claims_path}")
        return [Claim.from_dict(item) for item in payload]

    def upsert_claim(self, claim: Claim) -> None:
        items = {item.claim_id: item for item in self.list_claims()}
        items[claim.claim_id] = claim
        ordered = [items[key].to_dict() for key in sorted(items)]
        self._atomic_write_json(self.claims_path, ordered)

    def append_event(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _read_json(path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
