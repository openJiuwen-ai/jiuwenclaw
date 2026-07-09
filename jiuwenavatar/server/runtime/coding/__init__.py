# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""统一编码引擎抽象（Coding Engine）.

数字分身的"编码后端"被抽象为可互换的 ``CodingEngine``：

- ``jiuwen-coding``  —  原生 DeepAgent，无外部 CLI（Leader 直接用 skills + bash 执行）
- ``claude-code``    —  Anthropic Claude Code CLI（``claude -p``）
- ``codex``          —  OpenAI Codex CLI（``codex exec``）

所有 CLI 引擎共享同一套"准备工作区 + 缺失即安装 + 运行任务"的流程，
并通过唯一的 ``coding_task`` 工具暴露给 Leader——Leader 不需要知道具体后端，
运行时按当前分身的 ``coding_engine`` 自动路由。
"""

from __future__ import annotations

from jiuwenavatar.server.runtime.coding.engines import (
    CODING_ENGINE_CLAUDE_CODE,
    CODING_ENGINE_CODEX,
    CODING_ENGINE_JIUWEN,
    DEFAULT_CODING_ENGINE,
    CodingEngine,
    EngineStatus,
    assert_coding_engine_selectable,
    clear_workspace_avatar,
    coding_engine_selectability,
    get_coding_engine,
    list_coding_engine_selectability,
    list_engine_kinds,
    set_workspace_avatar,
)
from jiuwenavatar.server.runtime.coding.tool import (
    clear_active_coding_engine,
    coding_task,
    get_active_coding_engine,
    set_active_coding_engine,
)

__all__ = [
    "CODING_ENGINE_JIUWEN",
    "CODING_ENGINE_CLAUDE_CODE",
    "CODING_ENGINE_CODEX",
    "DEFAULT_CODING_ENGINE",
    "CodingEngine",
    "EngineStatus",
    "assert_coding_engine_selectable",
    "coding_engine_selectability",
    "get_coding_engine",
    "list_coding_engine_selectability",
    "list_engine_kinds",
    "coding_task",
    "set_active_coding_engine",
    "get_active_coding_engine",
    "clear_active_coding_engine",
    "set_workspace_avatar",
    "clear_workspace_avatar",
]
