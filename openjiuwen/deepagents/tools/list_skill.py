# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.message import SystemMessage, UserMessage
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.deepagents.prompts.skill_rail_prompt import (
    SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT,
)
from openjiuwen.deepagents.tools.base_tool import ToolOutput


class ListSkillTool(Tool):
    """List all enabled skills or return relevant skills for a task."""

    def __init__(
        self,
        get_skills: Callable[[], List[Skill]],
        list_skill_model: Optional[Model] = None,
    ):
        """Initialize ListSkillTool.

        Args:
            get_skills: Callable that returns current enabled skills.
            list_skill_model: Optional model used for skill routing.
        """
        super().__init__(
            ToolCard(
                id="ListSkillTool",
                name="list_skill",
                description="List available skills or select relevant skills for the current task.",
            )
        )
        self.get_skills = get_skills
        self.list_skill_model = list_skill_model
        self.card.input_params = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional. Current user task. If empty, return all available skills.",
                }
            },
            "required": []
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        """Invoke list_skill tool."""
        query = str(inputs.get("query", "") or "").strip()

        try:
            if not query:
                return ToolOutput(
                    success=True,
                    data={
                        "skills": self._dump_all_skills(),
                        "mode": "all",
                    },
                )

            if self.list_skill_model is None:
                return ToolOutput(
                    success=True,
                    data={
                        "skills": self._dump_all_skills(),
                        "mode": "all",
                        "message": "list_skill_model is not configured, fallback to all skills.",
                    },
                )

            selected_names = await self._route_skills(query)
            selected_skills = self._select_skills_by_names(selected_names)

            return ToolOutput(
                success=True,
                data={
                    "skills": self._dump_skills(selected_skills),
                    "mode": "filtered",
                    "selected_skill_names": [skill.name for skill in selected_skills],
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
            )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None

    def _dump_all_skills(self) -> List[Dict[str, Any]]:
        """Dump all current enabled skills."""
        return self._dump_skills(self.get_skills() or [])

    def _dump_skills(self, skills: List[Skill]) -> List[Dict[str, Any]]:
        """Dump skill objects into serializable dicts."""
        results: List[Dict[str, Any]] = []
        for skill in skills:
            results.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "directory": str(skill.directory),
                    "skill_md_path": str(Path(skill.directory) / "SKILL.md"),
                }
            )
        return results

    async def _route_skills(self, query: str) -> List[str]:
        """Route skills with list_skill_model."""
        payload = self._dump_all_skills()

        response = await self.list_skill_model.invoke(
            messages=[
                SystemMessage(content=SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT),
                UserMessage(
                    content=(
                        f"User task:\n{query}\n\n"
                        "Available skills:\n"
                        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
                        "Return only the names of the skills that are relevant to the task."
                    )
                ),
            ]
        )

        content = getattr(response, "content", "") or ""
        return self._parse_selected_skill_names(content)

    def _parse_selected_skill_names(self, content: str) -> List[str]:
        """Parse selected skill names from model output."""
        text = (content or "").strip()
        if not text:
            return []

        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except Exception:
            return []

        skills = data.get("skills", [])
        if not isinstance(skills, list):
            return []

        return [str(item).strip() for item in skills if str(item).strip()]

    def _select_skills_by_names(self, names: List[str]) -> List[Skill]:
        """Select skill objects by names."""
        if not names:
            return []

        skills = self.get_skills() or []
        skill_map = {skill.name: skill for skill in skills}

        selected: List[Skill] = []
        for name in names:
            skill = skill_map.get(name)
            if skill is not None:
                selected.append(skill)

        return selected