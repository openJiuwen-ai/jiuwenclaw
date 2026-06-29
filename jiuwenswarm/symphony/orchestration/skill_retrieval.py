"""Skill retrieval candidate narrowing for Symphony orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from jiuwenswarm.symphony.orchestration.artifacts import ScoreArtifacts
from jiuwenswarm.symphony.skill_retrieval.config import load_settings
from jiuwenswarm.symphony.skill_retrieval.retrieve_service import (
    run_structured_skill_retrieve,
)


@dataclass(frozen=True)
class OrchestrationSkillRetrievalSelection:
    enabled: bool
    used: bool = False
    candidate_skill_ids: tuple[str, ...] = ()
    fallback_reason: str = ""
    candidate_records: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_skill_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "used": self.used,
            "candidate_skill_ids": list(self.candidate_skill_ids),
            "candidate_count": self.candidate_count,
            "fallback_reason": self.fallback_reason,
            "candidate_records": [dict(record) for record in self.candidate_records],
        }


def select_orchestration_skill_candidates(
    *,
    query: str,
    artifacts: ScoreArtifacts,
) -> OrchestrationSkillRetrievalSelection:
    settings = load_settings()
    if not settings.enabled:
        return OrchestrationSkillRetrievalSelection(enabled=False)

    try:
        status = _skill_retrieval_status()
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"skill retrieval status failed: {exc}")
    if not bool(status.get("index_exists")):
        return _fallback("skill retrieval index does not exist")
    if not bool(status.get("fresh")):
        return _fallback("skill retrieval index is stale")
    if (
        not str(settings.llm.model or "").strip()
        or not str(settings.llm.api_key or "").strip()
    ):
        return _fallback("skill retrieval LLM config is missing")

    raw_index_dir = str(status.get("index_dir") or "").strip()
    if not raw_index_dir:
        return _fallback("skill retrieval index path is empty")
    index_dir = Path(raw_index_dir).expanduser()

    try:
        result = run_structured_skill_retrieve(
            settings=settings,
            index_dir=index_dir,
            query=str(query or "").strip(),
        )
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"skill retrieval failed: {exc}")

    known_skill_ids = set(artifacts.skill_by_id)
    candidate_records = _simplify_candidate_records(
        getattr(result, "candidate_records", ()),
        known_skill_ids=known_skill_ids,
    )
    candidate_skill_ids = _candidate_skill_ids_from_result(
        result,
        known_skill_ids=known_skill_ids,
    )
    if not candidate_skill_ids:
        return OrchestrationSkillRetrievalSelection(
            enabled=True,
            fallback_reason="skill retrieval returned no candidates present in current score",
            candidate_records=tuple(candidate_records),
        )

    return OrchestrationSkillRetrievalSelection(
        enabled=True,
        used=True,
        candidate_skill_ids=tuple(candidate_skill_ids),
        candidate_records=tuple(candidate_records),
    )


def _fallback(reason: str) -> OrchestrationSkillRetrievalSelection:
    return OrchestrationSkillRetrievalSelection(
        enabled=True,
        fallback_reason=str(reason or "").strip(),
    )


def _skill_retrieval_status() -> dict[str, Any]:
    from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
    from jiuwenswarm.symphony.skill_retrieval.index_service import SkillIndexService

    return SkillIndexService(SkillManager()).status()


def _candidate_skill_ids_from_result(
    result: Any,
    *,
    known_skill_ids: set[str],
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    records = getattr(result, "candidate_records", ()) or ()
    for record in records:
        if not isinstance(record, dict):
            continue
        _append_known_candidate_ids(
            _candidate_id_values(record),
            known_skill_ids=known_skill_ids,
            output=output,
            seen=seen,
        )

    payloads = getattr(result, "payloads", ()) or ()
    _append_known_candidate_ids(
        (str(payload or "").strip() for payload in payloads),
        known_skill_ids=known_skill_ids,
        output=output,
        seen=seen,
    )
    return output


def _append_known_candidate_ids(
    values: Iterable[str],
    *,
    known_skill_ids: set[str],
    output: list[str],
    seen: set[str],
) -> None:
    for value in values:
        candidate_id = str(value or "").strip()
        if (
            not candidate_id
            or candidate_id in seen
            or candidate_id not in known_skill_ids
        ):
            continue
        seen.add(candidate_id)
        output.append(candidate_id)


def _candidate_id_values(record: dict[str, Any]) -> Iterable[str]:
    for key in ("worker_id", "resolved_payload"):
        value = str(record.get(key) or "").strip()
        if value:
            yield value


def _simplify_candidate_records(
    records: Iterable[Any],
    *,
    known_skill_ids: set[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        skill_id = next(
            (
                value
                for value in _candidate_id_values(record)
                if value in known_skill_ids
            ),
            "",
        )
        simplified = {
            "rank": record.get("rank"),
            "skill_id": skill_id,
            "worker_id": record.get("worker_id"),
            "resolved_payload": record.get("resolved_payload"),
            "skill_name": record.get("skill_name"),
            "score": record.get("score"),
            "source": record.get("source"),
        }
        output.append(
            {
                key: value
                for key, value in simplified.items()
                if value not in (None, "")
            }
        )
    return output
