# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM-based Proposal generator.

Uses an LLM to analyse trace span data and generate structured Proposals.
This is the "smart" generator — more flexible than RuleProposer but
requires an LLM call per batch.
"""

from __future__ import annotations

import json
import logging

from jiuwenswarm.evolve.models import (
    EvidenceRef,
    Proposal,
    ProposalTargetType,
    ProposalState,
    TraceBatch,
)
from jiuwenswarm.evolve.proposal_generators.base import ProposalGenerator
from jiuwenswarm.evolve.registry import proposal_generators

logger = logging.getLogger(__name__)

# Prompt template for the LLM to generate proposals from span data.
PROPOSER_SYSTEM_PROMPT = """You are an expert agent trace analyst. Your job is to examine
OpenTelemetry trace span data and identify issues that can be improved.

For each issue you find, produce a structured analysis with:
- failure_evidence: specific trace_id + span_id references
- root_cause: why the issue happened
- targeted_fix: what concrete change would address it (as a JSON object)
- predicted_impact: what improvement is expected
- risk: potential downsides

Only report REAL issues. If the trace looks successful, return an empty list.

Output format (JSON):
{
  "proposals": [
    {
      "target_type": "skill|memory|training",
      "proposal_type": "add_skill_experience|add_memory_retrieval_hint|...",
      "failure_evidence": [
        {"trace_id": "...", "span_id": "...", "field_path": null, "description": "..."}
      ],
      "root_cause": "...",
      "targeted_fix": {"action": "...", ...},
      "predicted_impact": "...",
      "risk": "..."
    }
  ]
}
"""


@proposal_generators.register("llm_proposer")
class LLMProposer(ProposalGenerator):
    """Generate Proposals by sending trace span data to an LLM for analysis."""

    def __init__(
        self,
        trace_reader: object | None = None,
        model_name: str = "gpt-4",
        max_spans_per_trace: int = 50,
    ) -> None:
        super().__init__(name="llm_proposer", trace_reader=trace_reader)
        self._model_name = model_name
        self._max_spans = max_spans_per_trace

    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        if self._trace_reader is None:
            logger.warning("LLMProposer: no trace_reader configured")
            return []

        # Collect span summaries for the batch
        trace_summaries: list[dict] = []
        for trace_id in batch.trace_ids:
            spans = self._trace_reader.read_spans(trace_id)
            if not spans:
                continue
            trace_summaries.append(
                {
                    "trace_id": trace_id,
                    "span_count": len(spans),
                    "spans": [
                        {
                            "span_id": s.get("span_id", ""),
                            "name": s.get("name", ""),
                            "status": s.get("status_code", "UNSET"),
                            "events": s.get("events", "")[:500],
                        }
                        for s in spans[: self._max_spans]
                    ],
                }
            )

        if not trace_summaries:
            return []

        # Call LLM
        proposals_raw = await self._call_llm(trace_summaries)

        # Parse into Proposal objects
        proposals: list[Proposal] = []
        for raw in proposals_raw:
            try:
                prop = Proposal(
                    target_type=ProposalTargetType(
                        raw.get("target_type", "skill")
                    ),
                    proposal_type=raw.get("proposal_type", "add_skill_experience"),
                    failure_evidence=[
                        EvidenceRef(**e)
                        for e in raw.get("failure_evidence", [])
                    ],
                    root_cause=raw.get("root_cause", ""),
                    targeted_fix=raw.get("targeted_fix", {}),
                    predicted_impact=raw.get("predicted_impact", ""),
                    risk=raw.get("risk"),
                    proposer_name="llm_proposer",
                    state=ProposalState.CANDIDATE,
                    metadata={"batch_id": batch.batch_id},
                )
                proposals.append(prop)
            except Exception as exc:
                logger.warning("LLMProposer: failed to parse proposal: %s", exc)

        logger.info(
            "LLMProposer: generated %d proposals from %d trace summaries",
            len(proposals), len(trace_summaries),
        )
        return proposals

    async def _call_llm(self, trace_summaries: list[dict]) -> list[dict]:
        """Call the LLM and parse the JSON response.

        This is a placeholder implementation. In production, this should
        use the agent's LLM client (e.g., openjiuwen's Model).
        """
        # For the demo skeleton, return empty — the LLM integration
        # is wired up once the framework is running.
        logger.warning(
            "LLMProposer._call_llm: LLM not yet integrated — returning empty. "
            "Model: %s, traces: %d",
            self._model_name,
            len(trace_summaries),
        )
        # TODO: Integrate with openjiuwen Model to make real LLM calls.
        # The prompt is in PROPOSER_SYSTEM_PROMPT above.
        return []
