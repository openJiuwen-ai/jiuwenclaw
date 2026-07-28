# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Auditor and rectify-feedback prompt templates adapted from AgentDropoutV2."""

from __future__ import annotations

TEAM_METRIC_AUDIT_TEMPLATE = """
You are a Team Collaboration Auditor.
Your task is to verify if a specific team member (**Agent Role**) has committed a
**FATAL collaboration flaw** regarding a specific **Area of Concern**.

### Relevance Pre-Check (CRITICAL)
Before auditing, evaluate if the **[Area of Concern]** is actually relevant to the
current Task and Agent Output.
- If Irrelevant: STOP and PASS. Write "Metric not applicable" in `analysis`,
  "N/A" in `suggestion`, and set `is_flawed` to false.
- If Relevant: Proceed to the Impact & Action Protocol below.

### The "Impact & Action" Protocol
1. Presumption of Validity: assume the agent is correct unless you find
   irrefutable evidence of a fatal flaw.
2. Actionability Test: if you cannot provide a concrete correction, it is NOT a flaw.
3. Impact Test: imperfect phrasing that does not change the core contribution
   is NOT a flaw.

### Judgment Criteria
**[Area of Concern]**: {trigger_condition}

Risk guidance:
{risk_alert}

### CONTEXT
- **Task**: {task}
- **Agent Role**: {role}
- **Agent Output**: {agent_output}

### OUTPUT FORMAT (JSON ONLY)
{{
    "evidence_quote": "Verbatim quote of the problematic part. Write 'N/A' if valid or irrelevant.",
    "analysis": "Explain WHY this violates the Area of Concern. Concise. Write 'N/A' if valid.",
    "suggestion": "Concrete instruction on how to fix it. Write 'N/A' if no fix needed.",
    "impact_assessment": "Does the core contribution / team outcome change? (YES/NO) and brief reason.",
    "is_flawed": false
}}
"""

RECTIFY_FEEDBACK_HEADER = (
    "An external auditor has reviewed your previous team contribution "
    "(Attempt {attempt_num}) and flagged some potential issues. "
    "Please review the following suggestions critically:\n\n"
)

RECTIFY_FEEDBACK_FOOTER = (
    "\n\n**Instruction**:\n"
    "1. If you agree with the advice, please refine your contribution.\n"
    "2. **If you are confident your original logic is correct, you may ignore this advice.**\n"
    "3. Please output the corrected contribution suitable for sharing with the team."
)

DROP_SIGNAL_PREFIX = "[AGENT_DROPOUT]"
DROP_SIGNAL_MESSAGE = (
    "{prefix} Member '{member_name}' exceeded failed-correction threshold "
    "({failure_count}) and should be removed via shutdown_member. "
    "Reason: {reason}"
)

REJECT_TOOL_MESSAGE = (
    "{prefix} Contribution rejected after audit failure. "
    "Do not share this content with the team.\n{details}"
)

RECTIFY_TOOL_MESSAGE = (
    "{prefix} Contribution blocked pending correction "
    "(attempt {attempt}/{max_attempts}).\n\n{feedback}"
)


def build_rectify_feedback(attempt_num: int, feedback_lines: list[str]) -> str:
    """Build the ADv2-style rectification feedback body."""
    body = "\n".join(feedback_lines)
    return (
        RECTIFY_FEEDBACK_HEADER.format(attempt_num=attempt_num)
        + body
        + RECTIFY_FEEDBACK_FOOTER
    )
