"""Prompt and context construction for LLM relation matching."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from jiuwenswarm.symphony.fingerprint.models import SkillFingerprint
from jiuwenswarm.symphony.graph.models import (
    ALLOWED_RELATION_TYPES,
    RelationCandidate,
    SkillRegistry,
)


def build_llm_context(
    registry: SkillRegistry,
    candidates: List[RelationCandidate],
    *,
    reverse_skill_order: bool = False,
) -> Dict[str, Any]:
    skill_ids = _ordered_skill_ids_for_candidates(
        candidates,
        reverse_skill_order=reverse_skill_order,
    )
    skills = [registry.skills[skill_id] for skill_id in skill_ids]
    candidate_payload = [candidate.to_dict() for candidate in candidates]
    return {
        "allowed_relation_types": sorted(ALLOWED_RELATION_TYPES),
        "skills": [_skill_context(skill) for skill in skills],
        "candidates": candidate_payload,
        "input_sha256": hashlib.sha256(
            json.dumps(candidate_payload, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest(),
    }


def _ordered_skill_ids_for_candidates(
    candidates: List[RelationCandidate],
    *,
    reverse_skill_order: bool,
) -> List[str]:
    ordered: List[str] = []
    for candidate in candidates:
        pair = (
            [candidate.target_id, candidate.source_id]
            if reverse_skill_order
            else [candidate.source_id, candidate.target_id]
        )
        for skill_id in pair:
            if skill_id not in ordered:
                ordered.append(skill_id)
    return ordered


def _skill_context(skill: SkillFingerprint) -> Dict[str, Any]:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "inputs": [item.to_dict() for item in skill.inputs],
        "outputs": [item.to_dict() for item in skill.outputs],
    }


SYSTEM_PROMPT = """You validate Skill relation candidates for a Skill graph.

Return only a valid JSON object. Do not include markdown fences, explanations,
analysis, or reasoning text.

Input contains:
- skills: normalized Skill fingerprints.
- candidates: deterministic relation candidates.
- allowed_relation_types.

For each candidate pair, decide whether the suggested can_feed relation is
valid. Do not invent new skills, relation types, or candidate pairs.
If a candidate direction is wrong, omit it or return it with low confidence
and a reason.

Return:
{
  "matches": [
    {
      "candidate_id": "skill_a<->skill_b",
      "source_id": "directed source skill id",
      "target_id": "directed target skill id",
      "relation_type": "can_feed",
      "confidence": 0.0-1.0,
      "method": "llm_ontology_match",
      "reasons": ["short evidence-based reason"],
      "supporting_fields": {
        "port_mappings": [
          {"source_output": "output_name", "target_input": "input_name"}
        ],
        "source_outputs": ["..."],
        "target_inputs": ["..."]
      }
    }
  ]
}

Relation meanings:
- can_feed: source output can satisfy target input.
Choose port_mappings only from the candidate evidence. Prefer content inputs
such as body/query/text/sources over control inputs such as command/format.
"""
