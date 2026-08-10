"""LLM environment: run each candidate as a system prompt over the task inputs."""

from __future__ import annotations

from typing import Any

from jiuwenswarm.symphony.optimization.environment.base import PromptEnvironment, run_cases
from jiuwenswarm.symphony.optimization.models import Execution, PromptCandidate, TaskSpec


class LLMEnvironment(PromptEnvironment):
    """Execute a candidate prompt directly against an LLM.

    The candidate becomes the system prompt; each case input becomes the user
    message. This is the default backend and requires no external workflow.
    """

    def __init__(
        self,
        client: Any,
        *,
        parallel: bool = True,
        max_concurrency: int = 5,
    ) -> None:
        self._client = client
        self._parallel = parallel
        self._max_concurrency = max_concurrency

    async def execute(self, candidate: PromptCandidate, task: TaskSpec) -> Execution:
        async def runner(system_prompt: str, case_input: str) -> str:
            return await self._client.complete_text_async(
                system_prompt=system_prompt,
                user_content=case_input,
                error_context="Prompt optimizer environment",
            )

        return await run_cases(
            runner,
            candidate,
            task,
            parallel=self._parallel,
            max_concurrency=self._max_concurrency,
        )


__all__ = ["LLMEnvironment"]
