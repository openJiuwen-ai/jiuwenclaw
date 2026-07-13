"""Agent-facing Symphony tools."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Callable

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.extensions.registry import ExtensionRegistry
from jiuwenswarm.symphony.config import load_symphony_config
from jiuwenswarm.symphony.score_storage import (
    CURRENT_POINTER_FILENAME,
    resolve_score_artifact_dir,
)

logger = logging.getLogger(__name__)

_SKILL_RETRIEVAL_CANDIDATE_RECORD_LIMIT = 10


class SymphonyToolkit:
    """Expose Symphony extension RPC methods as model-callable tools."""

    @staticmethod
    def _resolve_timeout_s(default_s: float = 1800.0) -> float:
        return default_s

    async def _call_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.info(
            "[SymphonyToolkit] calling RPC: method=%s params_keys=%s",
            method,
            sorted(params),
        )
        try:
            registry = ExtensionRegistry.get_instance()
        except RuntimeError as exc:
            return {
                "success": False,
                "detail": f"Symphony extension RPC unavailable: {method}: {exc}",
            }

        handler = registry.get_rpc_handler(method)
        if handler is None:
            return {
                "success": False,
                "detail": f"Symphony extension RPC unavailable: {method}: handler not registered",
            }

        timeout_s = self._resolve_timeout_s()
        try:
            result = handler(params, request=None)
            payload = await asyncio.wait_for(
                result if inspect.isawaitable(result) else _return_value(result),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            return {"success": False, "detail": f"{method}: timeout after {timeout_s}s"}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Symphony RPC failed: %s", method)
            return {"success": False, "detail": f"{method}: {exc}"}

        return payload if isinstance(payload, dict) else {"success": True, "result": payload}

    @staticmethod
    def _disabled_payload(method: str) -> dict[str, Any]:
        return {
            "success": False,
            "disabled": True,
            "method": method,
            "detail": "Symphony is disabled by config: symphony.enabled=false",
        }

    async def score_status(self) -> dict[str, Any]:
        if not self.is_enabled():
            return self._disabled_payload("symphony.score_status")
        return await self._call_rpc("symphony.score_status", {})

    async def refresh_score(self) -> dict[str, Any]:
        if not self.is_enabled():
            return self._disabled_payload("symphony.build_score")
        return await self._call_rpc("symphony.build_score", {})

    @staticmethod
    def _score_needs_build(status: dict[str, Any]) -> bool:
        if not bool(status.get("exists", False)):
            return True
        if bool(status.get("stale", False)):
            return True
        for key in ("added_count", "changed_count", "removed_count"):
            try:
                if int(status.get(key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    @staticmethod
    def _score_summary_markdown(
        status: dict[str, Any],
        update: dict[str, Any] | None,
    ) -> str:
        lines = ["## Symphony score", ""]
        if status.get("success"):
            state = "stale" if status.get("stale") else "fresh"
            if not status.get("exists"):
                state = "missing"
            reason = str(status.get("reason") or "").strip()
            lines.append(f"- Status: `{state}`")
            if reason:
                lines.append(f"- Detail: {reason}")
            for key, label in (
                ("added_count", "Added"),
                ("changed_count", "Changed"),
                ("removed_count", "Removed"),
            ):
                value = status.get(key)
                if value not in (None, ""):
                    lines.append(f"- {label}: `{value}`")
        else:
            detail = str(status.get("detail") or "score status failed").strip()
            lines.append("- Status: `failed`")
            lines.append(f"- Detail: {detail}")
        if update is not None:
            if update.get("rebuilt") is False:
                lines.append("- Update: `not required`")
                created_at = str(update.get("score_created_at") or "").strip()
                if created_at:
                    lines.append(f"- Score created: `{created_at}`")
                return "\n".join(lines)

            update_state = "succeeded" if update.get("success") else "failed"
            lines.append(f"- Update: `{update_state}`")
            detail = str(update.get("detail") or update.get("reason") or "").strip()
            if detail:
                lines.append(f"- Update detail: {detail}")
            created_at = str(update.get("score_created_at") or "").strip()
            if created_at:
                lines.append(f"- Score created: `{created_at}`")
            total_tokens = update.get("llm_total_tokens")
            if total_tokens not in (None, ""):
                lines.append(f"- Build tokens: `{total_tokens}`")
        else:
            lines.append("- Update: `not required`")
        return "\n".join(lines)

    @staticmethod
    def _attach_display_payload(
        payload: dict[str, Any],
        status: dict[str, Any],
        update: dict[str, Any] | None,
    ) -> None:
        del status, update
        presentation = payload.get("presentation")
        presentation_markdown = (
            presentation.get("markdown") if isinstance(presentation, dict) else None
        )
        presentation_mermaid = (
            presentation.get("mermaid") if isinstance(presentation, dict) else None
        )
        rendered = (
            payload.get("content")
            or payload.get("markdown")
            or presentation_markdown
        )
        mermaid = payload.get("mermaid") or presentation_mermaid
        if isinstance(mermaid, str) and mermaid.strip():
            payload.setdefault("mermaid", mermaid.strip())
        if not isinstance(rendered, str):
            rendered = ""
        rendered = rendered.strip()
        payload["content"] = rendered
        payload["markdown"] = rendered
        payload["summary"] = rendered
        payload.setdefault("display_format", "markdown")
        payload.setdefault("direct_display", True)

    @classmethod
    def _compact_plan_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        planning_payload = cls._planning_payload(payload)
        compact: dict[str, Any] = {
            "success": payload.get("success", True),
        }
        for key in (
            "disabled",
            "content",
            "mermaid",
            "direct_display",
            "display_format",
            "continue_after_display",
            "followup_action",
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

        score_status = payload.get("score_status")
        if isinstance(score_status, dict):
            compact["score_status"] = cls._compact_score_status(score_status)

        score_build = payload.get("score_build")
        if isinstance(score_build, dict):
            compact["score_build"] = cls._compact_score_build(score_build)
        elif isinstance(score_status, dict):
            compact["score_build"] = cls._score_build_summary(score_status, None)

        skill_retrieval = planning_payload.get("skill_retrieval")
        if not isinstance(skill_retrieval, dict):
            skill_retrieval = payload.get("skill_retrieval")
        if isinstance(skill_retrieval, dict):
            compact["skill_retrieval"] = cls._compact_skill_retrieval(skill_retrieval)

        plan = cls._compact_plan(cls._primary_plan(planning_payload))
        if plan:
            compact["plan"] = plan

        metrics = cls._compact_metrics(payload, planning_payload)
        if metrics:
            compact["metrics"] = metrics
        return compact

    @staticmethod
    def _compact_score_status(status: dict[str, Any]) -> dict[str, Any]:
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
    def _compact_score_build(cls, update: dict[str, Any]) -> dict[str, Any]:
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
                "score_created_at",
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

    @classmethod
    def _score_build_summary(
        cls,
        status: dict[str, Any],
        update: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(update, dict):
            compact = cls._compact_score_build(update)
            if update.get("success") is not False:
                metadata = cls._score_metadata(status, update)
                for key, value in metadata.items():
                    compact.setdefault(key, value)
            return compact

        if not status.get("success"):
            reason = "score_status_failed"
        elif cls._score_needs_build(status):
            reason = "not_run"
        else:
            reason = "not_required"
        return {
            "rebuilt": False,
            "reason": reason,
            **cls._score_metadata(status, None),
        }

    @staticmethod
    def _score_metadata(
        status: dict[str, Any],
        update: dict[str, Any] | None,
    ) -> dict[str, Any]:
        score_dir = ""
        if isinstance(update, dict):
            score_dir = str(update.get("score_dir") or "").strip()
        if not score_dir:
            score_dir = str(status.get("score_dir") or "").strip()
        if not score_dir:
            return {}

        root = Path(score_dir)
        metadata: dict[str, Any] = {}
        pointer_path = root / CURRENT_POINTER_FILENAME
        if pointer_path.is_file():
            try:
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pointer = {}
            if isinstance(pointer, dict):
                version = str(pointer.get("version") or "").strip()
                if version:
                    metadata["version"] = version

        try:
            manifest_path = resolve_score_artifact_dir(root) / "score_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        if isinstance(manifest, dict):
            created_at = str(manifest.get("created_at") or "").strip()
            if created_at:
                metadata["score_created_at"] = created_at
        return metadata

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
    def _compact_skill_retrieval(cls, payload: dict[str, Any]) -> dict[str, Any]:
        compact = _copy_compact_fields(
            payload,
            (
                "enabled",
                "source",
                "used",
                "candidate_skill_ids",
                "candidate_count",
                "fallback_reason",
            ),
            keep_empty=("fallback_reason",),
        )
        records = payload.get("candidate_records")
        if isinstance(records, list):
            compact["candidate_records"] = [
                cls._compact_candidate_record(record)
                for record in records[:_SKILL_RETRIEVAL_CANDIDATE_RECORD_LIMIT]
                if isinstance(record, dict)
            ]
        return compact

    @staticmethod
    def _compact_candidate_record(record: dict[str, Any]) -> dict[str, Any]:
        return _copy_compact_fields(
            record,
            ("rank", "skill_id", "skill_name", "score", "source"),
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
        confidence = edge.get("confidence")
        if confidence not in (None, ""):
            compact["confidence"] = confidence
        return compact

    @staticmethod
    def _compact_metrics(
        payload: dict[str, Any],
        planning_payload: dict[str, Any],
    ) -> dict[str, Any]:
        metrics = _copy_compact_fields(
            planning_payload,
            (
                "planning_mode",
                "llm_call_count",
                "candidate_skill_count",
                "candidate_edge_count",
            ),
        )
        mode = payload.get("mode") or planning_payload.get("mode")
        if mode not in (None, ""):
            metrics["mode"] = mode
        return metrics

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
        status = str(
            plan.get("status")
            or planning_payload.get("status")
            or payload.get("status")
            or ""
        ).strip().lower()
        missing_inputs = (
            plan.get("missing_inputs")
            or planning_payload.get("missing_inputs")
            or []
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
            execution_graph.get("nodes")
            if isinstance(execution_graph, dict)
            else []
        )
        return not steps and not graph_nodes

    @classmethod
    def _attach_followup_control(cls, payload: dict[str, Any]) -> None:
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
            return self._compact_plan_payload(self._disabled_payload("symphony.plan"))
        status = await self.score_status()
        if not status.get("success"):
            detail = self._failure_detail(status, "symphony.score_status failed")
            return self._compact_plan_payload({
                "success": False,
                "detail": f"symphony.score_status failed before planning: {detail}",
                "score_status": status,
            })
        update: dict[str, Any] | None = None
        if status.get("success") and self._score_needs_build(status):
            update = await self.refresh_score()
            if not update.get("success"):
                detail = self._failure_detail(update, "symphony.build_score failed")
                return self._compact_plan_payload({
                    "success": False,
                    "detail": f"symphony.build_score failed before planning: {detail}",
                    "score_status": status,
                    "score_build": self._score_build_summary(status, update),
                })
        score_build = self._score_build_summary(status, update)

        params: dict[str, Any] = {
            "query": str(query or "").strip(),
        }
        mode_text = str(mode or "").strip()
        if mode_text:
            params["mode"] = mode_text
        normalized_candidate_skill_ids = _normalize_candidate_skill_ids(
            candidate_skill_ids
        )
        if normalized_candidate_skill_ids is not None:
            params["candidate_skill_ids"] = normalized_candidate_skill_ids
        payload = await self._call_rpc("symphony.plan", params)
        if isinstance(payload, dict):
            payload.setdefault("score_status", status)
            payload["score_build"] = score_build
            self._attach_followup_control(payload)
            self._attach_display_payload(payload, status, score_build)
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
        ) -> Tool:
            card = ToolCard(
                id=name,
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                "symphony_read_score",
                "Read whether the Symphony score exists or is stale before composing skill execution.",
                {"type": "object", "properties": {}},
                self.score_status,
            ),
            make_tool(
                "symphony_refresh_score",
                "Extract installed skill features and refresh the Symphony score.",
                {"type": "object", "properties": {}},
                self.refresh_score,
            ),
            make_tool(
                "symphony_compose_score",
                (
                    "MUST call before answering when the user says to use skill(s) "
                    "or 技能, or when skill capabilities, skill chaining, skill ordering, "
                    "or a specialized toolchain could help complete the task. Use skill_branch_peek "
                    "and skill_branch_explore first when installed-skill retrieval can narrow "
                    "the candidate skills, then pass returned worker_id values as candidate_skill_ids. "
                    "This is the Symphony composition entrypoint: it reads the score, refreshes stale "
                    "or missing scores, then composes the skill execution graph from the provided "
                    "candidates or a default score subgraph. If no suitable candidates or a missing "
                    "capability is reported, use search_skill to discover external skills; when "
                    "installing a discovered skill is appropriate, call install_skill, then call "
                    "symphony_refresh_score and retry this tool with the original query. "
                    "After it returns, present its content result directly to the user; "
                    "do not call individual skill tools just to manually recreate the plan. "
                    "Skip only clearly ordinary tasks that do not benefit from skill capabilities."
                ),
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The original user task to complete with skill capabilities.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["fast"],
                            "description": (
                                "Optional planning mode. The current Symphony runtime "
                                "supports fast planning only."
                            ),
                        },
                        "candidate_skill_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional installed skill worker_id values returned by "
                                "skill_branch_explore. When provided, Symphony composes "
                                "from these candidate skills and their eligible neighbors."
                            ),
                        },
                    },
                    "required": ["query"],
                },
                self.plan,
            ),
        ]


async def _return_value(value: Any) -> Any:
    return value


def _copy_compact_fields(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    *,
    keep_empty: tuple[str, ...] = (),
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    keep_empty_set = set(keep_empty)
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if key not in keep_empty_set and value in (None, "", [], {}):
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
