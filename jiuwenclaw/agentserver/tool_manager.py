# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ToolManager - tools.add 等 RPC：落盘用户 MCP 工具配置并交给 mcp_toolkits 生成 Tool。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from openjiuwen.core.runner import Runner

from jiuwenclaw.utils import get_agent_tools_dir

from jiuwenclaw.agentserver.tools.mcp_toolkits import create_mcp_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 落盘 JSON 模板：列表顺序即写入顺序；每项为 (disk_key, default, kind)。
# kind:
#   tool_name — 使用 mcpServers 的 key，忽略 cfg 里的同名键
#   text      — 字符串；cfg 缺省用 default，非 str 则 str()
#   list      — 列表；cfg 非 list 则用 default 的拷贝
#   any       — cfg.get(source_key, default)；default 为 dict/list 时做浅拷贝
# 扩展字段：在列表末尾追加 ("new_key", default, "text"|"list"|"any") 即可。
# ---------------------------------------------------------------------------
TOOL_DISK_SCHEMA: list[tuple[str, Any, str]] = [
    ("name", "", "tool_name"),
    ("description", "", "text"),
    ("command", "", "text"),
    ("args", [], "list"),
]

# 落盘字段名 -> mcpServers 内对象中的键名（缺省表示与 disk_key 相同）
TOOL_DISK_SOURCE_MAP: dict[str, str] = {}


def _mutable_default_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _coerce_tool_disk_value(
    disk_key: str,
    default: Any,
    kind: str,
    tool_name: str,
    cfg: dict[str, Any],
) -> Any:
    src = TOOL_DISK_SOURCE_MAP.get(disk_key, disk_key)
    if kind == "tool_name":
        return (tool_name or "").strip()
    if kind == "text":
        raw = cfg.get(src, default)
        if raw is None:
            return ""
        return raw if isinstance(raw, str) else str(raw)
    if kind == "list":
        raw = cfg.get(src, default)
        return list(raw) if isinstance(raw, list) else _mutable_default_copy(default)
    if kind == "any":
        if src not in cfg:
            return _mutable_default_copy(default)
        return cfg[src]
    raise ValueError(f"TOOL_DISK_SCHEMA 未知 kind={kind!r}，字段={disk_key!r}")


def _tool_record_for_disk(tool_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """按 TOOL_DISK_SCHEMA + TOOL_DISK_SOURCE_MAP 生成落盘对象，再合并 cfg 中未消费的键。"""
    record: dict[str, Any] = {}
    sources_used: set[str] = set()

    for disk_key, default, kind in TOOL_DISK_SCHEMA:
        src = TOOL_DISK_SOURCE_MAP.get(disk_key, disk_key)
        sources_used.add(src)
        record[disk_key] = _coerce_tool_disk_value(disk_key, default, kind, tool_name, cfg)

    for key, val in cfg.items():
        if key in record or key in sources_used:
            continue
        record[key] = val

    return record


def _safe_tool_file_stem(tool_name: str) -> str:
    name = (tool_name or "").strip()
    if not name:
        raise ValueError("工具名不能为空")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"非法工具名: {tool_name!r}")
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    if not safe or not re.search(r"[a-zA-Z0-9]", safe):
        raise ValueError(f"非法工具名: {tool_name!r}")
    return safe


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


class ToolManager:
    """管理 tools 相关 RPC（与 SkillManager 的 handler 命名风格一致）。"""

    def __init__(self, get_agent: Callable[[], Any] | None = None) -> None:
        """get_agent: 返回当前 ``JiuWenClaw._instance``，用于 ``Runner.resource_mgr`` / ``ability_manager`` 注册。"""
        self._get_agent = get_agent

    async def handle_tools_add(self, params: dict) -> dict[str, Any]:
        """按工具名拆分落盘到 ``agent/tools/``；对每个工具以与落盘一致的 JSON 调用 ``create_mcp_tool`` 并注册到 Agent。

        params:
            mcp_json: str，整段 JSON 字符串；根对象须含 ``mcpServers``，
                每个 key 为工具名；落盘结构由 ``TOOL_DISK_SCHEMA`` / ``TOOL_DISK_SOURCE_MAP`` 定义，
                cfg 中未参与映射的键会追加在模板字段之后。
        """
        mcp_json = params.get("mcp_json")
        if not isinstance(mcp_json, str) or not mcp_json.strip():
            raise ValueError("缺少参数：请提供 mcp_json（JSON 字符串）")

        try:
            root = json.loads(mcp_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc

        if not isinstance(root, dict):
            raise ValueError("根节点必须是 JSON 对象")
        servers = root.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError("缺少有效的 mcpServers 对象")

        for tool_name, cfg in servers.items():
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError(f"非法的工具名: {tool_name!r}")
            if not isinstance(cfg, dict):
                raise ValueError(f"mcpServers[{tool_name!r}] 必须是对象")

        agent = self._get_agent() if self._get_agent else None
        if agent is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        saved: list[dict[str, str]] = []
        registered: list[dict[str, str]] = []
        tools_dir = get_agent_tools_dir()
        for tool_name, cfg in servers.items():
            stem = _safe_tool_file_stem(tool_name)
            out_path = tools_dir / f"{stem}.json"
            record = _tool_record_for_disk(tool_name, cfg)
            _atomic_write_json(out_path, record)
            saved.append({"name": tool_name, "path": str(out_path.resolve())})
            logger.info("[ToolManager] 已写入工具配置 name=%s path=%s", tool_name, out_path)

            single_json = json.dumps(record, ensure_ascii=False)
            tool = await create_mcp_tool(single_json)
            try:
                if not Runner.resource_mgr.get_tool(tool.card.id):
                    Runner.resource_mgr.add_tool(tool)
                agent.ability_manager.add(tool.card)
            except Exception as exc:
                logger.error("[ToolManager] 注册工具失败 name=%s: %s", tool_name, exc)
                raise
            registered.append({"name": tool.card.name, "id": tool.card.id})
            logger.info("[ToolManager] 已注册工具 name=%s id=%s", tool.card.name, tool.card.id)

        return {
            "saved": saved,
            "tools_dir": str(tools_dir.resolve()),
            "registered_tools": registered,
        }
