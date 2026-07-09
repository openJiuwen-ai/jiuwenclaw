# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rule-based Proposal generator — pattern-matching on span events.

This is the simplest built-in generator. It inspects OTEL span data for
common error patterns (tool failures, missing params, etc.) and produces
structured Proposals without calling an LLM.
"""

from __future__ import annotations

import logging

from jiuwenswarm.evolve.models import (
    EvidenceRef,
    Proposal,
    ProposalTargetType,
    TraceBatch,
)
from jiuwenswarm.evolve.proposal_generators.base import ProposalGenerator
from jiuwenswarm.evolve.registry import proposal_generators

logger = logging.getLogger(__name__)


@proposal_generators.register("rule_proposer")
class RuleProposer(ProposalGenerator):
    """Generate Proposals by scanning span events for known error patterns.

    Patterns detected (initial version):
    - Tool execution errors (status_code=ERROR in span)
    - Missing parameter mentions in span events
    - Low-confidence or fallback responses

    Does NOT require an LLM — purely rule-based, fast, and deterministic.
    """

    def __init__(self, trace_reader: object | None = None) -> None:
        super().__init__(name="rule_proposer", trace_reader=trace_reader)

    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        proposals: list[Proposal] = []

        if self._trace_reader is None:
            logger.warning("RuleProposer: no trace_reader configured, cannot read spans")
            return proposals

        for trace_id in batch.trace_ids:
            spans = self._trace_reader.read_spans(trace_id)
            if not spans:
                continue

            # Pattern 1: Tool execution errors
            for span in spans:
                if self._is_tool_error(span):
                    prop = self._build_tool_error_proposal(
                        trace_id=trace_id,
                        span=span,
                        batch_id=batch.batch_id,
                    )
                    proposals.append(prop)
                    break  # One proposal per trace for now

            # Pattern 2: Missing parameter or validation errors in events
            if len(proposals) < len(batch.trace_ids):  # haven't proposed yet for this trace
                for span in spans:
                    if self._has_missing_param(span):
                        prop = self._build_missing_param_proposal(
                            trace_id=trace_id,
                            span=span,
                            batch_id=batch.batch_id,
                        )
                        proposals.append(prop)
                        break

        logger.info(
            "RuleProposer: generated %d proposals from %d traces",
            len(proposals), len(batch.trace_ids),
        )
        return proposals

    # ------------------------------------------------------------------
    # Pattern detectors
    # ------------------------------------------------------------------

    @staticmethod
    def _is_tool_error(span: dict) -> bool:
        """Check if the span represents a tool execution error."""
        status = span.get("status_code", "")
        if status in ("ERROR", "error"):
            return True
        # Also check events for error markers
        events = span.get("events", "")
        if events and isinstance(events, str):
            return "error" in events.lower() or "exception" in events.lower()
        return False

    @staticmethod
    def _has_missing_param(span: dict) -> bool:
        """Check if span events mention missing/required parameters."""
        events = span.get("events", "")
        if not events or not isinstance(events, str):
            return False
        keywords = ("missing", "required", "parameter", "argument", "invalid")
        event_lower = events.lower()
        return any(k in event_lower for k in keywords)

    # ------------------------------------------------------------------
    # Proposal builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_tool_error_proposal(
        trace_id: str, span: dict, batch_id: str
    ) -> Proposal:
        span_id = span.get("span_id", "")
        span_name = span.get("name", "unknown")
        return Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="add_skill_experience",
            failure_evidence=[
                EvidenceRef(
                    trace_id=trace_id,
                    span_id=span_id,
                    description=f"Tool error in span '{span_name}': status_code=ERROR",
                )
            ],
            root_cause=f"Tool execution failure in {span_name}",
            targeted_fix={
                "action": "add_error_handling",
                "tool": span_name,
                "suggestion": "Add retry logic or fallback behavior for this tool",
            },
            predicted_impact="Reduce tool execution failures",
            risk="Low — adds defensive error handling",
            proposer_name="rule_proposer",
            metadata={"batch_id": batch_id},
        )

    @staticmethod
    def _build_missing_param_proposal(
        trace_id: str, span: dict, batch_id: str
    ) -> Proposal:
        span_id = span.get("span_id", "")
        span_name = span.get("name", "unknown")
        return Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="add_skill_experience",
            failure_evidence=[
                EvidenceRef(
                    trace_id=trace_id,
                    span_id=span_id,
                    description=f"Missing/invalid parameter in span '{span_name}'",
                )
            ],
            root_cause=f"Missing or invalid parameter in {span_name} execution",
            targeted_fix={
                "action": "add_parameter_validation",
                "tool": span_name,
                "suggestion": "Add parameter validation before tool execution",
            },
            predicted_impact="Fewer parameter-related errors",
            risk="Low",
            proposer_name="rule_proposer",
            metadata={"batch_id": batch_id},
        )
