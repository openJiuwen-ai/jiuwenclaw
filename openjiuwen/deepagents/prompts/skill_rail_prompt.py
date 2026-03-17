# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt templates for SkillRail."""

from __future__ import annotations

from typing import Iterable, List


SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT = """
You are a list_skill selector.
Your task is to select the most relevant skills for the given user task.
Return a JSON object only.
Output format:
{
  "skills": ["skill_name_1", "skill_name_2"]
}
"""


SKILL_RAIL_ALL_MODE_HEADER = (
    "You are an agent equipped with skills to solve tasks.\n"
    "Before execution, read the relevant SKILL.md using read_file.\n\n"
    "Available skills:\n"
)

SKILL_RAIL_ALL_MODE_INSTRUCTION = (
    "\nInstruction:\n"
    "Select the most relevant skill by reading its SKILL.md first.\n"
    "Use code to execute Python or JavaScript when needed.\n"
    "Use bash to execute shell commands when needed."
)


SKILL_RAIL_AUTO_LIST_MODE_PROMPT = """
You are an agent equipped with skills to solve tasks.
Before execution, read the relevant SKILL.md using read_file.

Instruction:
When you need to decide which skill is relevant to the current task, call the list_skill tool first.
Then read the most relevant SKILL.md before using the skill.
Use code to execute Python or JavaScript when needed.
Use bash to execute shell commands when needed.
"""


SKILL_RAIL_NO_SKILL_PROMPT = """
You are an agent equipped with skills to solve tasks.
Before execution, read the relevant SKILL.md using read_file when skill information is available.
Use code to execute Python or JavaScript when needed.
Use bash to execute shell commands when needed.

No skill was selected for this task.
"""


def build_skill_line(
    *,
    index: int,
    skill_name: str,
    description: str,
    skill_md_path: str,
) -> str:
    """Build one rendered skill line."""
    return (
        f"{index}. **{skill_name}**: {description}\n"
        f"   Path: {skill_md_path}"
    )


def build_skill_lines(lines: Iterable[str]) -> str:
    """Join rendered skill lines."""
    items: List[str] = [line for line in lines if line]
    return "\n\n".join(items)


def build_all_mode_skill_prompt(skill_lines: str) -> str:
    """Build prompt for all mode."""
    text = (skill_lines or "").strip()
    if not text:
        return SKILL_RAIL_NO_SKILL_PROMPT
    return SKILL_RAIL_ALL_MODE_HEADER + text + SKILL_RAIL_ALL_MODE_INSTRUCTION


def build_auto_list_mode_skill_prompt() -> str:
    """Build prompt for auto_list mode."""
    return SKILL_RAIL_AUTO_LIST_MODE_PROMPT