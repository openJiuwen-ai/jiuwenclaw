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
    """掷骰子。掷一个 sides 面的骰子 count 次，返回每次点数，并记录到历史。

    用于随机决策、游戏、抽样选择。和 bash 的 RANDOM 不同：本工具持久化每次结果到历史，
    后续可用 get_roll_history 回看。

    Args:
        sides: 骰子面数（如 6 面、20 面），默认 6。
        count: 掷几次，默认 1。

    Returns:
        每次点数 + 总和的字符串。
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
    """查看掷骰历史记录。返回最近 limit 次的掷骰结果。

    用于回看之前 roll_dice 的记录。历史持久化在本地文件，跨会话保留。

    Args:
        limit: 返回最近多少条，默认 10。

    Returns:
        历史记录的字符串。
    """
    history = _load_history()
    if not history:
        return "暂无掷骰历史"
    recent = history[-limit:]
    lines = [f"- {i+1}. {h['results']} (总和 {h['total']}, {h['sides']}面×{h['count']}次)" for i, h in enumerate(recent)]
    return f"最近 {len(recent)} 条掷骰历史：\n" + "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
