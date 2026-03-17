# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Trajectory operations: extraction and query utilities.

- TracerTrajectoryExtractor: Extract Trajectory from Session.tracer() spans.
- iter_steps / get_steps_for_case_operator: Filter TrajectoryStep by criteria.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from openjiuwen.agent_evolving.trajectory.types import (
    ExecutionSpec,
    Trajectory,
    TrajectoryStep,
    StepKind,
)


def _dt_to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert datetime to milliseconds since epoch. Returns None if dt is None."""
    return int(dt.timestamp() * 1000) if dt else None


class TracerTrajectoryExtractor:
    """
    Extract Trajectory from Session.tracer() spans.

    Extracts agent spans (llm/tool/agent) and workflow spans with DAG edges.
    Reads span fields without requiring core modification: invoke_type, name,
    inputs, outputs, error, meta_data, llm_call_id.
    """

    def extract(self, session: Any, execution: ExecutionSpec) -> Trajectory:
        """
        Extract trajectory from session tracer.

        Args:
            session: Agent session with tracer attribute
            execution: Execution specification for this trajectory

        Returns:
            Trajectory containing all steps and their dependencies
        """
        tracer = self._get_tracer(session)
        agent_spans = self._get_agent_spans(tracer)
        wf_spans = self._get_workflow_spans(tracer)

        steps: List[TrajectoryStep] = []
        invoke_index: Dict[str, int] = {}

        # Extract agent steps and build invoke index
        for span in agent_spans:
            step, invoke_id = self._build_agent_step(span)
            steps.append(step)
            if invoke_id:
                invoke_index[invoke_id] = len(steps) - 1

        # Extract workflow steps
        for span in wf_spans:
            step = self._build_workflow_step(span)
            steps.append(step)

        # Build edges from parent_invoke_id and child_invokes
        edges = self._build_edges(steps, invoke_index)

        return Trajectory(
            case_id=execution.case_id,
            execution_id=execution.execution_id,
            trace_id=self._get_trace_id(tracer),
            steps=steps,
            edges=edges or None,
        )

    @staticmethod
    def _get_tracer(session: Any) -> Any:
        """Get tracer from session. Returns None if not available."""
        tracer = getattr(session, "tracer", None)
        return tracer() if callable(tracer) else tracer

    @staticmethod
    def _get_agent_spans(tracer: Any) -> List[Any]:
        """Extract agent spans from tracer."""
        if tracer is None:
            return []
        agent_sm = getattr(tracer, "tracer_agent_span_manager", None)
        if agent_sm is None:
            return []
        get_spans = getattr(agent_sm, "get_all_spans", None)
        if not callable(get_spans):
            return []
        result = get_spans()
        return result if isinstance(result, list) else []

    @staticmethod
    def _get_workflow_spans(tracer: Any) -> List[Any]:
        """Extract workflow spans from tracer."""
        if tracer is None:
            return []
        wf_sms = getattr(tracer, "tracer_workflow_span_manager_dict", None)
        if not isinstance(wf_sms, dict):
            return []
        spans: List[Any] = []
        for sm in wf_sms.values():
            get_spans = getattr(sm, "get_all_spans", None)
            if callable(get_spans):
                result = get_spans()
                if isinstance(result, list):
                    spans.extend(result)
        return spans

    @staticmethod
    def _get_trace_id(tracer: Any) -> Optional[str]:
        """Extract trace ID from tracer."""
        if tracer is None:
            return None
        trace_id = getattr(tracer, "_trace_id", None)
        return str(trace_id) if trace_id is not None else None

    def _build_agent_step(self, span: Any) -> Tuple[TrajectoryStep, Optional[str]]:
        """
        Build TrajectoryStep from agent span.

        Returns:
            Tuple of (step, invoke_id)
        """
        kind = self._classify_span_kind(span)
        meta = getattr(span, "meta_data", None) or {}
        operator_id = self._get_operator_id(span, meta)
        node_id = meta.get("node_id") or meta.get("component_id")

        step = TrajectoryStep(
            kind=kind,
            operator_id=operator_id,
            agent_id=meta.get("agent_id"),
            role=meta.get("role"),
            node_id=node_id,
            inputs=self._extract_inputs(span),
            outputs=self._extract_outputs(span),
            error=getattr(span, "error", None),
            start_time_ms=_dt_to_ms(getattr(span, "start_time", None)),
            end_time_ms=_dt_to_ms(getattr(span, "end_time", None)),
            meta=self._build_meta(span, meta),
        )
        invoke_id = getattr(span, "invoke_id", None)
        return step, invoke_id if isinstance(invoke_id, str) else None

    def _build_workflow_step(self, span: Any) -> TrajectoryStep:
        """Build TrajectoryStep from workflow span."""
        node_id = (
            getattr(span, "component_id", None)
            or getattr(span, "component_name", None)
            or getattr(span, "workflow_name", None)
        )
        return TrajectoryStep(
            kind="workflow",
            operator_id=None,
            agent_id=None,
            role=None,
            node_id=node_id,
            inputs=self._extract_inputs(span),
            outputs=self._extract_outputs(span),
            error=getattr(span, "error", None),
            start_time_ms=_dt_to_ms(getattr(span, "start_time", None)),
            end_time_ms=_dt_to_ms(getattr(span, "end_time", None)),
            meta={
                "workflow_id": getattr(span, "workflow_id", None),
                "workflow_name": getattr(span, "workflow_name", None),
                "component_id": getattr(span, "component_id", None),
                "component_name": getattr(span, "component_name", None),
                "component_type": getattr(span, "component_type", None),
                "loop_node_id": getattr(span, "loop_node_id", None),
                "loop_index": getattr(span, "loop_index", None),
                "parent_node_id": getattr(span, "parent_node_id", None),
            },
        )

    @staticmethod
    def _classify_span_kind(span: Any) -> StepKind:
        """Classify span kind based on invoke_type."""
        invoke_type = getattr(span, "invoke_type", None)
        invoke_str = str(invoke_type) if invoke_type else ""

        if invoke_str == "plugin":
            return "tool"  # type: ignore[return-value]
        if invoke_str in ("llm", "workflow", "memory"):
            return invoke_str  # type: ignore[return-value]
        return "agent"  # type: ignore[return-value]

    @staticmethod
    def _get_operator_id(span: Any, meta: Dict[str, Any]) -> Optional[str]:
        """Extract operator ID from span attributes."""
        return (
            getattr(span, "operator_id", None)
            or getattr(span, "llm_call_id", None)
            or meta.get("operator_id")
            or getattr(span, "name", None)
        )

    @staticmethod
    def _extract_inputs(span: Any) -> Any:
        """Extract inputs from span, unwrapping if nested."""
        raw = getattr(span, "inputs", None)
        if isinstance(raw, dict) and "inputs" in raw:
            return raw["inputs"]
        return raw

    @staticmethod
    def _extract_outputs(span: Any) -> Any:
        """Extract outputs from span, unwrapping if nested."""
        raw = getattr(span, "outputs", None)
        if isinstance(raw, dict) and "outputs" in raw:
            return raw["outputs"]
        return raw

    @staticmethod
    def _build_meta(span: Any, base_meta: Dict[str, Any]) -> Dict[str, Any]:
        """Build meta dict with invoke relationships."""
        meta = copy.deepcopy(base_meta)
        meta["invoke_id"] = getattr(span, "invoke_id", None)
        meta["parent_invoke_id"] = getattr(span, "parent_invoke_id", None)
        meta["child_invokes"] = getattr(span, "child_invokes_id", None)
        return meta

    @staticmethod
    def _build_edges(
        steps: List[TrajectoryStep],
        invoke_index: Dict[str, int],
    ) -> List[Tuple[int, int]]:
        """Build dependency edges from step metadata."""
        edges: List[Tuple[int, int]] = []

        for idx, step in enumerate(steps):
            parent_id = step.meta.get("parent_invoke_id")
            if isinstance(parent_id, str) and parent_id in invoke_index:
                edges.append((invoke_index[parent_id], idx))

            child_ids = step.meta.get("child_invokes") or []
            if isinstance(child_ids, list):
                for cid in child_ids:
                    if isinstance(cid, str) and cid in invoke_index:
                        edges.append((idx, invoke_index[cid]))

        return edges


def iter_steps(
    trajectories: List[Trajectory],
    *,
    case_id: Optional[str] = None,
    operator_id: Optional[str] = None,
    kind: Optional[StepKind] = None,
) -> Iterator[TrajectoryStep]:
    """
    Iterate over TrajectoryStep matching optional criteria.

    Args:
        trajectories: List of trajectories to search
        case_id: Optional case ID filter
        operator_id: Optional operator ID filter
        kind: Optional step kind filter (e.g., "llm", "tool")

    Yields:
        TrajectoryStep matching all specified criteria
    """
    for traj in trajectories:
        if case_id is not None and traj.case_id != case_id:
            continue
        for step in traj.steps:
            if operator_id is not None and step.operator_id != operator_id:
                continue
            if kind is not None and step.kind != kind:
                continue
            yield step


def get_steps_for_case_operator(
    trajectories: List[Trajectory],
    case_id: str,
    operator_id: str,
    kind: StepKind = "llm",
) -> List[TrajectoryStep]:
    """
    Get all steps for a specific case and operator.

    Args:
        trajectories: List of trajectories to search
        case_id: Case ID to match
        operator_id: Operator ID to match
        kind: Step kind to filter by (default: "llm")

    Returns:
        List of matching TrajectoryStep objects
    """
    return list(
        iter_steps(
            trajectories,
            case_id=case_id,
            operator_id=operator_id,
            kind=kind,
        )
    )
