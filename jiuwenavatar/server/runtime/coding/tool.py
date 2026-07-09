# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""统一编码任务工具 ``coding_task``.

Leader 始终调用同一个 ``coding_task`` 工具；运行时根据当前分身激活的
``CodingEngine``（通过 ContextVar 设置）自动路由到 claude-code / codex 等外部 CLI。
原生 jiuwen-coding 后端不注册该工具（Leader 直接用 skills + bash）。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from openjiuwen.core.foundation.tool import tool

from jiuwenavatar.server.runtime.coding.engines import CodingEngine

logger = logging.getLogger(__name__)

_ACTIVE_ENGINE: ContextVar[CodingEngine | None] = ContextVar(
    "active_coding_engine", default=None
)


def set_active_coding_engine(engine: CodingEngine | None) -> None:
    """设置当前会话激活的编码引擎（供 coding_task 路由）."""
    _ACTIVE_ENGINE.set(engine)


def get_active_coding_engine() -> CodingEngine | None:
    """读取当前会话激活的编码引擎."""
    return _ACTIVE_ENGINE.get()


def clear_active_coding_engine() -> None:
    """清除当前会话激活的编码引擎."""
    _ACTIVE_ENGINE.set(None)


@tool(
    name="coding_task",
    description=(
        "Delegate an AIDLC coding/review task to the avatar's configured coding engine "
        "(Claude Code or Codex CLI). When an external engine is active, use this for code reading, "
        "review analysis, implementation, and other coding work instead of analyzing code/diff in "
        "the Leader. For PR/diff review, do not pre-read the diff to count lines or understand scope; "
        "delegate the PR URL and let the coding engine collect evidence autonomously. Pass a complete, "
        "self-contained task prompt: original goal, PR/Issue URL, expected outputs, and whether "
        "inline-comment suggestions are needed. Example: '@dev-reviewer independently review this "
        "PR: <URL>; read ./skills/dev-reviewer/SKILL.md; use GITCODE_TOKEN to fetch diff; do not "
        "ask follow-up questions; return findings with path/location/position when possible.' "
        "Runs non-interactively in the prepared workspace (skills are symlinked in and credentials "
        "such as GITCODE_TOKEN are passed through). Returns the engine's full textual output for "
        "you to act on (submit review comments, open PRs, etc.)."
    ),
)
async def coding_task(message: str, cwd: str | None = None) -> str:
    """Route a coding task to the active external coding engine."""
    engine = get_active_coding_engine()
    if engine is None or not engine.is_cli:
        return (
            "[coding_task] 当前没有激活外部编码引擎（原生 jiuwen-coding 后端）。"
            "请直接使用已加载的 Skill 与 bash 完成本次任务，不要调用 coding_task。"
        )
    return await engine.run_task(message, cwd=cwd)
