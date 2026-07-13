"""One-shot fast Symphony score planner."""

from __future__ import annotations

import json
from typing import Any, Sequence

from jiuwenswarm.symphony.llm import LLMConfig, create_llm_client, llm_usage_context
from jiuwenswarm.symphony.orchestration.artifacts import ScoreArtifacts
from jiuwenswarm.symphony.orchestration.planning.plan_builder import edge_plan_item
from jiuwenswarm.symphony.orchestration.planning.utils import skill_id

FAST_PLANNER_MAX_SKILLS = 40
FAST_PLANNER_MAX_EDGES = 80

FAST_PLANNER_SYSTEM_PROMPT = """You are Symphony's fast Skill planner.
Return strict JSON only.

You receive:
- The user's query.
- Candidate Skills with id, name, and description only.
- Candidate can_feed relationships between those Skills.

Task:
- Select the best existing Skill execution path for the query.
- Use only provided skill IDs.
- Use only provided can_feed edges.
- Do not invent Skills, inputs, outputs, or edge relationships.
- Prefer the shortest path that satisfies the user's intent.
- If required information is missing, set status to "needs_input" and list it.
- If no useful plan exists from the candidates, set status to "no_plan".

Schema:
{
  "title": "short plan title",
  "status": "ready | needs_input | no_plan",
  "reason": "why this plan is best",
  "steps": [
    {"skill_id": "skill-a", "reason": "why this step is used"}
  ],
  "can_feed_edges": [
    {"source_id": "skill-a", "target_id": "skill-b"}
  ],
  "missing_inputs": [
    {"skill_id": "skill-a", "name": "input name", "type": "unknown", "reason": "why it is needed"}
  ]
}
"""


class FastOneShotPlanner:
    """Ask the LLM for one validated plan from a bounded score subgraph."""

    def __init__(
        self,
        artifacts: ScoreArtifacts,
        *,
        llm_config: LLMConfig | None,
        llm_client: Any | None,
        min_edge_confidence: float,
        top_k: int,
        candidate_skill_ids: Sequence[str] | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.llm_config = llm_config
        self.llm_client = llm_client
        self.min_edge_confidence = min_edge_confidence
        self.top_k = max(1, int(top_k))
        self.candidate_skill_ids = self._normalize_candidate_skill_ids(
            candidate_skill_ids,
            known_skill_ids=set(artifacts.skill_by_id),
        )

    async def plan(self, query: str) -> dict[str, Any]:
        client = self._client()
        subgraph = self._candidate_subgraph()
        prompt_payload = {
            "query": query,
            "top_k": self.top_k,
            "min_edge_confidence": self.min_edge_confidence,
            "skills": subgraph["skills"],
            "can_feed_edges": subgraph["edges"],
            "planning_instructions": [
                "Return the single best plan as strict JSON.",
                "Use only skill IDs from skills.",
                "Use only can_feed_edges from can_feed_edges.",
                "Do not rely on skill inputs or outputs; they are filled by Symphony after validation.",
            ],
        }
        with llm_usage_context("orchestration", "one_shot_fast_planning"):
            raw = await client.complete_json_async(
                system_prompt=FAST_PLANNER_SYSTEM_PROMPT,
                user_content=json.dumps(prompt_payload, ensure_ascii=False),
                error_context="Symphony one-shot fast planning",
            )

        base = {
            "query": query,
            "score_dir": str(self.artifacts.score_dir),
            "planning_mode": "one_shot_fast",
            "llm_call_count": 1,
            "candidate_skill_count": len(subgraph["skills"]),
            "candidate_edge_count": len(subgraph["edges"]),
        }
        try:
            selection = json.loads(raw)
        except json.JSONDecodeError as exc:
            return self._failure(base, f"Invalid fast planner JSON: {raw[:500]}", exc)
        if not isinstance(selection, dict):
            return self._failure(base, "Fast planner returned a non-object JSON payload.")

        materialized = self._materialize_selection(
            selection,
            candidate_skill_ids=set(subgraph["skill_ids"]),
            candidate_edges=subgraph["edge_by_key"],
        )
        if not materialized["valid"]:
            return self._failure(base, materialized["detail"], validation=materialized)

        plan = materialized["plan"]
        return {
            **base,
            "plans": [plan] if plan.get("steps") else [],
            "recommended_plans": [plan] if plan.get("steps") else [],
            "ranking_mode": "one_shot_fast",
            "decision": {
                "mode": "one_shot_fast",
                "strategy": "single_llm_selection",
                "validated_count": 1 if plan.get("steps") else 0,
                "candidate_count": len(subgraph["skills"]),
            },
            "validation": materialized,
            "status": plan.get("status", "no_plan") if plan else "no_plan",
            "reason": plan.get("reason", "") if plan else "",
        }

    def _client(self) -> Any:
        if self.llm_client is not None:
            return self.llm_client
        if self.llm_config is None:
            raise ValueError("fast Symphony planning requires llm_config or llm_client.")
        return create_llm_client(self.llm_config)

    def _candidate_subgraph(self) -> dict[str, Any]:
        if self.candidate_skill_ids:
            return self._retrieval_candidate_subgraph()
        return self._default_candidate_subgraph()

    def _default_candidate_subgraph(self) -> dict[str, Any]:
        skill_by_id = self.artifacts.skill_by_id
        sorted_edges = self._sorted_eligible_edges()
        selected: set[str] = set()
        for edge in sorted_edges:
            for key in ("source", "target"):
                edge_skill_id = skill_id(edge.get(key))
                if (
                    edge_skill_id in skill_by_id
                    and len(selected) < FAST_PLANNER_MAX_SKILLS
                ):
                    selected.add(edge_skill_id)
            if len(selected) >= FAST_PLANNER_MAX_SKILLS:
                break

        candidate_edges = []
        for edge in sorted_edges:
            if (
                skill_id(edge.get("source")) in selected
                and skill_id(edge.get("target")) in selected
            ):
                candidate_edges.append(edge)
        candidate_edges.sort(
            key=lambda item: (
                -float(item.get("confidence") or 0.0),
                skill_id(item.get("source")),
                skill_id(item.get("target")),
            )
        )
        candidate_edges = candidate_edges[:FAST_PLANNER_MAX_EDGES]

        for edge in candidate_edges:
            for key in ("source", "target"):
                edge_skill_id = skill_id(edge.get(key))
                if (
                    edge_skill_id in skill_by_id
                    and len(selected) < FAST_PLANNER_MAX_SKILLS
                ):
                    selected.add(edge_skill_id)

        if not selected:
            for skill in self.artifacts.skills[:FAST_PLANNER_MAX_SKILLS]:
                current_skill_id = str(skill.get("id") or "")
                if current_skill_id.strip():
                    selected.add(current_skill_id)

        filtered_candidate_edges = []
        for edge in candidate_edges:
            if (
                skill_id(edge.get("source")) in selected
                and skill_id(edge.get("target")) in selected
            ):
                filtered_candidate_edges.append(edge)
        candidate_edges = filtered_candidate_edges
        return self._subgraph_payload(selected, candidate_edges)

    def _retrieval_candidate_subgraph(self) -> dict[str, Any]:
        skill_by_id = self.artifacts.skill_by_id
        sorted_edges = self._sorted_eligible_edges()
        selected: set[str] = set()

        def add_skill(current_skill_id: str) -> None:
            if (
                current_skill_id in skill_by_id
                and current_skill_id not in selected
                and len(selected) < FAST_PLANNER_MAX_SKILLS
            ):
                selected.add(current_skill_id)

        for current_skill_id in self.candidate_skill_ids:
            add_skill(current_skill_id)
        if not selected:
            return self._default_candidate_subgraph()

        seed_ids = set(selected)
        for edge in sorted_edges:
            source_id = skill_id(edge.get("source"))
            target_id = skill_id(edge.get("target"))
            if source_id not in seed_ids and target_id not in seed_ids:
                continue
            add_skill(source_id)
            add_skill(target_id)
            if len(selected) >= FAST_PLANNER_MAX_SKILLS:
                break

        candidate_edges = []
        for edge in sorted_edges:
            if (
                skill_id(edge.get("source")) in selected
                and skill_id(edge.get("target")) in selected
            ):
                candidate_edges.append(edge)
            if len(candidate_edges) >= FAST_PLANNER_MAX_EDGES:
                break
        return self._subgraph_payload(selected, candidate_edges)

    def _sorted_eligible_edges(self) -> list[dict[str, Any]]:
        filtered_edges = []
        for edge in self.artifacts.graph.get("edges", []):
            edge_confidence = float(edge.get("confidence") or 0.0)
            if edge.get("type") == "can_feed" and edge_confidence >= self.min_edge_confidence:
                filtered_edges.append(edge)
        return sorted(
            filtered_edges,
            key=lambda item: (
                -float(item.get("confidence") or 0.0),
                str(item.get("source") or ""),
                str(item.get("target") or ""),
            ),
        )

    def _subgraph_payload(
        self,
        selected: set[str],
        candidate_edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        skill_by_id = self.artifacts.skill_by_id
        skill_payloads = [
            self._skill_payload(skill_by_id[current_skill_id])
            for current_skill_id in sorted(selected)
            if current_skill_id in skill_by_id
        ]
        edge_payloads = [self._edge_payload(edge) for edge in candidate_edges]
        return {
            "skills": skill_payloads,
            "edges": edge_payloads,
            "skill_ids": [item["id"] for item in skill_payloads],
            "edge_by_key": {
                (skill_id(edge.get("source")), skill_id(edge.get("target"))): edge
                for edge in candidate_edges
            },
        }

    def _materialize_selection(
        self,
        selection: dict[str, Any],
        *,
        candidate_skill_ids: set[str],
        candidate_edges: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        status = str(selection.get("status") or "ready").strip().lower()
        if status not in {"ready", "needs_input", "no_plan"}:
            status = "ready"
        raw_steps = selection.get("steps") or selection.get("skill_order") or []
        step_ids = self._normalize_step_ids(raw_steps)
        if status == "no_plan" or not step_ids:
            return {
                "valid": True,
                "detail": "",
                "plan": {
                    "title": str(
                        selection.get("title") or "No Symphony fast plan"
                    ).strip(),
                    "status": "no_plan",
                    "steps": [],
                    "stages": [],
                    "produced_artifacts": [],
                    "missing_inputs": [],
                    "can_feed_edges": [],
                    "reason": str(selection.get("reason") or "").strip(),
                    "plan_classification": "invalid",
                    "connectivity_trace": [],
                },
            }

        unknown_ids = [
            current_skill_id
            for current_skill_id in step_ids
            if current_skill_id not in candidate_skill_ids
        ]
        if unknown_ids:
            return {
                "valid": False,
                "detail": f"Fast planner selected unknown skill IDs: {unknown_ids}",
            }
        skill_by_id = self.artifacts.skill_by_id
        missing_artifact_ids = [
            current_skill_id
            for current_skill_id in step_ids
            if current_skill_id not in skill_by_id
        ]
        if missing_artifact_ids:
            return {
                "valid": False,
                "detail": (
                    "Fast planner selected missing artifact skill IDs: "
                    f"{missing_artifact_ids}"
                ),
            }

        selected_edges_result = self._normalize_selected_edges(
            selection.get("can_feed_edges") or []
        )
        selected_edges = selected_edges_result["edges"]
        if selected_edges_result["invalid"]:
            return {
                "valid": False,
                "detail": (
                    "Fast planner returned malformed can_feed edges: "
                    f"{selected_edges_result['invalid']}"
                ),
            }
        if len(step_ids) > 1 and not selected_edges:
            selected_edges = [
                (step_ids[index], step_ids[index + 1])
                for index in range(len(step_ids) - 1)
            ]

        invalid_edges = [edge for edge in selected_edges if edge not in candidate_edges]
        if invalid_edges:
            return {
                "valid": False,
                "detail": f"Fast planner selected illegal can_feed edges: {invalid_edges}",
            }
        order = {current_skill_id: index for index, current_skill_id in enumerate(step_ids)}
        backward_edges = [
            (source, target)
            for source, target in selected_edges
            if order.get(source, -1) >= order.get(target, -1)
        ]
        if backward_edges:
            return {
                "valid": False,
                "detail": (
                    "Fast planner selected edges that violate step order: "
                    f"{backward_edges}"
                ),
            }

        missing_inputs = self._normalize_missing_inputs(
            selection.get("missing_inputs") or [],
            set(step_ids),
        )
        missing_by_skill: dict[str, list[dict[str, Any]]] = {}
        for item in missing_inputs:
            missing_by_skill.setdefault(str(item.get("skill_id") or ""), []).append(item)

        steps = []
        produced_artifacts = []
        for index, current_skill_id in enumerate(step_ids, start=1):
            skill = skill_by_id[current_skill_id]
            outputs = list(skill.get("outputs") or [])
            produced_artifacts.extend(
                {
                    "name": item.get("name"),
                    "type": item.get("type") or "unknown",
                    "source": "skill_output",
                }
                for item in outputs
                if item.get("name")
            )
            steps.append(
                {
                    "step": index,
                    "skill_id": current_skill_id,
                    "name": str(skill.get("name") or current_skill_id),
                    "inputs": list(skill.get("inputs") or []),
                    "outputs": outputs,
                    "missing_inputs": missing_by_skill.get(current_skill_id, []),
                    "filled_inputs": [],
                    "reason": self._step_reason(
                        selection.get("steps") or [],
                        current_skill_id,
                    ),
                }
            )

        edge_items = [edge_plan_item(candidate_edges[edge]) for edge in selected_edges]
        if missing_inputs:
            status = "needs_input"
        return {
            "valid": True,
            "detail": "",
            "plan": {
                "title": str(selection.get("title") or "Symphony fast plan").strip(),
                "status": status,
                "steps": steps,
                "stages": [
                    {"stage": index, "skills": [step]}
                    for index, step in enumerate(steps, start=1)
                ],
                "produced_artifacts": produced_artifacts,
                "missing_inputs": missing_inputs,
                "can_feed_edges": edge_items,
                "reason": str(selection.get("reason") or "").strip(),
                "plan_classification": (
                    "executable"
                    if status == "ready"
                    else "structurally_valid_but_incomplete"
                ),
                "connectivity_trace": ["can_feed"] if edge_items else [],
                "source": "one_shot_fast",
            },
        }

    @staticmethod
    def _failure(
        base: dict[str, Any],
        detail: str,
        exc: Exception | None = None,
        *,
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            **base,
            "success": False,
            "detail": detail,
            "plans": [],
            "recommended_plans": [],
            "ranking_mode": "one_shot_fast_failed",
            "validation": validation or {"valid": False, "detail": detail},
        }
        if exc is not None:
            payload["error"] = str(exc)
        return payload

    @staticmethod
    def _skill_payload(skill: dict[str, Any]) -> dict[str, Any]:
        current_skill_id = str(skill.get("id") or "")
        return {
            "id": current_skill_id,
            "name": str(skill.get("name") or current_skill_id),
            "description": str(skill.get("description") or "")[:800],
        }

    @staticmethod
    def _edge_payload(edge: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": skill_id(edge.get("source")),
            "target_id": skill_id(edge.get("target")),
        }

    @staticmethod
    def _normalize_step_ids(raw_steps: Any) -> list[str]:
        if not isinstance(raw_steps, list):
            return []
        output = []
        for item in raw_steps:
            current_skill_id = skill_id(
                item.get("skill_id") if isinstance(item, dict) else item
            ).strip()
            if current_skill_id and current_skill_id not in output:
                output.append(current_skill_id)
        return output

    @staticmethod
    def _normalize_selected_edges(raw_edges: Any) -> dict[str, Any]:
        if not isinstance(raw_edges, list):
            return {"edges": [], "invalid": [raw_edges]}
        edges = []
        invalid = []
        for item in raw_edges:
            if not isinstance(item, dict):
                invalid.append(item)
                continue
            source = skill_id(item.get("source_id") or item.get("source")).strip()
            target = skill_id(item.get("target_id") or item.get("target")).strip()
            if not source or not target:
                invalid.append(item)
                continue
            edge = (source, target)
            if edge not in edges:
                edges.append(edge)
        return {"edges": edges, "invalid": invalid}

    @staticmethod
    def _normalize_missing_inputs(
        raw_items: Any,
        step_ids: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        output = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            current_skill_id = skill_id(item.get("skill_id")).strip()
            if current_skill_id and current_skill_id not in step_ids:
                continue
            normalized = {
                "skill_id": current_skill_id,
                "name": str(item.get("name") or "unknown"),
                "type": str(item.get("type") or "unknown"),
            }
            reason = str(item.get("reason") or "").strip()
            if reason:
                normalized["reason"] = reason
            output.append(normalized)
        return output

    @staticmethod
    def _step_reason(raw_steps: Any, target_skill_id: str) -> str:
        if not isinstance(raw_steps, list):
            return ""
        for item in raw_steps:
            if (
                isinstance(item, dict)
                and skill_id(item.get("skill_id")) == target_skill_id
            ):
                return str(item.get("reason") or "").strip()
        return ""

    @staticmethod
    def _normalize_candidate_skill_ids(
        values: Sequence[str] | None,
        *,
        known_skill_ids: set[str],
    ) -> tuple[str, ...] | None:
        if values is None:
            return None
        output = []
        seen = set()
        for value in values:
            current_skill_id = str(value or "").strip()
            if (
                not current_skill_id
                or current_skill_id in seen
                or current_skill_id not in known_skill_ids
            ):
                continue
            seen.add(current_skill_id)
            output.append(current_skill_id)
        return tuple(output) if output else None
