"""Environment interface + a shared case runner.

An :class:`PromptEnvironment` executes a candidate prompt and returns a backend-
agnostic :class:`Execution`. The concrete backend only needs to answer "given this
system prompt and this input, what is the output?" — :func:`run_cases` handles the
per-case timing, error isolation, and token attribution around it.

Token attribution works under concurrent candidate execution by tagging every LLM
call with a unique per-candidate ``operation`` (``env:<candidate_id>``) via
``llm_usage_context``; asyncio copies the context per task, so tags don't bleed
across candidates. Backends that don't touch the JiuwenSwarm LLM stack simply
report zero tokens.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from jiuwenswarm.symphony.llm import get_llm_token_usage_summary, llm_usage_context
from jiuwenswarm.symphony.optimization.models import (
    CaseResult,
    Execution,
    PromptCandidate,
    TaskSpec,
)

# A runner turns (system_prompt, case_input) into an output string.
CaseRunner = Callable[[str, str], Awaitable[str]]


class PromptEnvironment(ABC):
    """Executes a candidate prompt against a task and reports an :class:`Execution`."""

    @abstractmethod
    async def execute(self, candidate: PromptCandidate, task: TaskSpec) -> Execution:
        ...


async def run_cases(
    runner: CaseRunner,
    candidate: PromptCandidate,
    task: TaskSpec,
    *,
    parallel: bool = True,
    max_concurrency: int = 5,
    attribute_tokens: bool = True,
) -> Execution:
    """Run every case of ``task`` through ``runner`` under ``candidate``'s prompt."""

    op = f"env:{candidate.candidate_id}"
    cases = task.cases or [_implicit_case(task)]
    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def one(case) -> CaseResult:
        started = time.monotonic()
        try:
            if parallel:
                async with sem:
                    output = await runner(candidate.prompt, case.input)
            else:
                output = await runner(candidate.prompt, case.input)
            return CaseResult(
                case_input=case.input,
                output=output or "",
                latency_s=time.monotonic() - started,
                hidden=case.hidden,
            )
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                case_input=case.input,
                output="",
                latency_s=time.monotonic() - started,
                hidden=case.hidden,
                error=str(exc),
            )

    started = time.monotonic()
    with llm_usage_context("optimization", op):
        if parallel:
            results = list(await asyncio.gather(*(one(c) for c in cases)))
        else:
            results = [await one(c) for c in cases]
    total_latency = time.monotonic() - started

    errors = [r.error for r in results if r.error]
    execution = Execution(
        candidate=candidate,
        case_results=results,
        latency_s=total_latency,
        token_usage=_token_usage_for(op) if attribute_tokens else {},
        error=errors[0] if errors and len(errors) == len(results) else None,
    )
    return execution


def _implicit_case(task: TaskSpec):
    from jiuwenswarm.symphony.optimization.models import TaskCase

    return TaskCase(input=task.objective)


def _token_usage_for(op: str) -> dict:
    try:
        summary = get_llm_token_usage_summary()
    except Exception:  # noqa: BLE001
        return {}
    records = summary.get("records") if isinstance(summary, dict) else None
    if not isinstance(records, list):
        return {}
    total = sum(
        int(r.get("total_tokens") or 0)
        for r in records
        if isinstance(r, dict) and r.get("operation") == op
    )
    return {"total": {"total_tokens": total}}


__all__ = ["PromptEnvironment", "CaseRunner", "run_cases"]
