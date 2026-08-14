# -*- coding: utf-8 -*-
"""最小自定义 MCP server：掷骰子 + 历史持久化。

暴露两个工具（内置/bash 都不能跨会话持久化掷骰历史）：
- roll_dice: 掷 N 面骰子 K 次，记录到 JSON 文件
- get_roll_history: 查看掷骰历史

用于验证 v3 检索：MCP 工具提供"内置无法替代"的能力时，
LLM 会不会 search_tools 发现并调用它。
"""
from __future__ import annotations
import json
import os
import random
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dice")
_HISTORY = Path(os.environ.get("DICE_HISTORY_PATH", "dice_history.json"))


def _load_history() -> list:
    if _HISTORY.exists():
        try:
            return json.loads(_HISTORY.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_history(h: list) -> None:
    _HISTORY.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


@mcp.tool()
def roll_dice(sides: int = 6, count: int = 1) -> str:
    """Roll a die with N sides K times. Returns each roll result and persists to history.

    For random decisions, games, sampling. Unlike bash RANDOM: this tool persists
    every result to history, viewable later via get_roll_history.

    Args:
        sides: number of die faces (e.g. 6, 20), default 6.
        count: how many times to roll, default 1.

    Returns:
        a string of each result plus the total.
    """
    if sides < 2:
        return "sides 必须 >= 2"
    count = max(1, min(count, 100))
    results = [random.randint(1, sides) for _ in range(count)]
    total = sum(results)
    history = _load_history()
    history.append({"sides": sides, "count": count, "results": results, "total": total})
    _save_history(history[-1000:])
    if count == 1:
        return f"掷 {sides} 面骰子 1 次，点数：{results[0]}"
    return f"掷 {sides} 面骰子 {count} 次，点数：{results}，总和：{total}"


@mcp.tool()
def get_roll_history(limit: int = 10) -> str:
    """View the roll history. Returns the most recent `limit` roll results.

    For reviewing past roll_dice records. History is persisted to a local file,
    preserved across sessions.

    Args:
        limit: how many recent entries to return, default 10.

    Returns:
        a string of the history records.
    """
    history = _load_history()
    if not history:
        return "暂无掷骰历史"
    recent = history[-limit:]
    lines = [f"- {i+1}. {h['results']} (总和 {h['total']}, {h['sides']}面×{h['count']}次)" for i, h in enumerate(recent)]
    return f"最近 {len(recent)} 条掷骰历史：\n" + "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
