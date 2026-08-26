"""Agent-facing Symphony tools."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.agents.harness.common.tool_progress_context import (
    current_tool_progress,
)
from jiuwenswarm.symphony.config import load_symphony_config
from jiuwenswarm.symphony.service import (
    SwarmSymphonyService,
    get_swarm_symphony_service,
)

logger = logging.getLogger(__name__)

_DEFAULT_SERVICE_TIMEOUT_S = 1800.0
_COMPOSE_SERVICE_TIMEOUT_S = 3300.0


def _coerce_compose_inputs(inputs: Any) -> Any:
    if not isinstance(inputs, dict):
        return inputs
    encoded_candidate_ids = inputs.get("candidate_skill_ids")
    if not isinstance(encoded_candidate_ids, str):
        return inputs
    try:
        decoded_candidate_ids = json.loads(encoded_candidate_ids)
    except json.JSONDecodeError:
        return inputs
    if not isinstance(decoded_candidate_ids, list):
        return inputs

    normalized_inputs = dict(inputs)
    normalized_inputs["candidate_skill_ids"] = decoded_candidate_ids
    return normalized_inputs


class _ComposeGraphLocalFunction(LocalFunction):
    async def invoke(self, inputs: Any, **kwargs: Any) -> Any:
        return await super().invoke(_coerce_compose_inputs(inputs), **kwargs)


class SymphonyToolkit:
    """Expose the process-local Symphony service as model-callable tools."""

    def __init__(self, service: SwarmSymphonyService | None = None) -> None:
        self._service = service

    @staticmethod
    def _resolve_timeout_s(default_s: float = 1800.0) -> float:
        return default_s

    async def _call_service(
        self,
        operation: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        logger.info(
            "[SymphonyToolkit] calling service: operation=%s",
            operation,
        )
        default_timeout_s = (
            _COMPOSE_SERVICE_TIMEOUT_S
            if operation == "plan"
            else _DEFAULT_SERVICE_TIMEOUT_S
        )
        timeout_s = self._resolve_timeout_s(default_timeout_s)
        try:
            service = self._service or get_swarm_symphony_service()
            handler = getattr(service, operation)
            payload = await asyncio.wait_for(
                handler(*args, **kwargs), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            if operation not in {"plan", "refresh_graph"}:
                return {
                    "success": False,
                    "detail": f"symphony.{operation}: timeout after {timeout_s}s",
                }
            return {
                "success": False,
                "reason": "graph_build_timeout",
                "timed_out": True,
                "retryable": False,
                "operation": operation,
                "timeout_s": timeout_s,
                "detail": f"symphony.{operation}: timeout after {timeout_s}s",
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Symphony service failed: %s", operation)
            return {"success": False, "detail": f"symphony.{operation}: {exc}"}

        return (
            payload
            if isinstance(payload, dict)
            else {"success": True, "result": payload}
        )

    @staticmethod
    def _disabled_payload(method: str) -> dict[str, Any]:
        return {
            "success": False,
            "disabled": True,
            "method": method,
            "detail": "Symphony is disabled by config: symphony.enabled=false",
        }

    async def graph_status(self) -> dict[str, Any]:
        if not self.is_enabled():
            return self._disabled_payload("symphony_read_graph")
        return await self._call_service("graph_status")

    async def refresh_graph(self) -> dict[str, Any]:
        if not self.is_enabled():
            return self._disabled_payload("symphony_refresh_graph")
        return await self._call_service(
            "refresh_graph",
            progress=current_tool_progress(),
        )

    @classmethod
    def _compact_plan_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        planning_payload = cls._planning_payload(payload)
        compact: dict[str, Any] = {
            "success": payload.get("success", True),
        }
        for key in (
            "disabled",
            "content",
            "direct_display",
            "continue_after_display",
            "followup_action",
            "timed_out",
            "retryable",
            "operation",
            "timeout_s",
        ):
            if key in payload:
                compact[key] = payload[key]

        for key in ("detail", "error"):
            value = payload.get(key)
            if value not in (None, ""):
                compact[key] = value
        if not bool(compact["success"]):
            reason = payload.get("reason")
            if reason not in (None, ""):
                compact["reason"] = reason

        graph_status = payload.get("graph_status")
        graph_build = payload.get("graph_build")
        if not bool(compact["success"]) and isinstance(graph_status, dict):
            compact["graph_status"] = cls._compact_graph_status(graph_status)
        if isinstance(graph_build, dict) and (
            not bool(compact["success"]) or graph_build.get("rebuilt") is True
        ):
            compact["graph_build"] = cls._compact_graph_build(graph_build)

        beam_search = planning_payload.get("beam_search")
        if isinstance(beam_search, dict):
            compact["beam_search"] = cls._compact_beam_search(beam_search)

        for key in ("plan_id", "dynamic_graph_enabled"):
            value = planning_payload.get(key)
            if value in (None, ""):
                value = payload.get(key)
            if value not in (None, ""):
                compact[key] = value

        plan = cls._compact_plan(cls._primary_plan(planning_payload))
        if plan:
            compact["plan"] = plan

        return compact

    @staticmethod
    def _compact_graph_status(status: dict[str, Any]) -> dict[str, Any]:
        compact = _copy_compact_fields(
            status,
            (
                "success",
                "exists",
                "stale",
                "skill_count",
                "changed_count",
                "added_count",
                "removed_count",
                "resume_available",
                "detail",
                "reason",
            ),
        )
        return compact

    @classmethod
    def _compact_graph_build(cls, update: dict[str, Any]) -> dict[str, Any]:
        compact = _copy_compact_fields(
            update,
            (
                "rebuilt",
                "success",
                "skill_count",
                "reused_count",
                "extracted_count",
                "removed_count",
                "edge_count",
                "diagnostics_count",
                "relation_reused_count",
                "relation_resolved_count",
                "version",
                "graph_created_at",
                "llm_total_tokens",
                "reason",
                "detail",
            ),
        )
        compact.setdefault("rebuilt", True)
        if compact["rebuilt"] is True:
            progress = cls._compact_build_progress(update.get("build_progress"))
            if progress:
                compact["build_progress"] = progress
            total_tokens = cls._llm_total_tokens(update.get("llm_token_usage"))
            if total_tokens > 0 and update.get("success") is not False:
                compact["llm_total_tokens"] = total_tokens
        return compact

    @staticmethod
    def _llm_total_tokens(token_usage: Any) -> int:
        if not isinstance(token_usage, dict):
            return 0
        total = token_usage.get("total")
        if not isinstance(total, dict):
            return 0
        try:
            return max(0, int(total.get("total_tokens") or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _compact_build_progress(progress: Any) -> dict[str, Any]:
        if not isinstance(progress, dict):
            return {}
        return _copy_compact_fields(
            progress,
            ("stage", "label", "percent", "status", "current", "total"),
        )

    @classmethod
    def _compact_beam_search(cls, payload: dict[str, Any]) -> dict[str, Any]:
        compact = _copy_compact_fields(
            payload,
            (
                "language",
                "round_index",
            ),
        )
        graph = payload.get("graph")
        if isinstance(graph, dict):
            compact["graph"] = cls._compact_beam_graph(graph)
        return compact

    @classmethod
    def _compact_beam_graph(cls, graph: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        nodes = graph.get("nodes")
        if isinstance(nodes, list):
            compact["nodes"] = [
                cls._compact_beam_node(node) for node in nodes if isinstance(node, dict)
            ]
        edges = graph.get("edges")
        if isinstance(edges, list):
            compact["edges"] = [
                cls._compact_beam_edge(edge) for edge in edges if isinstance(edge, dict)
            ]
        return compact

    @staticmethod
    def _compact_beam_node(node: dict[str, Any]) -> dict[str, Any]:
        return _copy_compact_fields(
            node,
            ("id", "label", "status", "seed"),
        )

    @staticmethod
    def _compact_beam_edge(edge: dict[str, Any]) -> dict[str, Any]:
        return _copy_compact_fields(
            edge,
            ("source", "target", "status"),
        )

    @classmethod
    def _compact_plan(cls, plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, dict) or not plan:
            return {}
        compact = _copy_compact_fields(plan, ("title", "status", "reason"))
        steps = plan.get("steps")
        if isinstance(steps, list):
            compact["steps"] = [
                cls._compact_plan_step(step, index)
                for index, step in enumerate(steps, start=1)
                if isinstance(step, dict)
            ]
        edges = plan.get("can_feed_edges")
        if isinstance(edges, list):
            compact["can_feed_edges"] = [
                cls._compact_can_feed_edge(edge)
                for edge in edges
                if isinstance(edge, dict)
            ]
        missing_inputs = plan.get("missing_inputs")
        if isinstance(missing_inputs, list):
            compact["missing_inputs"] = missing_inputs
        return compact

    @staticmethod
    def _compact_plan_step(step: dict[str, Any], index: int) -> dict[str, Any]:
        compact = _copy_compact_fields(step, ("step", "skill_id", "reason"))
        compact.setdefault("step", index)
        name = step.get("name") or step.get("skill_name")
        if name not in (None, ""):
            compact["name"] = name
        return compact

    @staticmethod
    def _compact_can_feed_edge(edge: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        source = edge.get("source_id") or edge.get("source")
        target = edge.get("target_id") or edge.get("target")
        if source not in (None, ""):
            compact["source_id"] = source
        if target not in (None, ""):
            compact["target_id"] = target
        method = edge.get("method")
        if method not in (None, ""):
            compact["method"] = method
        reason = edge.get("reason")
        if reason not in (None, ""):
            compact["reason"] = reason
        return compact

    @staticmethod
    def _primary_plan(payload: dict[str, Any]) -> dict[str, Any]:
        for key in ("recommended_plans", "plans"):
            plans = payload.get(key)
            if not isinstance(plans, list):
                continue
            for plan in plans:
                if isinstance(plan, dict):
                    return plan
        return {}

    @classmethod
    def _planning_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        return result if isinstance(result, dict) else payload

    @classmethod
    def _needs_external_skill_discovery(cls, payload: dict[str, Any]) -> bool:
        planning_payload = cls._planning_payload(payload)
        plan = cls._primary_plan(planning_payload)
        status = (
            str(
                plan.get("status")
                or planning_payload.get("status")
                or payload.get("status")
                or ""
            )
            .strip()
            .lower()
        )
        missing_inputs = (
            plan.get("missing_inputs") or planning_payload.get("missing_inputs") or []
        )
        if status == "needs_input" or missing_inputs:
            return False
        if status == "no_plan":
            return True

        steps = plan.get("steps") if isinstance(plan, dict) else []
        execution_graph = planning_payload.get("execution_graph")
        if not isinstance(execution_graph, dict):
            execution_graph = payload.get("execution_graph")
        graph_nodes = (
            execution_graph.get("nodes") if isinstance(execution_graph, dict) else []
        )
        return not steps and not graph_nodes

    @classmethod
    def _attach_followup_control(cls, payload: dict[str, Any]) -> None:
        if payload.get("reason") == "graph_build_timeout":
            payload["continue_after_display"] = False
            if payload.get("followup_action") == "external_skill_discovery":
                payload.pop("followup_action")
            return
        if cls._needs_external_skill_discovery(payload):
            payload["continue_after_display"] = True
            payload["followup_action"] = "external_skill_discovery"
            return
        payload.setdefault("continue_after_display", False)

    @staticmethod
    def _failure_detail(payload: dict[str, Any], fallback: str) -> str:
        return str(
            payload.get("detail")
            or payload.get("reason")
            or payload.get("error")
            or fallback
        ).strip()

    async def plan(
        self,
        query: str,
        mode: str | None = None,
        candidate_skill_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            return self._compact_plan_payload(
                self._disabled_payload("symphony_compose_graph")
            )
        query_text = str(query or "").strip()
        mode_text = str(mode or "").strip()
        normalized_candidate_skill_ids = _normalize_candidate_skill_ids(
            candidate_skill_ids
        )
        payload = await self._call_service(
            "plan",
            query_text,
            mode=mode_text or None,
            candidate_skill_ids=normalized_candidate_skill_ids,
            progress=current_tool_progress(),
        )
        if isinstance(payload, dict):
            self._attach_followup_control(payload)
            return self._compact_plan_payload(payload)
        return payload

    @staticmethod
    def is_enabled(config: dict[str, Any] | None = None) -> bool:
        try:
            if config is None:
                return bool(load_symphony_config().enabled)
            return bool(load_symphony_config(config).enabled)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load Symphony config; tools disabled: %s", exc)
            return False

    def get_tools(self, config: dict[str, Any] | None = None) -> list[Tool]:
        if not self.is_enabled(config):
            return []

        def make_tool(
            name: str,
            description: str,
            input_params: dict[str, Any],
            func: Callable[..., Any],
            uses_internal_timeout: bool = False,
        ) -> Tool:
            card = ToolCard(
                id=name,
                name=name,
                description=description,
                input_params=input_params,
                properties=(
                    {"resilience": {"timeout_s": None}} if uses_internal_timeout else {}
                ),
            )
            tool_type = (
                _ComposeGraphLocalFunction
                if name == "symphony_compose_graph"
                else LocalFunction
            )
            return tool_type(card=card, func=func)

        return [
            make_tool(
                "symphony_read_graph",
                "Read whether the Skill Graph exists or is stale before composing Skill execution.",
                {"type": "object", "properties": {}},
                self.graph_status,
            ),
            make_tool(
                "symphony_refresh_graph",
                (
                    "Extract installed Skill features and refresh the Skill Graph. "
                    "If a result reports graph_build_timeout or manual_graph_build, "
                    "do not call this tool or symphony_compose_graph again in this round."
                ),
                {"type": "object", "properties": {}},
                self.refresh_graph,
                uses_internal_timeout=True,
            ),
            make_tool(
                "symphony_compose_graph",
                (
                    "Compose an execution plan for a task that requires multiple installed "
                    "skills or an ordered skill workflow. Discovery, comparison, and "
                    "recommendation alone do not require this tool. Pass only shortlisted "
                    "exact skill IDs in candidate_skill_ids; omit that argument when the "
                    "user requested a plan but no candidate is known. The tool may refresh a "
                    "missing or stale graph before composing. Use its returned plan and ask "
                    "for any missing inputs it reports. If it returns graph_build_timeout or "
                    "manual_graph_build, do not retry graph tools in the same round."
                ),
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The user's original task, without retrieval commands or internal notes.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["fast", "beam"],
                            "description": (
                                "Optional planning mode. Use fast for simple tasks, "
                                "short execution chains, or when candidate skills are "
                                "already clear; fast is the default. Use beam for "
                                "complex multi-step tasks, tasks with multiple possible "
                                "skill paths, or when prerequisite skills need to be "
                                "discovered through bidirectional search."
                            ),
                        },
                        "candidate_skill_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Exact installed skill IDs already shortlisted for the task. "
                                "Batch all relevant IDs in this one argument; do not include "
                                "weak matches or every skill from a catalog overview."
                            ),
                        },
                    },
                    "required": ["query"],
                },
                self.plan,
                uses_internal_timeout=True,
            ),
        ]


def _copy_compact_fields(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value in (None, "", [], {}):
            continue
        compact[key] = value
    return compact


def _normalize_candidate_skill_ids(values: Any) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        current_skill_id = str(value or "").strip()
        if not current_skill_id or current_skill_id in seen:
            continue
        seen.add(current_skill_id)
        output.append(current_skill_id)
    return output
