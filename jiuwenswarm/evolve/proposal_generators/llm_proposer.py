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
PROPOSER_SYSTEM_PROMPT = """You are an expert at extracting actionable knowledge from agent conversations.

Your job: analyze a conversation where an agent used a skill, and extract KNOWLEDGE that will help the agent perform better next time.

You will receive:
- skill_name: which skill was invoked
- conversation: user messages and agent responses

IMPORTANT: Your output will be directly injected into the agent's context as "evolution experience".
So you must produce ACTIONABLE KNOWLEDGE, not diagnosis.

BAD output (diagnosis - useless to agent):
  root_cause: "SKILL.md lacks a formula definition"
  suggestion: "Add formula to SKILL.md"

GOOD output (knowledge - directly useful to agent):
  root_cause: "Agent didn't know the correct formula for @@@"
  suggestion: "The @@@ operator is defined as: a@@@b = a*b + a^3 + b^3. Example: 3@@@5 = 15+27+125 = 167."

What to extract as knowledge:
- Correct formulas, definitions, algorithms that the agent got wrong
- User preferences (output format, style, language)
- Behavioral rules the user explicitly requested
- Facts or domain knowledge the agent was missing

Do NOT report:
- Infrastructure issues (status codes, tool bugs)
- Things that can't be fixed by adding knowledge
- Vague suggestions like "improve the skill"
- Cases where the agent performed correctly
- Cases where there's no clearly better alternative

IMPORTANT: Only generate a proposal when you can observe a CLEAR DEFICIENCY in the
conversation — the agent did something wrong, gave incorrect results, or the user
explicitly corrected it. If the skill worked correctly or you're unsure whether there's
a problem, return {"proposals": []}. Err on the side of NOT generating proposals.
A wrong or unnecessary proposal is worse than no proposal.

Output (JSON):
{
  "proposals": [
    {
      "target_id": "skill-name",
      "target_type": "skill",
      "proposal_type": "add_skill_experience",
      "failure_evidence": [
        {"trace_id": "...", "span_id": null, "field_path": null, "description": "brief description of what went wrong"}
      ],
      "root_cause": "what the agent did wrong (for context)",
      "targeted_fix": {
        "action": "add_knowledge",
        "suggestion": "THE ACTUAL KNOWLEDGE to inject - must be directly usable by the agent. Include formulas, rules, examples, etc."
      },
      "predicted_impact": "how this helps",
      "risk": "potential downsides"
    }
  ]
}
"""


SKILL_TRACE_MARKERS = (
    "gen_ai.tool.execute: skill_tool",
    "gen_ai.tool.execute: search_skill",
    "gen_ai.tool.execute: install_skill",
)


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

    @staticmethod
    def _is_skill_trace(spans: list[dict]) -> bool:
        """Check if a trace involves skill-related tool calls."""
        for span in spans:
            name = span.get("name", "")
            if any(marker in name for marker in SKILL_TRACE_MARKERS):
                return True
        return False

    @staticmethod
    def _extract_conversation(trace_id: str, spans: list[dict]) -> dict | None:
        """Extract meaningful conversation data from spans for LLM analysis."""
        skill_name = None
        conversation_rounds = []

        for span in spans:
            name = span.get("name", "")
            attrs_raw = span.get("attributes") or ""

            # Extract skill name from skill_tool span
            if "gen_ai.tool.execute: skill_tool" in name and attrs_raw:
                try:
                    attrs = json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
                    args = json.loads(attrs.get("gen_ai.tool.arguments", "{}"))
                    skill_name = args.get("skill_name")
                except (json.JSONDecodeError, TypeError):
                    pass

            # Extract conversation from gen_ai.chat spans
            if name == "gen_ai.chat" and attrs_raw:
                try:
                    attrs = json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
                    messages_raw = attrs.get("gen_ai.input.messages", "")
                    if not messages_raw:
                        continue
                    messages = json.loads(messages_raw) if isinstance(messages_raw, str) else messages_raw

                    # Extract user and assistant messages (skip system)
                    for msg in messages:
                        role = msg.get("role", "")
                        if role in ("user", "assistant"):
                            parts = msg.get("parts", [])
                            text_parts = []
                            for p in parts:
                                if isinstance(p, dict) and p.get("type") == "text":
                                    content = p.get("content", "")
                                    # Truncate long messages
                                    if len(content) > 2000:
                                        content = content[:2000] + "...[truncated]"
                                    text_parts.append(content)
                            if text_parts:
                                conversation_rounds.append({
                                    "role": role,
                                    "content": "\n".join(text_parts),
                                })
                except (json.JSONDecodeError, TypeError):
                    pass

        if not skill_name:
            return None

        return {
            "trace_id": trace_id,
            "skill_name": skill_name,
            "conversation": conversation_rounds,
        }

    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        if self._trace_reader is None:
            logger.warning("LLMProposer: no trace_reader configured")
            return []

        # Collect conversation data, filtering for skill-related traces
        trace_summaries: list[dict] = []
        skipped = 0
        for trace_id in batch.trace_ids:
            spans = self._trace_reader.read_spans(trace_id)
            if not spans:
                continue
            if not self._is_skill_trace(spans):
                skipped += 1
                continue
            summary = self._extract_conversation(trace_id, spans)
            if summary:
                trace_summaries.append(summary)

        if skipped:
            logger.info(
                "LLMProposer: skipped %d non-skill traces, %d remaining",
                skipped, len(trace_summaries),
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
                    target_id=raw.get("target_id"),
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
        """Call the LLM and parse the JSON response."""
        try:
            from openjiuwen.core.foundation.llm import (
                Model,
                ModelClientConfig,
                ModelRequestConfig,
                SystemMessage,
                UserMessage,
            )
        except ImportError:
            logger.warning("LLMProposer: openjiuwen.llm not available")
            return []

        try:
            from jiuwenswarm.common.config import get_default_models
            from jiuwenswarm.common.utils import get_env_file

            # Load .env if not already loaded (CLI context)
            env_file = get_env_file()
            if env_file.exists():
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=env_file, override=False)

            models = get_default_models()
            if not models:
                logger.warning("LLMProposer: no default model configured")
                return []

            first = models[0]
            client_cfg = first.get("model_client_config", {})
            model_cfg = first.get("model_config_obj", {})

            model = Model(
                model_client_config=ModelClientConfig(
                    client_provider=client_cfg.get("client_provider", "OpenAI"),
                    api_base=client_cfg.get("api_base", ""),
                    api_key=client_cfg.get("api_key", ""),
                    verify_ssl=client_cfg.get("verify_ssl", False),
                ),
                model_config=ModelRequestConfig(
                    model=client_cfg.get("model_name", self._model_name),
                    temperature=model_cfg.get("temperature", 0.7),
                ),
            )

            import json

            user_content = json.dumps(trace_summaries, ensure_ascii=False)
            messages = [
                SystemMessage(content=PROPOSER_SYSTEM_PROMPT),
                UserMessage(content=user_content),
            ]

            logger.info(
                "LLMProposer: calling LLM with %d trace summaries",
                len(trace_summaries),
            )
            response = await model.invoke(messages=messages)
            content = (
                response.content if hasattr(response, "content") else str(response)
            )

            # Parse JSON from response
            parsed = self._parse_json_response(content)
            return parsed.get("proposals", [])

        except Exception as exc:
            logger.warning("LLMProposer._call_llm failed: %s", exc)
            return []

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        """Extract JSON from LLM response, handling markdown code blocks."""
        import json

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` blocks
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        # Try extracting from ``` ... ``` blocks
        if "```" in text:
            start = text.index("```") + 3
            # Skip language identifier on same line
            newline = text.index("\n", start)
            start = newline + 1
            end = text.index("```", start)
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        logger.warning("LLMProposer: failed to parse JSON from LLM response")
        return {"proposals": []}
