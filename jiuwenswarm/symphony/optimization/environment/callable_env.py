"""Callable / agent / workflow environments.

These adapt *any* execution backend — a deterministic evaluator, a plugin, an
agent coroutine, or a JiuwenSwarm workflow — behind the same
:class:`PromptEnvironment` contract, so the optimizer stays decoupled from how a
candidate prompt is actually run.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from jiuwenswarm.symphony.optimization.environment.base import PromptEnvironment, run_cases
from jiuwenswarm.symphony.optimization.models import Execution, PromptCandidate, TaskSpec

# (system_prompt, case_input) -> output text (sync or async)
RunFn = Callable[[str, str], object]


class CallableEnvironment(PromptEnvironment):
    """Wrap any ``(system_prompt, case_input) -> str`` callable (sync or async)."""

    def __init__(
        self,
        fn: RunFn,
        *,
        parallel: bool = True,
        max_concurrency: int = 5,
        attribute_tokens: bool = False,
    ) -> None:
        self._fn = fn
        self._parallel = parallel
        self._max_concurrency = max_concurrency
        self._attribute_tokens = attribute_tokens

    async def execute(self, candidate: PromptCandidate, task: TaskSpec) -> Execution:
        async def runner(system_prompt: str, case_input: str) -> str:
            result = self._fn(system_prompt, case_input)
            if isinstance(result, Awaitable):
                result = await result
            return "" if result is None else str(result)

        return await run_cases(
            runner,
            candidate,
            task,
            parallel=self._parallel,
            max_concurrency=self._max_concurrency,
            attribute_tokens=self._attribute_tokens,
        )


# An agent runner is just a callable that happens to drive an agent; alias for clarity.
AgentEnvironment = CallableEnvironment


class WorkflowEnvironment(PromptEnvironment):
    """Run each candidate through a JiuwenSwarm workflow/agent coroutine.

    ``workflow`` is ``async (system_prompt, case_input) -> str`` — e.g. a wrapper
    around a Symphony plan execution, a team run, or a single-agent turn. Token
    usage is attributed automatically when the workflow uses the JiuwenSwarm LLM
    stack.
    """

    def __init__(
        self,
        workflow: Callable[[str, str], Awaitable[str]],
        *,
        parallel: bool = True,
        max_concurrency: int = 5,
    ) -> None:
        self._workflow = workflow
        self._parallel = parallel
        self._max_concurrency = max_concurrency

    async def execute(self, candidate: PromptCandidate, task: TaskSpec) -> Execution:
        return await run_cases(
            self._workflow,
            candidate,
            task,
            parallel=self._parallel,
            max_concurrency=self._max_concurrency,
        )


__all__ = ["CallableEnvironment", "AgentEnvironment", "WorkflowEnvironment"]
