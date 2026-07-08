# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""AheDecisionPolicy — RuleGate + LLMDecision two-phase judgment.

Pluggable: zero code overlap with RulePolicy or EvalPolicy. Only shared
contract is DecisionPolicy.evaluate(proposal) -> DecisionResult.

Phase 1 (RuleGate): hard constraint checks (field completeness, target_type,
    governance, duplicate detection). Fails fast with blocking=True.
Phase 2 (LLMDecision): semantic judgment (consistency, reasonability, risk).
    Requires openjiuwen Model.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jiuwenswarm.evolve.models import (
    DecisionResult,
    DecisionSuggestion,
    Proposal,
    ProposalTargetType,
    ExperienceOperation,
)
from jiuwenswarm.evolve.decision_policies.base import DecisionPolicy
from jiuwenswarm.evolve.registry import decision_policies
from jiuwenswarm.evolve.ahe.experience_governor import ExperienceGovernor

logger = logging.getLogger(__name__)

# ── LLM Decision System Prompt ───────────────────────────────────────────

LLM_DECISION_SYSTEM_PROMPT = """你是一名智能体演进决策专家。判断以下 Proposal 是否应该被接受。

## 评估维度

1. **一致性**: Proposal 的 root_cause 是否与诊断结果中的证据一致？
2. **合理性**: targeted_fix 能否有效解决 root_cause 描述的问题？
3. **可信度**: predicted_impact 是否可信（不夸大、有依据）？
4. **风险**: risk 是否可接受（不会导致功能回退或用户体验下降）？
5. **治理合规**: 操作类型（operations）是否适合当前治理上下文？
6. **经验去重**: proposed new_content 是否已被 existing/similar experiences 覆盖？

## 经验去重规则

- 如果已有经验已经覆盖当前失败模式，不要接受新增同类经验；返回 rejected，并说明应 NOOP 或 MERGE 到已有经验。
- 如果 proposal 使用 ADD，但 new_content 只是对已有经验的同义改写，应返回 rejected。
- 如果 proposal 使用 MERGE/UPDATE，必须判断 target_experience_id 是否合理，且是否真的增加了新的 evidence 或更清晰的行为约束。
- 只有当 proposed new_content 提供了已有经验没有覆盖的新失败模式、新约束或更准确的行动准则时，才允许 active。

## 输出 JSON (必选)

{
  "score": 0.0-1.0,
  "suggestion": "candidate | active | rejected",
  "reason": "判定原因（中文），引用的关键证据"
}

说明:
- score >= 0.7: 推荐 active
- score 0.4-0.7: 推荐 candidate (需要更多验证)
- score < 0.4: 推荐 rejected
"""


@decision_policies.register("ahe_decision_policy")
class AheDecisionPolicy(DecisionPolicy):
    """AHE Decision — RuleGate + LLMDecision two-phase judgment.

    RuleGate fails fast on hard violations. LLMDecision provides
    semantic assessment.
    """

    def __init__(
        self,
        governor: ExperienceGovernor | None = None,
        model: Any | None = None,
    ) -> None:
        super().__init__(name="ahe_decision_policy", version="1.0")
        self._governor = governor or ExperienceGovernor()
        self._model = model

    async def evaluate(self, proposal: Proposal) -> DecisionResult:
        """Two-phase evaluation.

        Returns blocking DecisionResult immediately if RuleGate fails,
        otherwise runs LLMDecision.
        """
        # Phase 1: RuleGate
        rule_result = self._rule_gate(proposal)
        if rule_result.blocking:
            return rule_result

        # Phase 2: LLMDecision
        llm_result = await self._llm_decision(proposal)
        return llm_result

    def _rule_gate(self, proposal: Proposal) -> DecisionResult:
        """Hard constraint checks — any failure blocks the proposal."""
        failed_checks = []

        # 1. Field completeness
        if not proposal.failure_evidence:
            failed_checks.append("empty_failure_evidence")
        if not proposal.root_cause or not proposal.root_cause.strip():
            failed_checks.append("empty_root_cause")
        if proposal.targeted_fix is None or len(proposal.targeted_fix) == 0:
            failed_checks.append("empty_targeted_fix")
        if not proposal.predicted_impact or not proposal.predicted_impact.strip():
            failed_checks.append("empty_predicted_impact")

        # 2. target_type range (Phase 1: skill only)
        if proposal.target_type != ProposalTargetType.SKILL:
            failed_checks.append(f"unsupported_target_type_{proposal.target_type.value}")

        # 3. Experience governance validation
        operations_raw = proposal.metadata.get("operations", [])
        if operations_raw:
            for op_dict in operations_raw:
                try:
                    op = ExperienceOperation(**op_dict)
                    # Check target_id - must be a user skill, not system/builtin skill
                    target_skill = proposal.target_id
                    if not target_skill:
                        # No target_id means no specific skill - reject
                        failed_checks.append("no_target_skill")
                        continue
                    if not self._governor.validate_operation(target_skill, op):
                        failed_checks.append(f"governance_violation_{op.op.value}")
                except Exception as exc:
                    failed_checks.append(f"invalid_operation_{exc}")
        else:
            # No operations = legacy Proposal, treat as ADD for approval
            pass

        # 4. Duplicate detection
        if self._is_duplicate(proposal):
            failed_checks.append("duplicate_proposal")

        if failed_checks:
            return DecisionResult(
                decision_id=f"dec-rule-{proposal.proposal_id[:8]}",
                proposal_id=proposal.proposal_id,
                policy_name=self.name,
                policy_version=self.version,
                score=0.0,
                reason=f"RuleGate blocked: {', '.join(failed_checks)}",
                suggestion=DecisionSuggestion.REJECTED,
                blocking=True,
                failed_checks=failed_checks,
            )

        return DecisionResult(
            decision_id=f"dec-rulegate-{proposal.proposal_id[:8]}",
            proposal_id=proposal.proposal_id,
            policy_name=self.name,
            policy_version=self.version,
            score=0.5,
            reason="RuleGate passed",
            suggestion=DecisionSuggestion.CANDIDATE,
            blocking=False,
            failed_checks=[],
        )

    async def _llm_decision(self, proposal: Proposal) -> DecisionResult:
        """LLM semantic judgment."""
        if self._model is None:
            self._model = await self._init_model()

        if self._model is None:
            logger.warning("AheDecisionPolicy: no model for LLM decision, using RuleGate result")
            return DecisionResult(
                decision_id=f"dec-pass-{proposal.proposal_id[:8]}",
                proposal_id=proposal.proposal_id,
                policy_name=self.name,
                policy_version=self.version,
                score=0.5,
                reason="No LLM available, defaulting to CANDIDATE",
                suggestion=DecisionSuggestion.CANDIDATE,
                blocking=False,
            )

        # Build prompt
        user_content = (
            f"## Proposal\n"
            f"- target_type: {proposal.target_type.value}\n"
            f"- target_id: {proposal.target_id}\n"
            f"- root_cause: {proposal.root_cause}\n"
            f"- targeted_fix: {json.dumps(proposal.targeted_fix, ensure_ascii=False)}\n"
            f"- predicted_impact: {proposal.predicted_impact}\n"
            f"- risk: {proposal.risk}\n"
            f"- failure_evidence: {[e.model_dump() for e in proposal.failure_evidence]}\n"
            f"- operations: {json.dumps(proposal.metadata.get('operations', []), ensure_ascii=False)}\n"
        )

        # Add governance context for this skill
        target_skill = proposal.target_id
        if not target_skill:
            # No target skill - skip governance context (will be rejected by governance check)
            user_content += "\n## Governance Context\n- No target skill specified (will be rejected)\n"
        else:
            ctx = self._governor.get_context(
                target_skill,
                query_hint=self._build_proposal_similarity_hint(proposal),
            )
            user_content += (
                f"\n## Governance Context\n"
                f"- skill: {ctx.skill_name}\n"
                f"- existing experiences: {ctx.current_count}/{ctx.max_count}\n"
                f"- allowed operations: {[o.value for o in ctx.allowed_operations]}\n"
                f"- existing_experiences: {json.dumps(ctx.existing_experiences, ensure_ascii=False)}\n"
                f"- candidate_similar_experiences: {json.dumps(ctx.similar_experiences, ensure_ascii=False)}\n"
        )

        try:
            # Build messages in OpenAI dict format (no openjiuwen dependency)
            messages = [
                {"role": "system", "content": LLM_DECISION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]

            response = await self._model.invoke(messages=messages)

            # Extract content with defensive check
            if hasattr(response, 'choices') and response.choices:
                content = response.choices[0].message.content or ""
            else:
                logger.warning(
                    "AheDecisionPolicy: API response has no choices for decision "
                    "for proposal %s, treating as empty response",
                    proposal.proposal_id,
                )
                content = response.content if hasattr(response, "content") else ""

            parsed = self._parse_llm_json(str(content))
            score = float(parsed.get("score", 0.5))
            suggestion_str = parsed.get("suggestion", "candidate")
            reason = parsed.get("reason", "LLM decision")

            # Normalize suggestion string to enum
            suggestion = DecisionSuggestion.CANDIDATE
            if suggestion_str == "active":
                suggestion = DecisionSuggestion.ACTIVE
            elif suggestion_str == "rejected":
                suggestion = DecisionSuggestion.REJECTED

            return DecisionResult(
                decision_id=f"dec-llm-{proposal.proposal_id[:8]}",
                proposal_id=proposal.proposal_id,
                policy_name=self.name,
                policy_version=self.version,
                score=score,
                reason=f"LLMDecision: {reason}",
                suggestion=suggestion,
                blocking=False,
                failed_checks=[],
            )

        except Exception as exc:
            logger.warning("AheDecisionPolicy._llm_decision failed: %s", exc)
            return DecisionResult(
                decision_id=f"dec-fallback-{proposal.proposal_id[:8]}",
                proposal_id=proposal.proposal_id,
                policy_name=self.name,
                policy_version=self.version,
                score=0.5,
                reason=f"LLM decision failed, defaulting to CANDIDATE: {exc}",
                suggestion=DecisionSuggestion.CANDIDATE,
                blocking=False,
            )

    @staticmethod
    def _build_proposal_similarity_hint(proposal: Proposal) -> str:
        """Build compact text for recalling potentially similar experiences."""
        parts: list[str] = [
            proposal.root_cause or "",
            proposal.predicted_impact or "",
            json.dumps(proposal.targeted_fix, ensure_ascii=False),
            json.dumps(proposal.metadata.get("operations", []), ensure_ascii=False),
        ]
        for ev in proposal.failure_evidence:
            parts.append(ev.description)
        return "\n".join(part[:1000] for part in parts if part)

    @staticmethod
    def _is_duplicate(proposal: Proposal) -> bool:
        """Check if a similar proposal already exists.

        Phase 1: always returns False (no cross-batch duplicate detection yet).
        Future: query evolution.db for similar content.
        """
        return False

    @staticmethod
    def _parse_llm_json(text: str) -> dict:
        """Extract JSON from LLM response."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        m2 = re.search(r"\{.*\}", text, re.DOTALL)
        if m2:
            try:
                return json.loads(m2.group(0))
            except json.JSONDecodeError:
                pass

        return {"score": 0.5, "suggestion": "candidate", "reason": "Failed to parse LLM response"}

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
            if api_key and isinstance(api_key, str):
                if api_key.startswith("${") and api_key.endswith("}"):
                    env_var = api_key[2:-1]
                    api_key = os.getenv(env_var)
            else:
                api_key = os.getenv("EVOLVE_API_KEY")

            if not api_key:
                logger.warning("AheDecisionPolicy: no API key configured")
                return None

            # Get api_base
            api_base = llm_cfg.get("api_base")
            if not api_base or not isinstance(api_base, str) or api_base.strip() == "":
                api_base = "https://api.deepseek.com/v1"
                logger.info("AheDecisionPolicy: api_base not configured, using DeepSeek default: %s", api_base)

            client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=60.0)

            return OpenAIModelWrapper(
                client=client,
                model=llm_cfg.get("model_name", "deepseek-v4-pro"),
                temperature=llm_cfg.get("temperature", 0.1),
                max_tokens=llm_cfg.get("max_tokens", 2000),
            )
        except Exception as exc:
            logger.warning("AheDecisionPolicy._init_model failed: %s", exc)
            return None