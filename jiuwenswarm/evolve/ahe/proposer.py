# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""AheProposer — self-contained CLEAN→EVAL→DIAG→GOV→PROPOSE pipeline.

Pluggable algorithm: zero code overlap with LLMProposer. Only shared
contract is ProposalGenerator.generate(batch) -> list[Proposal].
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from jiuwenswarm.evolve.models import (
    Proposal,
    ProposalState,
    ProposalTargetType,
    EvidenceRef,
    TraceBatch,
)
from jiuwenswarm.evolve.proposal_generators.base import ProposalGenerator
from jiuwenswarm.evolve.registry import proposal_generators
from jiuwenswarm.evolve.ahe.otel_adapter import OtelTraceAdapter
from jiuwenswarm.evolve.ahe.evaluator import TraceOutcomeEvaluator
from jiuwenswarm.evolve.ahe.diagnosis.agent import DiagnosisAgent
from jiuwenswarm.evolve.ahe.proposer_prompts import AHE_PROPOSER_SYSTEM_PROMPT
from jiuwenswarm.evolve.ahe.timing_stats import get_timing_stats, reset_timing_stats

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
        self._diagnosis_agent = None

    @property
    def _otel_adapter(self) -> OtelTraceAdapter:
        if self._adapter is None:
            self._adapter = OtelTraceAdapter(db_path=self._traces_db_path)
        return self._adapter

    def _is_editable_skill(self, skill_name: str) -> bool:
        """True if the skill exists in the user workspace and is not builtin.

        Inlined safety guard (no separate governor). Builtin/system skills are
        protected; only existing user skills may be evolved.
        """
        try:
            from jiuwenswarm.common.utils import get_builtin_skills_dir

            builtin_dir = get_builtin_skills_dir()
            if builtin_dir.exists():
                builtins = {i.name for i in builtin_dir.iterdir() if i.is_dir()}
                if skill_name in builtins:
                    return False
        except Exception as exc:
            logger.warning("AheProposer._is_editable_skill builtin check failed: %s", exc)

        if not self._skills_dir:
            return False
        return (Path(self._skills_dir) / skill_name).is_dir()

    def _editable_skill_names(self) -> set[str]:
        """Names of user skills that exist in the workspace and are not builtin."""
        if not self._skills_dir:
            return set()
        skills_dir = Path(self._skills_dir)
        if not skills_dir.exists():
            return set()
        return {
            item.name
            for item in skills_dir.iterdir()
            if item.is_dir() and self._is_editable_skill(item.name)
        }

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
        # Initialize timing stats for this run
        stats = reset_timing_stats()
        trace_ids = list(batch.trace_ids)

        if not trace_ids:
            return []

        # Start overall timing
        stats.start_run(len(trace_ids))
        logger.info("AheProposer: processing %d traces in batches", len(trace_ids))

        # ── Batch Processing ──
        batch_size = 10  # Max traces per batch
        all_proposals = []

        total_batches = (len(trace_ids) - 1) // batch_size + 1
        stats.total_batches = total_batches

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
                stats.proposals_per_batch.append(len(proposals))
                logger.info(
                    "Batch %d/%d completed: %d proposals (total so far: %d)",
                    batch_num, total_batches, len(proposals), len(all_proposals)
                )
            except Exception as exc:
                logger.warning(
                    "Batch %d/%d failed: %s. Continuing with next batch.",
                    batch_num, total_batches, exc
                )
                stats.proposals_per_batch.append(0)

        # ── Global Limits ──
        final_proposals = self._enforce_limits(all_proposals)

        # End timing and generate report
        stats.end_run(len(final_proposals))
        timing_report = stats.generate_report()
        logger.info("\n%s", timing_report)

        logger.info(
            "AheProposer: completed %d batches, %d traces processed, %d proposals generated",
            total_batches, len(trace_ids), len(final_proposals)
        )

        return final_proposals

    def _enforce_limits(self, proposals: list[Proposal]) -> list[Proposal]:
        """Enforce max_proposals limit on final proposal list.

        Args:
            proposals: All proposals generated across batches

        Returns:
            Limited list of proposals (at most max_proposals)
        """
        if len(proposals) <= self._max_proposals:
            return proposals

        logger.info(
            "Enforcing proposal limit: %d proposals generated, keeping top %d",
            len(proposals), self._max_proposals
        )
        # Keep first max_proposals (they're already ordered by priority in generation)
        return proposals[:self._max_proposals]

    async def _process_batch_traces(
        self, trace_ids: list[str], batch_id: str
    ) -> list[Proposal]:
        """Execute complete flow for a single batch of traces.

        Flow: LOAD → CLEAN → EVAL → failed逐个 DIAG/GOV/PROPOSE
        """
        stats = get_timing_stats()

        # ── Step 2: CLEAN (batch level) ──
        stats.start_stage("CLEAN")
        normalized_traces = []
        clean_failures = []  # 收集失败的详细信息

        for tid in trace_ids:
            try:
                trace_dict = self._otel_adapter.convert_trace(tid)
                trace_dict["trace_id"] = tid
                normalized_traces.append(trace_dict)
            except Exception as exc:
                # 记录详细的失败信息
                failure_info = {
                    "trace_id": tid,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "suggestion": self._suggest_fix_for_clean_error(exc),
                }
                clean_failures.append(failure_info)
                logger.warning(
                    "CLEAN failed for trace %s: %s (%s). Suggestion: %s",
                    tid[:16],
                    type(exc).__name__,
                    str(exc),
                    failure_info["suggestion"],
                )

        stats.traces_normalized = len(normalized_traces)
        stats.end_stage("CLEAN", trace_count=len(normalized_traces))

        # 打印 CLEAN 阶段的总结
        if clean_failures:
            logger.warning(
                "CLEAN stage: %d/%d traces failed to normalize",
                len(clean_failures), len(trace_ids)
            )
            # 按 error_type 分类统计
            error_type_counts = {}
            for failure in clean_failures:
                error_type = failure["error_type"]
                error_type_counts[error_type] = error_type_counts.get(error_type, 0) + 1

            for error_type, count in error_type_counts.items():
                logger.warning("  - %s: %d traces", error_type, count)

        if not normalized_traces:
            logger.warning("Batch: no traces could be normalized (all failed in CLEAN stage)")
            logger.warning("Possible causes:")
            logger.warning("  1. Traces are system requests (session.delete, heartbeat, etc.) - should be filtered")
            logger.warning("  2. Traces have incomplete data (missing LLM spans, no messages)")
            logger.warning("  3. Database schema mismatch or corruption")
            logger.warning("Check benchmark --filter-dialog option to exclude system traces")
            return []

        # ── Step 2.5: FILTER - 过滤空 input/output 的 trace ──
        stats.start_stage("FILTER")
        skipped_traces = []
        eval_traces = []

        for trace_dict in normalized_traces:
            trace_id = trace_dict.get("trace_id", "unknown")
            task_name = trace_dict.get("task_name", "")

            # 检查 input/output 是否为空
            from jiuwenswarm.evolve.ahe.evaluator import TraceOutcomeEvaluator
            input_text = TraceOutcomeEvaluator._extract_input(trace_dict)
            output_text = TraceOutcomeEvaluator._extract_output(trace_dict)

            # 判断是否为空（空字符串、空对象、或只有空白字符）
            is_empty_input = not input_text or input_text.strip() in ("", "{}", "[]")
            is_empty_output = not output_text or output_text.strip() in ("", "{}", "[]")

            if is_empty_input or is_empty_output:
                skipped_traces.append(trace_dict)
                # 打印详细信息，方便人工发现 trace 采集错误
                logger.info(
                    "Skip trace %s: task_name=%s\n  input=%s\n  output=%s",
                    trace_id,
                    task_name,
                    str(input_text)[:500] if input_text else "(empty)",
                    str(output_text)[:500] if output_text else "(empty)",
                )
                continue

            # 通过过滤，进入 EVAL 阶段
            eval_traces.append(trace_dict)

        stats.traces_filtered = len(skipped_traces)
        stats.end_stage("FILTER", trace_count=len(eval_traces))

        # 打印过滤统计
        if skipped_traces:
            logger.info(
                "FILTER stage: skipped %d traces with empty input/output",
                len(skipped_traces)
            )

        if not eval_traces:
            logger.warning("Batch: all traces filtered (empty input/output)")
            logger.warning("  - Total normalized: %d, skipped: %d",
                          len(normalized_traces), len(skipped_traces))
            return []

        logger.info(
            "Batch: %d traces will be evaluated (skipped %d empty traces)",
            len(eval_traces), len(skipped_traces)
        )

        # ── Step 3: EVAL ──
        stats.start_stage("EVAL")
        outcomes = await self._eval.evaluate_batch(eval_traces)
        failed = [
            (nt, oc) for nt, oc in zip(eval_traces, outcomes)
            if oc.outcome in ("fail", "uncertain", "pass")
        ]
        stats.traces_evaluated = len(eval_traces)
        stats.traces_failed = len(failed)
        stats.end_stage("EVAL", trace_count=len(eval_traces))
        logger.info("evaluate result is %s", outcomes)
        if not failed:
            logger.info("Batch: no fail/uncertain traces with LLM interaction")
            return []

        logger.info(
            "Batch: %d of %d LLM traces are fail/uncertain",
            len(failed), len(eval_traces)
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
                    "Failed to diagnose trace %s: %s. trace_debug=%s. "
                    "Continuing with next.",
                    trace_id,
                    exc,
                    self._build_trace_debug_summary(nt),
                    exc_info=True,
                )
        logger.info("proposal is %s", batch_proposals)
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
        stats = get_timing_stats()
        trace_id = normalized_trace.get("trace_id", "unknown")

        # ── Step 4: DIAG (single trace) ──
        stats.start_stage("DIAG")
        diag_start = time.time()
        diagnosis_result = await self._diag.run(
            trace_ids=[trace_id],
            normalized_traces=[normalized_trace],
            mode="diagnose",
            question="Analyze this trace for root causes of task failure.",
        )
        diag_duration = time.time() - diag_start
        stats.end_stage("DIAG", trace_count=1)
        stats.add_stage_detail("DIAG", trace_id, diag_duration)
        logger.info("diagnosis result is %s", diagnosis_result)
        if diagnosis_result.budget_exceeded:
            logger.warning("DiagnosisAgent budget exceeded for trace %s", trace_id)

        # ── Step 5: GOV (extract skill names) ──
        stats.start_stage("GOV")
        gov_start = time.time()
        skill_names = self._extract_skill_names(
            diagnosis_result, [(normalized_trace, outcome)]
        )

        if not skill_names:
            logger.info("No editable skills found for trace %s", trace_id)
            gov_duration = time.time() - gov_start
            stats.end_stage("GOV", trace_count=0)
            stats.add_stage_detail("GOV", trace_id, gov_duration, {"skills_found": 0})
            return []

        gov_duration = time.time() - gov_start
        stats.end_stage("GOV", trace_count=1)
        stats.add_stage_detail("GOV", trace_id, gov_duration, {"skills_found": len(skill_names)})

        logger.info("Editable skills for trace %s: %s", trace_id, skill_names)

        # ── Step 6: PROPOSE (generate proposals) ──
        stats.start_stage("PROPOSE")
        propose_start = time.time()
        proposals_raw = await self._call_llm_propose(
            failed_traces=[(normalized_trace, outcome)],
            diagnosis_result=diagnosis_result,
            batch_id=batch_id,
        )

        proposals = self._parse_proposals(proposals_raw, batch_id)
        propose_duration = time.time() - propose_start
        stats.traces_diagnosed += 1
        stats.end_stage("PROPOSE", trace_count=1)
        stats.add_stage_detail("PROPOSE", trace_id, propose_duration, {"proposals": len(proposals)})

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
                msg = self._coerce_dict(msg)
                if not msg:
                    continue
                tool_calls = msg.get("tool_calls", [])
                if isinstance(tool_calls, str):
                    parsed_tool_calls = self._parse_json_if_possible(tool_calls)
                    tool_calls = parsed_tool_calls if isinstance(parsed_tool_calls, list) else []
                elif isinstance(tool_calls, dict):
                    tool_calls = [tool_calls]
                elif not isinstance(tool_calls, list):
                    tool_calls = []

                for tc in tool_calls:
                    tc = self._coerce_dict(tc)
                    if not tc:
                        continue
                    tool_name = self._tool_call_name(tc)
                    input_data = self._tool_call_arguments(tc)

                    # Check if this is a skill_tool call
                    if tool_name == "skill_tool" or "skill" in tool_name.lower():
                        # Extract skill_name from arguments
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
                    if tool_name == "bash":
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
        editable_skills = self._editable_skill_names()

        valid_names = set()
        rejected_names = set()

        for name in raw_names:
            if self._is_editable_skill(name):
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

    @staticmethod
    def _build_trace_debug_summary(normalized_trace: dict) -> dict:
        """Build a compact schema summary for diagnosing malformed trace data."""
        messages = normalized_trace.get("messages", [])
        summary: dict[str, Any] = {
            "trace_id": normalized_trace.get("trace_id") or normalized_trace.get("id"),
            "messages_type": type(messages).__name__,
            "messages_count": len(messages) if isinstance(messages, list) else None,
            "message_samples": [],
        }
        if not isinstance(messages, list):
            return summary

        samples = []
        for msg in messages[:8]:
            item: dict[str, Any] = {"message_type": type(msg).__name__}
            if isinstance(msg, dict):
                tool_calls = msg.get("tool_calls")
                item["role"] = msg.get("role")
                item["tool_calls_type"] = type(tool_calls).__name__
                if isinstance(tool_calls, list):
                    item["tool_calls_count"] = len(tool_calls)
                    call_samples = []
                    for tc in tool_calls[:3]:
                        call_item: dict[str, Any] = {"type": type(tc).__name__}
                        if isinstance(tc, dict):
                            func = tc.get("function")
                            call_item["name"] = tc.get("name")
                            call_item["input_type"] = type(tc.get("input")).__name__
                            call_item["arguments_type"] = type(tc.get("arguments")).__name__
                            call_item["function_type"] = type(func).__name__
                            if isinstance(func, dict):
                                call_item["function_name"] = func.get("name")
                                call_item["function_arguments_type"] = type(
                                    func.get("arguments")
                                ).__name__
                                call_item["function_args_dict_type"] = type(
                                    func.get("_arguments_dict")
                                ).__name__
                        call_samples.append(call_item)
                    item["tool_call_samples"] = call_samples
            samples.append(item)
        summary["message_samples"] = samples
        return summary

    @staticmethod
    def _coerce_dict(value: Any) -> dict:
        """Return a dict for dict or JSON-object string values."""
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = AheProposer._parse_json_if_possible(value)
            if isinstance(parsed, dict):
                return parsed
        return {}

    @staticmethod
    def _parse_json_if_possible(value: str) -> Any:
        """Parse JSON strings; return original string on parse failure."""
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    @staticmethod
    def _tool_call_name(tool_call: dict) -> str:
        """Extract tool name from Anthropic-style or OpenAI-style tool call."""
        name = tool_call.get("name")
        if isinstance(name, str):
            return name
        func = tool_call.get("function")
        if isinstance(func, dict):
            func_name = func.get("name")
            if isinstance(func_name, str):
                return func_name
        return ""

    @staticmethod
    def _tool_call_arguments(tool_call: dict) -> Any:
        """Extract parsed tool arguments from common tool call formats."""
        if "input" in tool_call:
            return tool_call.get("input")
        if "arguments" in tool_call:
            arguments = tool_call.get("arguments")
            if isinstance(arguments, str):
                return AheProposer._parse_json_if_possible(arguments)
            return arguments

        func = tool_call.get("function")
        if not isinstance(func, dict):
            return {}

        args_dict = func.get("_arguments_dict")
        if isinstance(args_dict, dict):
            return args_dict
        if isinstance(args_dict, str):
            parsed_args = AheProposer._parse_json_if_possible(args_dict)
            if isinstance(parsed_args, dict):
                return parsed_args

        arguments = func.get("arguments", {})
        if isinstance(arguments, str):
            return AheProposer._parse_json_if_possible(arguments)
        return arguments

    async def _call_llm_propose(
        self,
        failed_traces: list[tuple],
        diagnosis_result: Any,
        batch_id: str,
    ) -> list[dict]:
        """Call LLM to generate proposals from accumulated context.

        Uses OpenAI SDK directly (no openjiuwen dependency), same pattern as DiagnosisAgent.
        """

        # Build context
        trace_summaries = self._build_trace_summaries(failed_traces)
        diag_summary = self._build_diagnosis_summary(diagnosis_result)

        user_content = (
            "## Trace Evaluation Results\n"
            + json.dumps(trace_summaries, ensure_ascii=False, indent=2)
            + "\n\n## Diagnosis Results\n"
            + diag_summary
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
            if hasattr(response, 'choices') and response.choices:
                # OpenAI SDK response format with valid choices
                content = response.choices[0].message.content or ""
            else:
                # Handle empty choices or fallback formats
                logger.warning(
                    "AheProposer: API response has no choices, treating as empty. "
                    "Response type: %s",
                    type(response).__name__,
                )
                content = response.content if hasattr(response, "content") else ""

            # Parse JSON response
            parsed = self._parse_llm_json(str(content))
            proposals = parsed.get("proposals", [])
            if not proposals:
                no_proposal_reason = self._extract_no_proposal_reason(parsed)
                logger.info(
                    "AheProposer: LLM returned 0 proposals. reason=%r "
                    "category=%r trace_ids=%s diagnosis_issues=%s "
                    "response_preview=%r",
                    no_proposal_reason,
                    parsed.get("no_proposal_category", ""),
                    [t.get("trace_id", "unknown") for t in trace_summaries],
                    self._summarize_diagnosis_issues_for_log(diagnosis_result),
                    str(content)[:2000].replace("\n", "\\n"),
                )
            return proposals

        except Exception as exc:
            logger.warning(
                "AheProposer._call_llm_propose failed: %s",
                exc,
                exc_info=True,
            )
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
    def _extract_no_proposal_reason(parsed: dict) -> str:
        """Extract a useful no-proposal reason from parsed LLM output."""
        for key in (
            "no_proposal_reason",
            "reason",
            "rationale",
            "explanation",
            "message",
        ):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return (
            "LLM returned an empty proposals array without an explicit reason. "
            "Check response_preview and diagnosis context."
        )

    @staticmethod
    def _summarize_diagnosis_issues_for_log(diag_result: Any) -> list[dict]:
        """Build compact diagnosis issue summary for logs."""
        items = []
        for issue in getattr(diag_result, "issues", []) or []:
            items.append({
                "issue_type": getattr(issue, "issue_type", ""),
                "summary": str(getattr(issue, "summary", ""))[:300],
                "root_cause": str(getattr(issue, "root_cause", ""))[:300],
                "suggested_fix": str(getattr(issue, "suggested_fix", ""))[:300],
            })
        return items

    def _parse_proposals(
        self, raw_proposals: list[dict], batch_id: str
    ) -> list[Proposal]:
        """Parse raw dicts from LLM into Proposal objects."""
        proposals = []
        for index, raw in enumerate(raw_proposals):
            try:
                # Build failure evidence
                evidence = self._parse_evidence_refs(
                    raw.get("failure_evidence", []),
                    proposal_index=index,
                    context="failure_evidence",
                )

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
                    },
                )
                proposals.append(prop)
            except Exception as exc:
                logger.warning(
                    "AheProposer: failed to parse proposal index=%s: %s; raw_preview=%s",
                    index,
                    exc,
                    str(raw)[:2000],
                    exc_info=True,
                )
        return proposals

    @staticmethod
    def _parse_evidence_refs(
        raw_refs: Any,
        proposal_index: int | None = None,
        context: str = "evidence_refs",
    ) -> list[EvidenceRef]:
        """Parse LLM evidence refs while tolerating diagnosis-only fields.

        Diagnosis issues often use ``span_index``. ``EvidenceRef`` stores a
        trace pointer, so we map it into ``field_path`` instead of letting
        Pydantic reject the whole proposal because of one extra key.
        """
        if not isinstance(raw_refs, list):
            logger.warning(
                "AheProposer: expected list for %s, got %s; proposal_index=%s",
                context,
                type(raw_refs).__name__,
                proposal_index,
            )
            return []

        refs: list[EvidenceRef] = []
        for ref_index, raw_ref in enumerate(raw_refs):
            try:
                normalized = AheProposer._normalize_evidence_ref(raw_ref)
                if normalized is None:
                    continue
                refs.append(EvidenceRef(**normalized))
            except Exception as exc:
                logger.warning(
                    "AheProposer: failed to parse evidence ref "
                    "proposal_index=%s ref_index=%s context=%s: %s; raw_ref=%s",
                    proposal_index,
                    ref_index,
                    context,
                    exc,
                    str(raw_ref)[:1000],
                    exc_info=True,
                )
        return refs

    @staticmethod
    def _normalize_evidence_ref(raw_ref: Any) -> dict[str, Any] | None:
        """Normalize permissive LLM evidence payloads into EvidenceRef fields."""
        if isinstance(raw_ref, str):
            text = raw_ref.strip()
            if not text:
                return None
            return {
                "trace_id": "unknown",
                "description": text,
            }

        if not isinstance(raw_ref, dict):
            return None

        trace_id = str(raw_ref.get("trace_id") or "").strip() or "unknown"
        description = str(
            raw_ref.get("description")
            or raw_ref.get("evidence")
            or raw_ref.get("summary")
            or ""
        ).strip()

        span_id = raw_ref.get("span_id")
        field_path = raw_ref.get("field_path")
        span_index = raw_ref.get("span_index")

        if not field_path and span_index is not None:
            field_path = f"spans[{span_index}]"

        if not description:
            if span_index is not None:
                description = f"Evidence from span_index={span_index}"
            elif field_path:
                description = f"Evidence from {field_path}"
            else:
                description = "Evidence from LLM proposal"

        normalized: dict[str, Any] = {
            "trace_id": trace_id,
            "description": description,
        }
        if span_id is not None:
            normalized["span_id"] = str(span_id)
        if field_path:
            normalized["field_path"] = str(field_path)
        return normalized

    def _suggest_fix_for_clean_error(self, exc: Exception) -> str:
        """根据 CLEAN 阶段的错误类型给出修复建议。

        Args:
            exc: 捕获的异常

        Returns:
            修复建议字符串
        """
        error_type = type(exc).__name__
        error_message = str(exc)

        # IndexError: list index out of range
        if error_type == "IndexError" and "index out of range" in error_message:
            return "Trace has no LLM spans (likely system request). Use --filter-dialog to exclude."

        # KeyError
        if error_type == "KeyError":
            return f"Missing required field: {error_message}. Check trace data completeness."

        # JSONDecodeError
        if "JSONDecodeError" in error_type or "json" in error_message.lower():
            return "Malformed JSON in trace attributes/events. Check telemetry data encoding."

        # AttributeError
        if error_type == "AttributeError":
            return f"Invalid attribute access: {error_message}. Check OTEL span schema."

        # ValueError
        if error_type == "ValueError":
            return f"Invalid value: {error_message}. Check trace data validation."

        # TypeError
        if error_type == "TypeError":
            return f"Type mismatch: {error_message}. Check trace data types."

        # 数据库相关错误
        if "sqlite" in error_message.lower() or "database" in error_message.lower():
            return "Database access error. Check traces.db path and permissions."

        # 兜底建议
        return "Unknown error type. Check trace structure and OTEL adapter implementation."

    @staticmethod
    def _parse_llm_json(text: str) -> dict:
        """Extract JSON from LLM response with diagnostics on failure."""
        raw_text = str(text or "")
        candidates = AheProposer._json_candidates(raw_text)
        last_error: json.JSONDecodeError | None = None

        for label, candidate in candidates:
            try:
                parsed = AheProposer._loads_json_candidate(candidate)
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.debug(
                    "AheProposer: JSON parse attempt failed (%s): %s",
                    label,
                    exc,
                    exc_info=True,
                )
                continue

            normalized = AheProposer._normalize_parsed_proposer_payload(parsed)
            if normalized is not None:
                return normalized

            logger.debug(
                "AheProposer: JSON parse attempt produced unsupported payload "
                "(%s): type=%s preview=%r",
                label,
                type(parsed).__name__,
                str(parsed)[:500],
            )

        preview = raw_text[:2000].replace("\n", "\\n")
        if last_error is not None:
            logger.warning(
                "AheProposer: failed to parse JSON from LLM response. "
                "last_error=%s response_preview=%r",
                last_error,
                preview,
                exc_info=(type(last_error), last_error, last_error.__traceback__),
            )
        else:
            logger.warning(
                "AheProposer: failed to parse JSON from LLM response. "
                "No JSON object/array candidate found. response_preview=%r",
                preview,
            )
        return {"proposals": []}

    @staticmethod
    def _json_candidates(text: str) -> list[tuple[str, str]]:
        """Return possible JSON payload substrings from an LLM response."""
        stripped = text.strip()
        candidates: list[tuple[str, str]] = []
        if stripped:
            candidates.append(("raw", stripped))

        for idx, match in enumerate(
            re.finditer(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
        ):
            block = match.group(1).strip()
            if block:
                candidates.append((f"code_fence_{idx}", block))

        decoder = json.JSONDecoder()
        for idx, pos in enumerate(
            i for i, ch in enumerate(text) if ch in "{["
        ):
            try:
                _, end = decoder.raw_decode(text[pos:])
            except json.JSONDecodeError:
                continue
            candidates.append((f"raw_decode_{idx}", text[pos:pos + end]))

        # Fallback: balanced extraction handles braces inside strings better than
        # a greedy regex when raw_decode cannot start at the right position.
        balanced = AheProposer._extract_balanced_json(text)
        if balanced:
            candidates.append(("balanced", balanced))

        seen = set()
        unique: list[tuple[str, str]] = []
        for label, candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            unique.append((label, candidate))
        return unique

    @staticmethod
    def _loads_json_candidate(candidate: str) -> Any:
        """Load candidate JSON, including one level of double encoding."""
        parsed = json.loads(candidate)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed

    @staticmethod
    def _normalize_parsed_proposer_payload(parsed: Any) -> dict | None:
        """Normalize parsed proposer payload to {'proposals': [...]}."""
        if isinstance(parsed, dict):
            proposals = parsed.get("proposals")
            if isinstance(proposals, list):
                return parsed
            # Some models return a single proposal object.
            if {"target_id", "target_type", "targeted_fix"} & set(parsed.keys()):
                return {"proposals": [parsed]}
        if isinstance(parsed, list):
            return {"proposals": parsed}
        return None

    @staticmethod
    def _extract_balanced_json(text: str) -> str | None:
        """Extract the first balanced JSON object/array from text."""
        start = None
        opening = ""
        for idx, ch in enumerate(text):
            if ch == "{":
                start, opening = idx, "{"
                break
            if ch == "[":
                start, opening = idx, "["
                break
        if start is None:
            return None

        stack = [opening]
        in_string = False
        escape = False
        for idx in range(start + 1, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch in "{[":
                stack.append(ch)
                continue
            if ch in "}]":
                expected = "}" if stack[-1] == "{" else "]"
                if ch != expected:
                    return None
                stack.pop()
                if not stack:
                    return text[start:idx + 1]
        return None

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

            # Create AsyncOpenAI client with timeout to prevent hanging
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=api_base,
                timeout=60.0,  # Add 60 second timeout
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
