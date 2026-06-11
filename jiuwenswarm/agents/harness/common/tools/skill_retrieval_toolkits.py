"""Agent-facing toolkit for installed skill retrieval."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
from jiuwenswarm.symphony.skill_retrieval import build_skill_index, retrieve_skills
from jiuwenswarm.symphony.skill_retrieval.config import load_settings


def is_skill_retrieval_enabled() -> bool:
    try:
        return bool(load_settings().enabled)
    except Exception:
        return False


class SkillRetrievalToolkit:
    """Expose dispatch-backed installed skill retrieval to agents."""

    def __init__(self, manager: SkillManager) -> None:
        self._manager = manager

    async def skill_index_build(self) -> dict[str, Any]:
        return await asyncio.to_thread(build_skill_index, self._manager)

    async def skill_retrieve(self, query: str) -> dict[str, Any]:
        return await asyncio.to_thread(retrieve_skills, query, self._manager)

    def get_tools(self) -> list[Tool]:
        def make_tool(name: str, description: str, input_params: dict, func: Callable[..., Any]) -> Tool:
            card = ToolCard(
                id=name,
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="skill_index_build",
                description=(
                    "Build or refresh the local dispatch tree index for installed enabled skills. "
                    "Do not call this proactively. First call skill_retrieve with the task query; "
                    "call skill_index_build only if skill_retrieve returns a failure result that "
                    "explicitly says the index is missing or stale and instructs you to build it."
                ),
                input_params={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                func=self.skill_index_build,
            ),
            make_tool(
                name="skill_retrieve",
                description=(
                    "Retrieve relevant installed skills for a task query using the local dispatch skill index. "
                    "Call this before skill_index_build. If the index is missing or stale, this tool returns "
                    "a failure result with instructions to call skill_index_build and then retry retrieval. "
                    "This only returns retrieval hints; it does not execute or orchestrate skills."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "User task or retrieval query.",
                        },
                    },
                    "required": ["query"],
                },
                func=self.skill_retrieve,
            ),
        ]
