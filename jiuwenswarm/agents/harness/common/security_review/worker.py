# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Async security review worker boundary."""
from __future__ import annotations

import json
from typing import Any

from jiuwenswarm.agents.harness.common.security_review.candidate_builder import (
    SECURITY_ADDENDUM,
    SECURITY_CANDIDATE_SYSTEM_PROMPT,
    SecurityCandidateBuilder,
)
from jiuwenswarm.agents.harness.common.security_review.schema import ReviewRequest, ReviewResult


class SecurityReviewWorker:
    """Run heavier security review outside hot callbacks."""

    def __init__(
        self,
        candidate_builder: SecurityCandidateBuilder | None = None,
        llm: Any | None = None,
    ) -> None:
        self._candidate_builder = candidate_builder or SecurityCandidateBuilder()
        self._llm = llm

    def update_llm(self, llm: Any | None) -> None:
        self._llm = llm

    async def review(self, request: ReviewRequest) -> ReviewResult:
        if self._llm is None:
            return ReviewResult(
                session_id=request.session_id,
                summary=self._summary(request),
                runtime_advice="",
                candidates=[],
            )

        raw_result = await self._invoke_llm(request)
        parsed = self._candidate_builder.validate_llm_result(raw_result)
        return ReviewResult(
            session_id=request.session_id,
            summary=parsed["summary"] or self._summary(request),
            runtime_advice="",
            candidates=parsed["candidates"],
        )

    async def _invoke_llm(self, request: ReviewRequest) -> dict[str, Any]:
        payload = self._candidate_builder.build_llm_input(request)
        user_prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        response = await self._llm.invoke(
            messages=[
                {
                    "role": "system",
                    "content": SECURITY_CANDIDATE_SYSTEM_PROMPT + "\n\n" + SECURITY_ADDENDUM,
                },
                {"role": "user", "content": user_prompt},
            ]
        )
        content = getattr(response, "content", response)
        try:
            parsed = json.loads(str(content))
        except json.JSONDecodeError:
            return {"summary": "", "runtime_advice": "", "candidate_decisions": []}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _summary(request: ReviewRequest) -> str:
        tool_names = sorted({signal.tool_name for signal in request.signals if signal.tool_name})
        return (
            f"Security review {request.request_type} for tools: {', '.join(tool_names) or 'none'}"
        )
