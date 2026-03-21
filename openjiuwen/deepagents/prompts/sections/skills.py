# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill prompt section for DeepAgent (migrated from skill_rail_prompt.py)."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# List-skill system prompt (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_CN = """
你是一个技能选择器。
你的任务是为给定的用户任务选择最相关的技能。
仅返回一个 JSON 对象。
输出格式：
{
  "skills": ["skill_name_1", "skill_name_2"]
}
"""

SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_EN = """
You are a list_skill selector.
Your task is to select the most relevant skills for the given user task.
Return a JSON object only.
Output format:
{
  "skills": ["skill_name_1", "skill_name_2"]
}
"""

SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_CN,
    "en": SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_EN,
}

# ---------------------------------------------------------------------------
# All-mode header / instruction (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_ALL_MODE_HEADER_CN = (
    "你是一个配备了技能的智能体，用于解决任务。\n"
    "在执行之前，请使用 read_file 阅读相关的 SKILL.md。\n\n"
    "可用技能：\n"
)

SKILL_RAIL_ALL_MODE_HEADER_EN = (
    "You are an agent equipped with skills to solve tasks.\n"
    "Before execution, read the relevant SKILL.md using read_file.\n\n"
    "Available skills:\n"
)

SKILL_RAIL_ALL_MODE_HEADER: Dict[str, str] = {
    "cn": SKILL_RAIL_ALL_MODE_HEADER_CN,
    "en": SKILL_RAIL_ALL_MODE_HEADER_EN,
}

SKILL_RAIL_ALL_MODE_INSTRUCTION_CN = (
    "\n指令：\n"
    "通过先阅读 SKILL.md 来选择最相关的技能。\n"
    "需要时使用 code 执行 Python 或 JavaScript。\n"
    "需要时使用 bash 执行 shell 命令。"
)

SKILL_RAIL_ALL_MODE_INSTRUCTION_EN = (
    "\nInstruction:\n"
    "Select the most relevant skill by reading its SKILL.md first.\n"
    "Use code to execute Python or JavaScript when needed.\n"
    "Use bash to execute shell commands when needed."
)

SKILL_RAIL_ALL_MODE_INSTRUCTION: Dict[str, str] = {
    "cn": SKILL_RAIL_ALL_MODE_INSTRUCTION_CN,
    "en": SKILL_RAIL_ALL_MODE_INSTRUCTION_EN,
}

# ---------------------------------------------------------------------------
# Auto-list mode prompt (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_AUTO_LIST_MODE_PROMPT_CN = """
你是一个配备了技能的智能体，用于解决任务。
在执行之前，请使用 read_file 阅读相关的 SKILL.md。

指令：
当你需要决定哪个技能与当前任务相关时，请先调用 list_skill 工具。
然后在使用技能之前阅读最相关的 SKILL.md。
需要时使用 code 执行 Python 或 JavaScript。
需要时使用 bash 执行 shell 命令。
"""

SKILL_RAIL_AUTO_LIST_MODE_PROMPT_EN = """
You are an agent equipped with skills to solve tasks.
Before execution, read the relevant SKILL.md using read_file.

Instruction:
When you need to decide which skill is relevant to the current task, call the list_skill tool first.
Then read the most relevant SKILL.md before using the skill.
Use code to execute Python or JavaScript when needed.
Use bash to execute shell commands when needed.
"""

SKILL_RAIL_AUTO_LIST_MODE_PROMPT: Dict[str, str] = {
    "cn": SKILL_RAIL_AUTO_LIST_MODE_PROMPT_CN,
    "en": SKILL_RAIL_AUTO_LIST_MODE_PROMPT_EN,
}

# ---------------------------------------------------------------------------
# No-skill fallback prompt (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_NO_SKILL_PROMPT_CN = """
你是一个配备了技能的智能体，用于解决任务。
当技能信息可用时，请使用 read_file 阅读相关的 SKILL.md。
需要时使用 code 执行 Python 或 JavaScript。
需要时使用 bash 执行 shell 命令。

当前任务没有选择任何技能。
"""

SKILL_RAIL_NO_SKILL_PROMPT_EN = """
You are an agent equipped with skills to solve tasks.
Before execution, read the relevant SKILL.md using read_file when skill information is available.
Use code to execute Python or JavaScript when needed.
Use bash to execute shell commands when needed.

No skill was selected for this task.
"""

SKILL_RAIL_NO_SKILL_PROMPT: Dict[str, str] = {
    "cn": SKILL_RAIL_NO_SKILL_PROMPT_CN,
    "en": SKILL_RAIL_NO_SKILL_PROMPT_EN,
}


# ---------------------------------------------------------------------------
# Helper functions (same signatures, now accept language)
# ---------------------------------------------------------------------------
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


def build_all_mode_skill_prompt(skill_lines: str, language: str = "cn") -> str:
    """Build prompt for all mode."""
    text = (skill_lines or "").strip()
    if not text:
        return SKILL_RAIL_NO_SKILL_PROMPT.get(language, SKILL_RAIL_NO_SKILL_PROMPT_CN)
    header = SKILL_RAIL_ALL_MODE_HEADER.get(language, SKILL_RAIL_ALL_MODE_HEADER_CN)
    instruction = SKILL_RAIL_ALL_MODE_INSTRUCTION.get(language, SKILL_RAIL_ALL_MODE_INSTRUCTION_CN)
    return header + text + instruction


def build_auto_list_mode_skill_prompt(language: str = "cn") -> str:
    """Build prompt for auto_list mode."""
    return SKILL_RAIL_AUTO_LIST_MODE_PROMPT.get(language, SKILL_RAIL_AUTO_LIST_MODE_PROMPT_CN)


def get_list_skill_system_prompt(language: str = "cn") -> str:
    """Get the list_skill system prompt for the given language."""
    return SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT.get(language, SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_CN)


def build_skills_section(
    skill_lines: str,
    language: str = "cn",
    mode: str = "all",
) -> Optional["PromptSection"]:
    """Build a PromptSection for skills.

    Args:
        skill_lines: Pre-rendered skill lines (only used in 'all' mode).
        language: 'cn' or 'en'.
        mode: 'all' or 'auto_list'.

    Returns:
        A PromptSection instance, or None if mode is unrecognised.
    """
    from openjiuwen.deepagents.prompts.builder import PromptSection

    if mode == "all":
        content = build_all_mode_skill_prompt(skill_lines, language)
    elif mode == "auto_list":
        content = build_auto_list_mode_skill_prompt(language)
    else:
        return None

    return PromptSection(
        name="skills",
        content={language: content},
        priority=90,
    )
