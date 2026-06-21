# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""AheProposer — self-contained CLEAN→EVAL→DIAG→GOV→PROPOSE pipeline.

Pluggable algorithm: zero code overlap with LLMProposer. Only shared
contract is ProposalGenerator.generate(batch) -> list[Proposal].
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jiuwenswarm.evolve.models import (
    Proposal,
    ProposalState,
    ProposalTargetType,
    EvidenceRef,
    TraceBatch,
    TraceOutcome,
    ExperienceOperationType,
    ExperienceOperation,
)
from jiuwenswarm.evolve.proposal_generators.base import ProposalGenerator
from jiuwenswarm.evolve.registry import proposal_generators
from jiuwenswarm.evolve.otel_adapter import OtelTraceAdapter
from jiuwenswarm.evolve.ahe.evaluator import TraceOutcomeEvaluator, TaskNameInferrer
from jiuwenswarm.evolve.ahe.experience_governor import ExperienceGovernor
from jiuwenswarm.evolve.diagnosis.agent import DiagnosisAgent
from jiuwenswarm.evolve.ahe.proposer_prompts import AHE_PROPOSER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


@proposal_generators.register("ahe_proposer")
class AheProposer(ProposalGenerator):
    """AHE algorithm AHE — self-contained CLEAN->EVAL->DIAG->GOV->PROPOSE.

    Independent from LLMProposer: does not reuse its code, prompt, or
    internal state. Only shares the ProposalGenerator.generate() interface.
    """

    def __init__(
        self,
        trace_reader: Any | None = None,
        store: Any | None = None,
        model: Any | None = None,
        max_proposals: int = 3,
        max_skill_proposals: int = 2,
        skills_dir: str | None = None,
        workspace_dir: str | None = None,
        traces_db_path: str | None = None,
    ) -> None:
        super().__init__(name="ahe_proposer", trace_reader=trace_reader)
        self._store = store
        self._model = model
        self._max_proposals = max_proposals
        self._max_skill_proposals = max_skill_proposals
        self._skills_dir = skills_dir
        self._workspace_dir = workspace_dir
        self._traces_db_path = traces_db_path or "traces.db"
        self._adapter = None  # lazy init
        self._evaluator = None
        self._governor = None
        self._diagnosis_agent = None

    @property
    def _otel_adapter(self) -> OtelTraceAdapter:
        if self._adapter is None:
            self._adapter = OtelTraceAdapter(db_path=self._traces_db_path)
        return self._adapter

    @property
    def _gov(self) -> ExperienceGovernor:
        if self._governor is None:
            self._governor = ExperienceGovernor(
                skills_dir=self._skills_dir,
            )
        return self._governor

    @property
    def _eval(self) -> TraceOutcomeEvaluator:
        if self._evaluator is None:
            self._evaluator = TraceOutcomeEvaluator(model=self._model)
        return self._evaluator

    @property
    def _diag(self) -> DiagnosisAgent:
        if self._diagnosis_agent is None:
            self._diagnosis_agent = DiagnosisAgent(
                store=self._store,
                model=self._model,
                workspace_dir=self._workspace_dir,
            )
        return self._diagnosis_agent

    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        """Execute the full PDA pipeline for a trace batch.

        Returns:
            List of Proposal objects (may be empty if nothing qualifies).
        """
        # ── Step 1: LOAD ──
        trace_ids = list(batch.trace_ids)
        if not trace_ids:
            return []

        logger.info("AheProposer: processing %d traces", len(trace_ids))

        # ── Step 2: CLEAN ──
        normalized_traces = []
        for tid in trace_ids:
            try:
                trace_dict = self._otel_adapter.convert_trace(tid)
                if not trace_dict.get("observations"):
                    continue
                # trace_dict is already in Langfuse format usable by evaluator
                trace_dict["trace_id"] = tid
                normalized_traces.append(trace_dict)
            except Exception as exc:
                logger.warning("AheProposer: CLEAN failed for %s: %s", tid, exc)

        if not normalized_traces:
            logger.info("AheProposer: no traces could be normalized")
            return []

        # ── Step 3: EVAL — filter fail/uncertain only ──
        outcomes = await self._eval.evaluate_batch(normalized_traces)
        failed = [
            (nt, oc) for nt, oc in zip(normalized_traces, outcomes)
            if oc.outcome in ("fail", "uncertain")
        ]

        if not failed:
            logger.info("AheProposer: no fail/uncertain traces, skipping")
            return []

        logger.info("AheProposer: %d of %d traces are fail/uncertain",
                     len(failed), len(normalized_traces))

        # ── Step 4: DIAG ──
        diag_trace_ids = [nt["trace_id"] for nt, _ in failed]
        diagnosis_result = await self._diag.run(
            trace_ids=diag_trace_ids,
            mode="diagnose",
            question="Analyze these traces for root causes of task failure.",
        )

        if diagnosis_result.budget_exceeded:
            logger.warning("AheProposer: DiagnosisAgent budget exceeded")

        # ── Step 5: GOV — get governance context ──
        skill_names = self._extract_skill_names(diagnosis_result, failed)
        governance_contexts = {
            name: self._gov.get_context(name)
            for name in skill_names
        }

        # ── Step 6: PROPOSE — LLM call ──
        proposals_raw = await self._call_llm_propose(
            failed_traces=failed,
            diagnosis_result=diagnosis_result,
            governance_contexts=governance_contexts,
            batch_id=batch.batch_id,
        )

        # ── Parse into Proposal objects ──
        proposals = self._parse_proposals(proposals_raw, batch.batch_id)

        # ── Enforce limits ──
        proposals = self._enforce_limits(proposals)

        logger.info("AheProposer: generated %d proposals", len(proposals))
        return proposals

    def _extract_skill_names(
        self,
        diagnosis_result: Any,
        failed_traces: list[tuple],
    ) -> set[str]:
        """Extract skill names from diagnosis issues and trace data."""
        names = set()

        # From diagnosis issues
        for issue in diagnosis_result.issues:
            # Look for skill_tool in evidence
            if "skill_tool" in (issue.evidence or "").lower():
                # Try to find the skill name from trace data
                for nt, _ in failed_traces:
                    if nt.get("trace_id") == issue.trace_id:
                        # Scan spans for skill_tool calls
                        if self._trace_reader:
                            spans = self._trace_reader.read_spans(issue.trace_id)
                            for span in spans:
                                attrs_str = span.get("attributes", "")
                                if isinstance(attrs_str, str) and "skill_tool" in attrs_str:
                                    # Rough extraction of skill name from attributes
                                    if '"skill_name":' in attrs_str:
                                        import re as _re
                                        m = _re.search(r'"skill_name"\s*:\s*"([^"]+)"', attrs_str)
                                        if m:
                                            names.add(m.group(1))

        # Fallback: use "general" as default skill name
        if not names:
            names.add("general")

        return names

    async def _call_llm_propose(
        self,
        failed_traces: list[tuple],
        diagnosis_result: Any,
        governance_contexts: dict[str, Any],
        batch_id: str,
    ) -> list[dict]:
        """Call LLM to generate proposals from accumulated context."""

        # Build context
        trace_summaries = self._build_trace_summaries(failed_traces)
        diag_summary = self._build_diagnosis_summary(diagnosis_result)
        gov_summary = self._build_governance_summary(governance_contexts)

        user_content = (
            "## Trace Evaluation Results\n"
            + json.dumps(trace_summaries, ensure_ascii=False, indent=2)
            + "\n\n## Diagnosis Results\n"
            + diag_summary
            + "\n\n## Governance Context\n"
            + gov_summary
            + "\n\nGenerate proposals for the problems found."
        )

        try:
            from openjiuwen.core.foundation.llm import (
                Model, SystemMessage, UserMessage,
            )
        except ImportError:
            logger.warning("AheProposer: openjiuwen.llm not available")
            return []

        model = self._model
        if model is None:
            model = await self._init_model()
        if model is None:
            return []

        try:
            messages = [
                SystemMessage(content=AHE_PROPOSER_SYSTEM_PROMPT),
                UserMessage(content=user_content),
            ]
            response = await model.invoke(messages=messages)
            content = response.content if hasattr(response, "content") else str(response)

            parsed = self._parse_llm_json(str(content))
            return parsed.get("proposals", [])

        except Exception as exc:
            logger.warning("AheProposer._call_llm_propose failed: %s", exc)
            return []

    def _build_trace_summaries(self, failed_traces: list[tuple]) -> list[dict]:
        """Build concise trace summaries for LLM input."""
        summaries = []
        for nt, outcome in failed_traces:
            trace_id = nt.get("trace_id", "unknown")
            input_data = nt.get("input", {})
            output_data = nt.get("output", {})

            # Extract first user message and final output
            first_input = (
                input_data.get("message", str(input_data))[:500]
                if isinstance(input_data, dict)
                else str(input_data)[:500]
            )
            final_output = (
                output_data.get("content", str(output_data))[:500]
                if isinstance(output_data, dict)
                else str(output_data)[:500]
            )

            summaries.append({
                "trace_id": trace_id,
                "outcome": outcome.outcome,
                "outcome_reason": outcome.reason,
                "input_snippet": first_input,
                "output_snippet": final_output,
            })
        return summaries

    @staticmethod
    def _build_diagnosis_summary(diag_result: Any) -> str:
        """Build diagnosis summary string."""
        lines = [f"Overall: {diag_result.response}"]
        for i, issue in enumerate(diag_result.issues):
            lines.append(
                f"Issue {i + 1}: [{issue.issue_type}] {issue.summary} "
                f"(trace={issue.trace_id}, span={issue.span_index})"
            )
            if issue.root_cause:
                lines.append(f"  Root cause: {issue.root_cause}")
            if issue.suggested_fix:
                lines.append(f"  Suggestion: {issue.suggested_fix}")
        return "\n".join(lines)

    @staticmethod
    def _build_governance_summary(contexts: dict[str, Any]) -> str:
        """Build governance context summary string."""
        lines = []
        for skill_name, ctx in contexts.items():
            lines.append(
                f"Skill '{skill_name}': {ctx.current_count}/{ctx.max_count} experiences, "
                f"can_add={ctx.can_add}, allowed={[o.value for o in ctx.allowed_operations]}"
            )
            if ctx.similar_experiences:
                sim_ids = [s["id"] for s in ctx.similar_experiences]
                lines.append(f"  Similar experiences: {sim_ids}")
            if ctx.replaceable_experiences:
                rep_ids = [r["id"] for r in ctx.replaceable_experiences]
                lines.append(f"  Replaceable experiences: {rep_ids}")
        return "\n".join(lines)

    def _parse_proposals(
        self, raw_proposals: list[dict], batch_id: str
    ) -> list[Proposal]:
        """Parse raw dicts from LLM into Proposal objects."""
        proposals = []
        for raw in raw_proposals:
            try:
                operations_raw = raw.get("operations", [])
                # Normalize operations into ExperienceOperation list
                ops = []
                for op_raw in operations_raw:
                    op_type = ExperienceOperationType(op_raw.get("op", "add"))
                    ev_refs = [
                        EvidenceRef(**e) for e in op_raw.get("evidence_refs", [])
                    ]
                    ops.append(
                        ExperienceOperation(
                            op=op_type,
                            target_experience_id=op_raw.get("target_experience_id"),
                            new_content=op_raw.get("new_content"),
                            reason=op_raw.get("reason", ""),
                            evidence_refs=ev_refs,
                        ).model_dump()
                    )

                # Build failure evidence
                evidence = [
                    EvidenceRef(**e) for e in raw.get("failure_evidence", [])
                ]

                prop = Proposal(
                    target_id=raw.get("target_id"),
                    target_type=ProposalTargetType(raw.get("target_type", "skill")),
                    proposal_type=raw.get("proposal_type", "add_skill_experience"),
                    failure_evidence=evidence,
                    root_cause=raw.get("root_cause", ""),
                    targeted_fix=raw.get("targeted_fix", {}),
                    predicted_impact=raw.get("predicted_impact", ""),
                    risk=raw.get("risk"),
                    proposer_name="ahe_proposer",
                    state=ProposalState.CANDIDATE,
                    metadata={
                        "batch_id": batch_id,
                        "operations": ops,
                    },
                )
                proposals.append(prop)
            except Exception as exc:
                logger.warning("AheProposer: failed to parse proposal: %s", exc)
        return proposals

    def _enforce_limits(self, proposals: list[Proposal]) -> list[Proposal]:
        """Enforce max proposals per batch and max skill proposals.

        Phase 1 rules:
        - Max 3 proposals total per batch
        - Max 2 skill proposals per batch
        """
        # Mark all as CANDIDATE first, then activate top N
        for p in proposals:
            p.state = ProposalState.CANDIDATE

        # Separate skill proposals
        skill_proposals = [p for p in proposals if p.target_type == ProposalTargetType.SKILL]
        other_proposals = [p for p in proposals if p.target_type != ProposalTargetType.SKILL]

        # Activate top skill proposals
        skill_proposals.sort(
            key=lambda p: float(p.metadata.get("max_score", 0.6)),
            reverse=True,
        )
        for p in skill_proposals[: self._max_skill_proposals]:
            p.state = ProposalState.ACTIVE
        for p in skill_proposals[self._max_skill_proposals :]:
            p.state = ProposalState.CANDIDATE

        # Combine and apply total cap
        all_proposals = skill_proposals + other_proposals
        if len(all_proposals) > self._max_proposals:
            # Keep active ones first
            all_proposals.sort(
                key=lambda p: (
                    0 if p.state == ProposalState.ACTIVE else 1,
                    -float(p.metadata.get("max_score", 0.6)),
                )
            )
            for p in all_proposals[self._max_proposals :]:
                p.state = ProposalState.CANDIDATE

        return [p for p in all_proposals if p.state == ProposalState.ACTIVE]

    @staticmethod
    def _parse_llm_json(text: str) -> dict:
        """Extract JSON from LLM response, handling markdown fences."""
        # Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Markdown json blocks
        m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # Plain code blocks
        m = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # Braces
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning("AheProposer: failed to parse JSON from LLM response")
        return {"proposals": []}

    async def _init_model(self):
        """Initialize openjiuwen Model from config."""
        try:
            from openjiuwen.core.foundation.llm import (
                Model, ModelClientConfig, ModelRequestConfig,
            )
            from jiuwenswarm.common.config import get_default_models
            from jiuwenswarm.common.utils import get_env_file

            env_file = get_env_file()
            if env_file.exists():
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=env_file, override=False)

            models = get_default_models()
            if not models:
                return None

            first = models[0]
            client_cfg = first.get("model_client_config", {})
            model_cfg = first.get("model_config_obj", {})

            return Model(
                model_client_config=ModelClientConfig(
                    client_provider=client_cfg.get("client_provider", "OpenAI"),
                    api_base=client_cfg.get("api_base", ""),
                    api_key=client_cfg.get("api_key", ""),
                    verify_ssl=client_cfg.get("verify_ssl", False),
                ),
                model_config=ModelRequestConfig(
                    model=client_cfg.get("model_name", "gpt-4"),
                    temperature=model_cfg.get("temperature", 0.4),
                    max_tokens=4000,
                ),
            )
        except Exception as exc:
            logger.warning("AheProposer._init_model failed: %s", exc)
            return None
