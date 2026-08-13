from __future__ import annotations

import json

from jiuwenswarm.research_evidence.schemas import Claim, Evidence, EvidenceKind
from jiuwenswarm.research_evidence.store import EvidenceStore


def test_store_round_trip_and_deterministic_upsert(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    store.upsert_evidence(
        Evidence("E2", EvidenceKind.EXPERIMENT, "accuracy 82%", "run-2", reliability=0.9)
    )
    store.upsert_evidence(
        Evidence("E1", EvidenceKind.LITERATURE, "prior result", "doi:example", reliability=0.8)
    )
    store.upsert_evidence(
        Evidence("E2", EvidenceKind.EXPERIMENT, "accuracy 84%", "run-3", reliability=0.95)
    )
    store.upsert_claim(Claim("C1", "The method reaches 84% accuracy", ["E2"]))

    assert [item.evidence_id for item in store.list_evidence()] == ["E1", "E2"]
    assert store.get_evidence("E2").content == "accuracy 84%"
    assert store.list_claims()[0].claim_id == "C1"
    assert json.loads(store.evidence_path.read_text(encoding="utf-8"))[1]["source"] == "run-3"


def test_append_event_is_valid_jsonl(tmp_path):
    store = EvidenceStore(tmp_path)
    store.append_event({"event": "selected", "ids": ["E1", "E2"]})
    store.append_event({"event": "verified", "ok": True})
    lines = store.events_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["selected", "verified"]
