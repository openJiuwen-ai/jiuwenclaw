#!/usr/bin/env python3
# coding: utf-8
"""gitcode-repo.json 加载与工作区解析。"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_NAME = "gitcode-repo.json"
LEGACY_CONFIG_NAME = "issue-resolver.json"
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class ConfigError(ValueError):
    """配置文件或工作区选择错误。"""


def find_config_path(config_path: str = "") -> str:
    """解析配置文件路径；未指定时在常见位置自动查找。"""
    if config_path:
        return config_path

    for name in (DEFAULT_CONFIG_NAME, LEGACY_CONFIG_NAME):
        if os.path.exists(name):
            return name

    for name in (DEFAULT_CONFIG_NAME, LEGACY_CONFIG_NAME):
        candidate = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", name))
        if os.path.exists(candidate):
            return candidate

    return ""


def load_raw_config(config_path: str) -> Dict[str, Any]:
    """读取原始 JSON 配置。"""
    if not config_path:
        return {}
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象")
    return data


def _workspace_names(workspaces: List[Dict[str, Any]]) -> List[str]:
    return [str(w.get("name") or f"<unnamed-{i}>") for i, w in enumerate(workspaces)]


def resolve_workspace_config(
    raw: Dict[str, Any],
    workspace_name: Optional[str] = None,
) -> Dict[str, Any]:
    """将多工作区配置解析为单工作区有效配置。

    优先级：
    1. 若存在非空 ``workspaces`` 列表，从中选取条目（``--workspace`` 或唯一条目）。
    2. 否则回退到顶层扁平 ``upstream`` / ``fork`` / ``local_repo``（旧格式）。
    """
    workspaces = raw.get("workspaces") or []
    if workspaces:
        if workspace_name:
            matched = [
                w for w in workspaces
                if w.get("name") == workspace_name
            ]
            if not matched:
                available = _workspace_names(workspaces)
                raise ConfigError(
                    f"未找到工作区 {workspace_name!r}，"
                    f"可用: {available}"
                )
            ws = matched[0]
        elif len(workspaces) == 1:
            ws = workspaces[0]
        else:
            available = _workspace_names(workspaces)
            raise ConfigError(
                f"配置文件含 {len(workspaces)} 个工作区，"
                f"请使用 --workspace 指定其一: {available}"
            )

        effective: Dict[str, Any] = {
            "gitcode_token": raw.get("gitcode_token", ""),
            "upstream": ws.get("upstream", {}),
            "fork": ws.get("fork", {}),
            "local_repo": ws.get("local_repo", {}),
            "_workspace_name": ws.get("name", ""),
        }
        if "poller" in ws:
            effective["poller"] = ws["poller"]
        return effective

    upstream = raw.get("upstream") or {}
    if upstream.get("owner") and upstream.get("repo"):
        return raw

    raise ConfigError(
        "配置无效：请填写 workspaces[] 或顶层 upstream.owner/upstream.repo"
    )


def load_resolved_config(
    config_path: str = "",
    workspace_name: Optional[str] = None,
) -> Dict[str, Any]:
    """加载并解析为单工作区有效配置。"""
    path = find_config_path(config_path)
    raw = load_raw_config(path)
    if not raw:
        return {}
    return resolve_workspace_config(raw, workspace_name)


def exit_on_config_error(exc: ConfigError) -> None:
    """以 JSON 错误格式退出 CLI。"""
    print(
        json.dumps({"error": str(exc)}, ensure_ascii=False),
        file=sys.stderr,
    )
    sys.exit(1)
