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
    ExperienceOperationType,
    ExperienceOperation,
)
from jiuwenswarm.evolve.proposal_generators.base import ProposalGenerator
from jiuwenswarm.evolve.registry import proposal_generators
from jiuwenswarm.evolve.ahe.otel_adapter import OtelTraceAdapter
from jiuwenswarm.evolve.ahe.evaluator import TraceOutcomeEvaluator
from jiuwenswarm.evolve.ahe.experience_governor import ExperienceGovernor
from jiuwenswarm.evolve.ahe.diagnosis.agent import DiagnosisAgent
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

    def _normalized_trace_dicts(self, failed: list[tuple]) -> list[dict]:
        """Extract NormalizedTrace dicts from failed trace tuples."""
        return [nt for nt, _ in failed]

    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        """Execute the full PDA pipeline with batch processing.

        Strategy: Process traces in batches (max 10 per batch), each batch executes
        complete flow: LOAD → CLEAN → EVAL → failed逐个 DIAG/GOV/PROPOSE.

        Returns:
            List of Proposal objects (may be empty if nothing qualifies).
        """
        # ── Step 1: LOAD ──
        trace_ids = list(batch.trace_ids)
        if not trace_ids:
            return []

        logger.info("AheProposer: processing %d traces in batches", len(trace_ids))

        # ── Batch Processing ──
        batch_size = 10  # Max traces per batch
        all_proposals = []

        total_batches = (len(trace_ids) - 1) // batch_size + 1
        for i in range(0, len(trace_ids), batch_size):
            batch_trace_ids = trace_ids[i:i+batch_size]
            batch_num = i // batch_size + 1

            logger.info(
                "Processing batch %d/%d: %d traces",
                batch_num, total_batches, len(batch_trace_ids)
            )

            # Execute complete flow for current batch
            try:
                proposals = await self._process_batch_traces(
                    batch_trace_ids, batch.batch_id
                )
                all_proposals.extend(proposals)
                logger.info(
                    "Batch %d/%d completed: %d proposals (total so far: %d)",
                    batch_num, total_batches, len(proposals), len(all_proposals)
                )
            except Exception as exc:
                logger.warning(
                    "Batch %d/%d failed: %s. Continuing with next batch.",
                    batch_num, total_batches, exc
                )

        # ── Global Limits ──
        final_proposals = self._enforce_limits(all_proposals)

        logger.info(
            "AheProposer: completed %d batches, %d traces processed, %d proposals generated",
            total_batches, len(trace_ids), len(final_proposals)
        )

        return final_proposals

    async def _process_batch_traces(
        self, trace_ids: list[str], batch_id: str
    ) -> list[Proposal]:
        """Execute complete flow for a single batch of traces.

        Flow: LOAD → CLEAN → EVAL → failed逐个 DIAG/GOV/PROPOSE
        """
        # ── Step 2: CLEAN (batch level) ──
        normalized_traces = []
        for tid in trace_ids:
            try:
                trace_dict = self._otel_adapter.convert_trace(tid)
                trace_dict["trace_id"] = tid
                normalized_traces.append(trace_dict)
            except Exception as exc:
                logger.warning("CLEAN failed for %s: %s", tid, exc)

        if not normalized_traces:
            logger.info("Batch: no traces could be normalized")
            return []

        logger.info("Batch: normalized %d traces", len(normalized_traces))

        # ── Step 3: EVAL (batch level) ──
        outcomes = await self._eval.evaluate_batch(normalized_traces)
        failed = [
            (nt, oc) for nt, oc in zip(normalized_traces, outcomes)
            if oc.outcome in ("fail", "uncertain")
        ]
        logger.info("evaluate result is %s", outcomes)
        if not failed:
            logger.info("Batch: no fail/uncertain traces")
            return []

        logger.info(
            "Batch: %d of %d traces are fail/uncertain",
            len(failed), len(normalized_traces)
        )

        # ── Step 4-6: Individual DIAG/GOV/PROPOSE for each failed trace ──
        batch_proposals = []
        for nt, oc in failed:
            try:
                proposals = await self._diagnose_single_trace(nt, oc, batch_id)
                batch_proposals.extend(proposals)
            except Exception as exc:
                trace_id = nt.get("trace_id", "unknown")
                logger.warning(
                    "Failed to diagnose trace %s: %s. Continuing with next.",
                    trace_id, exc
                )

        return batch_proposals

    async def _diagnose_single_trace(
        self, normalized_trace: dict, outcome: Any, batch_id: str
    ) -> list[Proposal]:
        """Execute DIAG → GOV → PROPOSE for a single trace.

        Args:
            normalized_trace: Normalized trace dict
            outcome: TraceOutcome object
            batch_id: Batch ID for metadata

        Returns:
            List of Proposal objects for this trace
        """
        trace_id = normalized_trace.get("trace_id", "unknown")

        # ── Step 4: DIAG (single trace) ──
        diagnosis_result = await self._diag.run(
            trace_ids=[trace_id],
            normalized_traces=[normalized_trace],
            mode="diagnose",
            question="Analyze this trace for root causes of task failure.",
        )

        if diagnosis_result.budget_exceeded:
            logger.warning("DiagnosisAgent budget exceeded for trace %s", trace_id)

        # ── Step 5: GOV (extract skill names) ──
        skill_names = self._extract_skill_names(
            diagnosis_result, [(normalized_trace, outcome)]
        )

        if not skill_names:
            logger.info("No editable skills found for trace %s", trace_id)
            return []

        governance_contexts = {
            name: self._gov.get_context(name)
            for name in skill_names
        }

        logger.info(
            "Governance context for trace %s: %d skills",
            trace_id, len(skill_names)
        )

        # ── Step 6: PROPOSE (generate proposals) ──
        proposals_raw = await self._call_llm_propose(
            failed_traces=[(normalized_trace, outcome)],
            diagnosis_result=diagnosis_result,
            governance_contexts=governance_contexts,
            batch_id=batch_id,
        )

        proposals = self._parse_proposals(proposals_raw, batch_id)

        logger.info(
            "Generated %d proposals for trace %s, propose is %s",
            len(proposals), trace_id, proposals
        )

        return proposals

    def _extract_skill_names(
        self,
        diagnosis_result: Any,
        failed_traces: list[tuple],
    ) -> set[str]:
        """Extract skill names from trace data and diagnosis issues.

        IMPORTANT SAFETY CHECKS:
        1. Extract from trace data and diagnosis issues
        2. VALIDATE each skill name against user workspace
        3. Filter out skills that don't exist in user workspace
        4. Filter out builtin/system skills
        5. Return empty set if no valid skills found

        This prevents:
        - Hallucinated skill names from diagnosis_result
        - Fallback to "general" (which is protected)
        - Creating new skills that don't exist
        """
        raw_names = set()

        # 1. Direct extraction from NormalizedTrace tool_calls
        for nt, _ in failed_traces:
            messages = nt.get("messages", [])
            for msg in messages:
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    # Check if this is a skill_tool call
                    if tc.get("name") == "skill_tool" or "skill" in tc.get("name", "").lower():
                        # Extract skill_name from arguments
                        input_data = tc.get("input", {})
                        if isinstance(input_data, dict):
                            skill_name = input_data.get("skill_name")
                            if skill_name:
                                raw_names.add(skill_name)
                        # Or from input string (parse if needed)
                        elif isinstance(input_data, str) and "skill_name" in input_data:
                            import re as _re
                            m = _re.search(r'"skill_name"\s*["\']?\s*["\':]\s*["\']([^"\']+)["\']', input_data)
                            if m:
                                raw_names.add(m.group(1))

                    # NEW: Extract from bash tool calls (skill scripts invoked via bash)
                    # Many skills are called via bash, not skill_tool
                    if tc.get("name") == "bash":
                        input_data = tc.get("input", {})
                        if isinstance(input_data, dict):
                            # Extract from workdir: ".../skills/<skill-name>/"
                            workdir = input_data.get("workdir", "")
                            if workdir and "skills" in workdir:
                                import re as _re
                                # Pattern: .../skills/<skill-name>/ or ...\\skills\\<skill-name>\\
                                m = _re.search(r'[\/\\]skills[\/\\]([^\/\\]+)[\/\\]', workdir)
                                if m:
                                    skill_name = m.group(1)
                                    raw_names.add(skill_name)

                            # Extract from command: ".../skills/<skill-name>/scripts/..."
                            command = input_data.get("command", "")
                            if command and "skills" in command:
                                import re as _re
                                # Pattern: .../skills/<skill-name>/scripts/ or ...\\skills\\<skill-name>\\scripts\\
                                m = _re.search(r'[\/\\]skills[\/\\]([^\/\\]+)[\/\\]scripts[\/\\]', command)
                                if m:
                                    skill_name = m.group(1)
                                    raw_names.add(skill_name)

                        elif isinstance(input_data, str):
                            # Fallback: try to extract from string input
                            import re as _re
                            # Pattern 1: workdir
                            m = _re.search(r'workdir["\']?\s*[:=]\s*["\'](?:[^"\']*)[\/\\]skills[\/\\]([^\/\\]+)[\/\\]', input_data)
                            if m:
                                raw_names.add(m.group(1))
                            # Pattern 2: command with skills path
                            m = _re.search(r'[\/\\]skills[\/\\]([^\/\\]+)[\/\\]scripts[\/\\]', input_data)
                            if m:
                                raw_names.add(m.group(1))

                    # Also check tool_calls in assistant message format
                    # (from NormalizedTrace messages)
                    func_data = tc.get("function", {})
                    if func_data.get("name") == "bash":
                        # Try _arguments_dict first (pre-parsed dict)
                        args_dict = func_data.get("_arguments_dict", {})
                        if args_dict:
                            # Extract from workdir or command in args_dict
                            workdir = args_dict.get("workdir", "")
                            if workdir and "skills" in workdir:
                                import re as _re
                                m = _re.search(r'[\/\\]skills[\/\\]([^\/\\]+)[\/\\]', workdir)
                                if m:
                                    raw_names.add(m.group(1))

                            command = args_dict.get("command", "")
                            if command and "skills" in command:
                                import re as _re
                                m = _re.search(r'[\/\\]skills[\/\\]([^\/\\]+)[\/\\]scripts[\/\\]', command)
                                if m:
                                    raw_names.add(m.group(1))

        # 2. Extraction from diagnosis issues (fallback)
        if not raw_names and diagnosis_result and diagnosis_result.issues:
            for issue in diagnosis_result.issues:
                # Priority: check root_cause and suggested_fix for skill_name
                text_fields = [
                    issue.root_cause or "",
                    issue.suggested_fix or "",
                    issue.summary or "",
                    issue.evidence or "",
                ]
                combined_text = " ".join(text_fields)

                # Look for skill-related keywords
                if "skill" in combined_text.lower():
                    import re as _re

                    # Pattern 1: "skill_name=xxx:" (explicit marker from DiagnosisAgent)
                    m = _re.search(r'skill_name\s*=\s*([a-zA-Z0-9_-]+)', combined_text)
                    if m:
                        raw_names.add(m.group(1))
                        continue

                    # Pattern 2: "skill xxx 存在缺陷" or "skill xxx 有问题"
                    m = _re.search(r'skill\s+([a-zA-Z0-9_-]+)\s+(?:存在|有)', combined_text)
                    if m:
                        raw_names.add(m.group(1))
                        continue

                    # Pattern 3: "修复 skill xxx" or "修改 skill xxx"
                    m = _re.search(r'(?:修复|修改|添加)\s+skill\s+([a-zA-Z0-9_-]+)', combined_text)
                    if m:
                        raw_names.add(m.group(1))
                        continue

                    # Pattern 4: "csv-row-counter skill" or similar patterns
                    m = _re.search(r'([a-zA-Z0-9_-]+)\s+skill', combined_text)
                    if m:
                        raw_names.add(m.group(1))
                        continue

                    # Pattern 5: skill_tool 调用
                    m = _re.search(r'skill_tool(?:\s*调用)?(?:\s*[的的是])?\s*["\']?([a-zA-Z0-9_-]+)', combined_text)
                    if m:
                        raw_names.add(m.group(1))
                        continue

        logger.info("Raw extracted skill names (before validation): %s", raw_names)

        # 3. VALIDATE: Filter against user editable skills
        # Get the list of skills that actually exist in user workspace
        editable_skills = self._gov.get_user_skill_names()

        valid_names = set()
        rejected_names = set()

        for name in raw_names:
            if self._gov.is_skill_editable(name):
                valid_names.add(name)
            else:
                rejected_names.add(name)

        # Log warnings for rejected names
        if rejected_names:
            logger.warning(
                "AheProposer: rejected skill names (not editable or doesn't exist): %s. "
                "This might be hallucination from diagnosis_result. "
                "Editable skills are: %s",
                rejected_names,
                editable_skills,
            )

        # Log result
        logger.info(
            "Validated skill names: %s (editable_skills=%s)",
            valid_names,
            editable_skills,
        )

        return valid_names

    async def _call_llm_propose(
        self,
        failed_traces: list[tuple],
        diagnosis_result: Any,
        governance_contexts: dict[str, Any],
        batch_id: str,
    ) -> list[dict]:
        """Call LLM to generate proposals from accumulated context.

        Uses OpenAI SDK directly (no openjiuwen dependency), same pattern as DiagnosisAgent.
        """

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

        # Initialize model if needed (same pattern as DiagnosisAgent)
        model = self._model
        if model is None:
            model = await self._init_model()
        if model is None:
            logger.warning("AheProposer: no model available")
            return []

        try:
            # Build messages in OpenAI dict format (no openjiuwen dependency)
            messages = [
                {"role": "system", "content": AHE_PROPOSER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]

            # Call model directly (no tools for proposal generation)
            response = await model.invoke(messages=messages)

            # Extract content
            if hasattr(response, 'choices'):
                # OpenAI SDK response format
                content = response.choices[0].message.content or ""
            else:
                # Fallback for other formats
                content = response.content if hasattr(response, "content") else str(response)

            # Parse JSON response
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
        """Initialize OpenAI Model from config (same pattern as DiagnosisAgent).

        No dependency on openjiuwen - uses OpenAI SDK directly.
        """
        try:
            from openai import AsyncOpenAI
            from jiuwenswarm.evolve import get_evolve_config
            from jiuwenswarm.evolve.ahe.openai_wrapper import OpenAIModelWrapper
            import os

            evolve_cfg = get_evolve_config()
            llm_cfg = evolve_cfg.get("llm", {})

            # Expand environment variables in api_key
            api_key = llm_cfg.get("api_key")
            # Check if api_key is valid string before calling startswith
            if api_key and isinstance(api_key, str):
                if api_key.startswith("${") and api_key.endswith("}"):
                    env_var = api_key[2:-1]
                    api_key = os.getenv(env_var)
            else:
                # api_key is None or not a string
                api_key = os.getenv("EVOLVE_API_KEY")  # Fallback: try direct env var

            if not api_key:
                logger.warning("AheProposer: no API key configured in evolve/config.yaml or environment")
                return None

            # Get api_base (with proper fallback for DeepSeek)
            api_base = llm_cfg.get("api_base")
            # Check if api_base is a valid non-empty string
            if not api_base or not isinstance(api_base, str) or api_base.strip() == "":
                # Use DeepSeek default endpoint if not configured
                api_base = "https://api.deepseek.com/v1"
                logger.info("AheProposer: api_base not configured, using DeepSeek default: %s", api_base)
            else:
                logger.info("AheProposer: using configured api_base: %s", api_base)

            # Create AsyncOpenAI client
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=api_base,
            )

            # Return wrapper that matches expected interface
            return OpenAIModelWrapper(
                client=client,
                model=llm_cfg.get("model_name", "deepseek-v4-pro"),
                temperature=llm_cfg.get("temperature", 0.1),
                max_tokens=llm_cfg.get("max_tokens", 2000),
            )
        except Exception as exc:
            logger.warning("AheProposer._init_model failed: %s", exc)
            return None

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
