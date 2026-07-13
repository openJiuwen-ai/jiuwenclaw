"""LLM-backed resolver for unseen I/O vocabulary terms."""

from __future__ import annotations

import json
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from jiuwenswarm.symphony.fingerprint.normalize.io_name_vocab import (
    IONameCandidate,
    IONameResolution,
    IONameVocabulary,
)
from jiuwenswarm.symphony.llm import (
    LLMConfig,
    create_llm_client,
    llm_usage_context,
)

_ACTION_ALIAS_EXISTING = "alias_existing"
_ACTION_CREATE_NEW = "create_new"
_ACTION_MERGE_EXISTING = "merge_existing"
_ACTION_EXCLUDE_FROM_VOCAB = "exclude_from_vocab"
_ALLOWED_ACTIONS = frozenset(
    {
        _ACTION_ALIAS_EXISTING,
        _ACTION_CREATE_NEW,
        _ACTION_MERGE_EXISTING,
        _ACTION_EXCLUDE_FROM_VOCAB,
    }
)


class LLMIONameResolver:
    """LLM-backed resolver for unseen io_name_vocab terms."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        batch_size: int = 1,
        progress: Optional[Callable[[str, IONameCandidate, Optional[IONameResolution]], None]] = None,
    ) -> None:
        self.config = config
        self.client = create_llm_client(config)
        self.batch_size = max(1, int(batch_size))
        self.progress = progress
        self._cache: Dict[str, IONameResolution] = {}
        self._cache_lock = RLock()

    async def resolve_async(
        self,
        candidates_by_skill: List[List[IONameCandidate]],
        vocabulary: IONameVocabulary,
    ) -> Dict[str, IONameResolution]:
        candidate_groups: List[Tuple[str, List[IONameCandidate]]] = []
        resolutions: Dict[str, IONameResolution] = {}
        seen: Set[str] = set()
        for index, candidates in enumerate(candidates_by_skill):
            unique_candidates: List[IONameCandidate] = []
            for candidate in candidates:
                if not candidate.token or candidate.token in seen:
                    continue
                seen.add(candidate.token)
                with self._cache_lock:
                    cached = self._cache.get(candidate.token)
                if cached is not None:
                    resolutions[candidate.token] = cached
                    continue
                unique_candidates.append(candidate)
            if unique_candidates:
                candidate_groups.append(
                    (_candidate_group_ref(index, unique_candidates), unique_candidates)
                )

        if not candidate_groups:
            return resolutions

        context = {
            "skills": [
                {
                    "skill_ref": skill_ref,
                    "candidates": [
                        _candidate_prompt_payload(candidate)
                        for candidate in candidates
                    ],
                }
                for skill_ref, candidates in candidate_groups
            ],
            "vocabulary": _compact_vocabulary_context(vocabulary),
            "allowed_actions": sorted(_ALLOWED_ACTIONS),
        }
        for _, candidates in candidate_groups:
            for candidate in candidates:
                self._emit_progress("start", candidate, None)
        with llm_usage_context("fingerprint_extraction", "io_name_resolution_prompt_batch"):
            content = await self.client.complete_json_async(
                system_prompt=_IO_NAME_PROMPT_BATCH_RESOLVER_PROMPT,
                user_content=json.dumps(context, ensure_ascii=False, indent=2),
                timeout=200,
                error_context="IO name vocabulary prompt batch LLM",
                request_overrides={
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
            )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "IO name vocabulary prompt batch LLM response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc

        payload_skills = payload.get("skills", [])
        if not isinstance(payload_skills, list):
            raise RuntimeError(
                "IO name vocabulary prompt batch LLM response must contain skills array."
            )

        candidates_by_ref = {
            skill_ref: {candidate.token: candidate for candidate in candidates}
            for skill_ref, candidates in candidate_groups
        }
        remaining_tokens = {
            skill_ref: set(candidates)
            for skill_ref, candidates in candidates_by_ref.items()
        }
        for skill_payload in payload_skills:
            if not isinstance(skill_payload, dict):
                continue
            skill_ref = str(skill_payload.get("skill_ref") or "").strip()
            if skill_ref not in candidates_by_ref:
                continue
            payload_items = skill_payload.get("resolutions", [])
            if not isinstance(payload_items, list):
                continue
            for item in payload_items:
                if not isinstance(item, dict):
                    continue
                token = str(item.get("token") or "").strip()
                if token not in remaining_tokens[skill_ref]:
                    continue
                resolution = _resolution_from_payload(item, vocabulary)
                resolutions[token] = resolution
                with self._cache_lock:
                    self._cache[token] = resolution
                remaining_tokens[skill_ref].remove(token)
                self._emit_progress(
                    "done",
                    candidates_by_ref[skill_ref][token],
                    resolution,
                )

        missing = {
            skill_ref: sorted(tokens)
            for skill_ref, tokens in remaining_tokens.items()
            if tokens
        }
        if missing:
            raise RuntimeError(
                "IO name vocabulary prompt batch LLM response omitted resolutions: "
                + json.dumps(missing, ensure_ascii=False, sort_keys=True)
            )
        return resolutions

    def _emit_progress(
        self,
        stage: str,
        candidate: IONameCandidate,
        resolution: Optional[IONameResolution],
    ) -> None:
        if self.progress is not None:
            self.progress(stage, candidate, resolution)


def _candidate_group_ref(index: int, candidates: List[IONameCandidate]) -> str:
    if not candidates:
        return str(index)
    first = candidates[0]
    return f"{index}:{first.token}"


def _candidate_prompt_payload(candidate: IONameCandidate) -> Dict[str, str]:
    return {
        "raw_value": candidate.raw_value,
        "token": candidate.token,
        "description": candidate.description,
        "direction": candidate.direction,
        "type": candidate.data_type,
    }


def _compact_vocabulary_context(vocabulary: IONameVocabulary) -> Dict[str, Any]:
    context = vocabulary.resolver_context()
    terms = []
    for item in context.get("terms", []):
        if not isinstance(item, dict):
            continue
        terms.append(
            {
                "name": str(item.get("name") or ""),
                "definition": str(item.get("definition") or ""),
                "aliases": [
                    str(alias)
                    for alias in item.get("aliases", [])
                    if str(alias).strip()
                ],
            }
        )
    return {
        "version": context.get("version"),
        "max_vocab_size": context.get("max_vocab_size"),
        "is_full": context.get("is_full"),
        "terms": terms,
    }


def _resolution_from_payload(
    payload: Dict[str, Any],
    vocabulary: IONameVocabulary,
) -> IONameResolution:
    action = str(payload.get("action") or _ACTION_MERGE_EXISTING)
    if action not in _ALLOWED_ACTIONS:
        action = _ACTION_MERGE_EXISTING

    target = (
        payload.get("target")
        or payload.get("normalized_value")
        or payload.get("normalized_name")
    )
    normalized_value = str(target).strip() if target is not None else None
    definition = str(payload.get("definition") or "").strip()
    if action == _ACTION_EXCLUDE_FROM_VOCAB:
        normalized_value = None
        definition = ""
    elif action == _ACTION_CREATE_NEW and vocabulary.is_full():
        action = _ACTION_MERGE_EXISTING
        normalized_value = vocabulary.closest_term(normalized_value or "")

    return IONameResolution(
        action=action,
        normalized_value=normalized_value,
        confidence=_coerce_confidence(payload.get("confidence")),
        reason=str(payload.get("reason") or ""),
        forced_merge=bool(payload.get("forced_merge")) or action == _ACTION_MERGE_EXISTING,
        definition=definition,
    )


def _coerce_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


_IO_NAME_PROMPT_BATCH_RESOLVER_PROMPT = """Resolve Skill input/output names against io_name_vocab.

Return only a valid JSON object. Do not include markdown fences, explanations,
analysis, or reasoning text.

Required JSON shape:
{
  "skills": [
    {
      "skill_ref": "the exact skill_ref from the input",
      "resolutions": [
        {
          "token": "candidate_token",
          "action": "alias_existing|create_new|merge_existing|exclude_from_vocab",
          "target": "existing_or_new_vocab_term_or_null",
          "confidence": 0.0,
          "reason": "short explanation",
          "definition": "semantic definition or enrichment suggestion"
        }
      ]
    }
  ]
}

Return exactly one resolution for every input candidate token. Preserve
skill_ref exactly. skill_ref is only an opaque correlation id.

Allowed actions:
- alias_existing: candidate is synonymous with an existing vocabulary term.
- create_new: candidate is a genuinely new runtime semantic role.
- merge_existing: candidate should be merged into the closest existing term.
- exclude_from_vocab: candidate is bookkeeping-only telemetry, analytics,
  tracking, or original-copy data.

The type field is only the data format or carrier, not the semantic role.
Existing terms have definitions in the vocabulary context - use them to judge
semantic equivalence.

Prefer recall for graph linking. Singular/plural forms, input/output variants,
and compound variants such as text/content/body or path/output_path often share
one semantic role. Directional qualifiers should block merging only when feeding
would be clearly wrong, such as source_language vs target_language or api_key vs query.
"""
