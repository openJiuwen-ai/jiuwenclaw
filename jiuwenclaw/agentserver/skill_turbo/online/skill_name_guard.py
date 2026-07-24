# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""skill_name / skill_root 路径安全校验。

LLM 工具参数中的 skill_name 不可直接拼进 Path，否则 ``..`` / 路径分隔符
可逃出已注册 skill 目录。
"""

from __future__ import annotations

import re
from pathlib import Path

# 允许字母数字、下划线、点、连字符；禁止以点开头（挡住 ``.`` / ``..``）
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# scenario：仅字母数字下划线（用于路径/模块名拼接）
_SCENARIO_RE = re.compile(r"^[A-Za-z0-9_]+$")


class InvalidSkillNameError(ValueError):
    """skill_name 非法（含路径穿越或非法字符）。"""


class InvalidScenarioError(ValueError):
    """scenario 非法（含路径穿越或非法字符）。"""


def validate_skill_name(skill_name: str) -> str:
    """校验并返回规范化 skill_name；非法则抛 InvalidSkillNameError。"""
    name = (skill_name or "").strip()
    if not name:
        raise InvalidSkillNameError("skill_name 不能为空")
    if ".." in name or "/" in name or "\\" in name:
        raise InvalidSkillNameError(f"非法 skill_name: {skill_name!r}")
    if not _SKILL_NAME_RE.fullmatch(name):
        raise InvalidSkillNameError(f"非法 skill_name: {skill_name!r}")
    return name


def validate_scenario(scenario: str) -> str:
    """校验 scenario（仅 [A-Za-z0-9_]），用于 schema/模块路径拼接。"""
    name = (scenario or "").strip()
    if not name:
        raise InvalidScenarioError("scenario 不能为空")
    if ".." in name or "/" in name or "\\" in name or "." in name:
        raise InvalidScenarioError(f"非法 scenario: {scenario!r}")
    if not _SCENARIO_RE.fullmatch(name):
        raise InvalidScenarioError(f"非法 scenario: {scenario!r}")
    return name


def safe_join_skill_dir(base: Path | str, skill_name: str) -> Path | None:
    """在 base 下拼接 skill_name，resolve 后必须仍位于 base 之下。

    Returns:
        存在的目录 Path，否则 None（含路径逃逸或目录不存在）。
    """
    try:
        name = validate_skill_name(skill_name)
    except InvalidSkillNameError:
        return None
    base_path = Path(base).resolve()
    candidate = (base_path / name).resolve()
    try:
        candidate.relative_to(base_path)
    except ValueError:
        return None
    if candidate.is_dir():
        return candidate
    return None


__all__ = [
    "InvalidSkillNameError",
    "InvalidScenarioError",
    "validate_skill_name",
    "validate_scenario",
    "safe_join_skill_dir",
]
