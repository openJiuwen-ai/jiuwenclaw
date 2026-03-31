# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""模式匹配器 - 仅支持 wildcard 模式；含权限规则持久化.

wildcard 模式：
- * → .*  (零个或多个)
- ? → .   (恰好一个)
- 正则元字符转义
- " *" 结尾 → ( .*)? 便于 "ls *" 匹配 "ls" 或 "ls -la"
- 全串锚定 ^...$ 防注入
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# 限制性字符类：仅允许命令参数和路径常见字符，排除 ; | & ` < > $ 等 shell 元字符防注入
# - 置于开头避免被解析为范围
_WILDCARD_CHARS = r'[-a-zA-Z0-9 \._/:"\']'


def match_wildcard(value: str, pattern: str) -> bool:
    """通配符匹配.

    - * → 限制性字符类* (排除 shell 元字符，防命令拼接)
    - ? → 限制性字符类 (恰好一个)
    - 正则元字符转义
    - " *" 结尾 → ( 字符类*)? 使 "ls *" 可匹配 "ls" 或 "ls -la"
    - 全串锚定 ^...$ 防止 "git status; rm -rf /" 匹配 "git status *"

    Args:
        value: 被匹配字符串（来自工具输入）
        pattern: 通配符模式（来自配置，可信）

    Returns:
        是否匹配
    """
    if not pattern or not value:
        return False
    val = value.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    # 1. 转义正则特殊字符（* 和 ? 保留，后续单独处理）
    to_escape = set(".+^${}()|[]\\")
    escaped = "".join("\\" + c if c in to_escape else c for c in pat)
    # 2. 先替换 ?（必须在 * 之前，否则会误替换 ")? " 中的 ?）
    escaped = escaped.replace("?", _WILDCARD_CHARS)
    # 3. * → 限制性字符类*
    if escaped.endswith(" *"):
        escaped = escaped[:-2] + "( " + _WILDCARD_CHARS + "*)?"
    else:
        escaped = escaped.replace("*", _WILDCARD_CHARS + "*")
    # 3. 全串锚定
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    try:
        return bool(re.match("^" + escaped + "$", val, flags))
    except re.error:
        return False




class PatternMatcher:
    """模式匹配器 - 仅支持 wildcard 模式 (*, ?)."""

    @staticmethod
    def match(pattern: str, value: str) -> bool:
        if not pattern or not value:
            return False
        return match_wildcard(value, pattern)

    def match_any(self, patterns: list[str], value: str) -> bool:
        """匹配任意一个模式."""
        return any(self.match(p, value) for p in patterns)


class PathMatcher:
    """路径匹配器."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_path(self, pattern: str, path: str | Path) -> bool:
        """匹配文件路径 (规范化分隔符后再比较)."""
        normalized_path = str(path).replace("\\", "/")
        normalized_pattern = pattern.replace("\\", "/")

        if self._pm.match(normalized_pattern, normalized_path):
            return True

        # 尝试匹配父目录层级
        path_obj = Path(str(path))
        for parent in path_obj.parents:
            parent_str = str(parent).replace("\\", "/")
            if self._pm.match(normalized_pattern, parent_str):
                return True
            if self._pm.match(normalized_pattern, parent_str + "/"):
                return True
            if self._pm.match(normalized_pattern, parent_str + "/*"):
                return True
        return False

    def match_path_any(self, patterns: list[str], path: str | Path) -> bool:
        return any(self.match_path(p, path) for p in patterns)


class URLMatcher:
    """URL 匹配器."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_url(self, pattern: str, url: str) -> bool:
        """匹配 URL (支持 hostname、netloc、full URL)."""
        if not url:
            return False
        if self._pm.match(pattern, url):
            return True
        try:
            parsed = urlparse(url)
            if self._pm.match(pattern, parsed.hostname or ""):
                return True
            if self._pm.match(pattern, parsed.netloc):
                return True
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            if self._pm.match(pattern, base_url):
                return True
            if self._pm.match(pattern, base_url + "/*"):
                return True
        except Exception:
            return False
        return False

    def match_url_any(self, patterns: list[str], url: str) -> bool:
        return any(self.match_url(p, url) for p in patterns)


class CommandMatcher:
    """命令匹配器 - 仅支持 wildcard，全串锚定防注入."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_command(self, pattern: str, command: str) -> bool:
        """匹配命令字符串 (wildcard 模式，全串锚定)."""
        if not command:
            return False
        return self._pm.match(pattern, command)

    def match_command_any(self, patterns: list[str], command: str) -> bool:
        return any(self.match_command(p, command) for p in patterns)


# ----- 全局便捷函数 -----
_pattern_matcher = PatternMatcher()
_path_matcher = PathMatcher()
_url_matcher = URLMatcher()
_command_matcher = CommandMatcher()


def match_pattern(pattern: str, value: str) -> bool:
    return _pattern_matcher.match(pattern, value)


def match_path(pattern: str, path: str | Path) -> bool:
    return _path_matcher.match_path(pattern, path)


def match_url(pattern: str, url: str) -> bool:
    return _url_matcher.match_url(pattern, url)


def match_command(pattern: str, command: str) -> bool:
    return _command_matcher.match_command(pattern, command)


def build_command_allow_pattern(cmd: str) -> str:
    """构建匹配完整命令的通配符模式.

    Examples:
        "start chrome"   → start chrome *
        "npm install"    → npm install *
        "ls"             → ls *
    """
    return cmd.strip() + " *"


def contains_path(parent: str | Path, child: str | Path) -> bool:
    """子路径是否在父路径下（含路径穿越防护）.
    """
    import os
    try:
        rel = os.path.relpath(Path(child).resolve(), Path(parent).resolve())
        return not rel.startswith("..") and rel != ".."
    except (ValueError, OSError):
        return False


# ---------- 权限规则持久化 ----------


def persist_permission_allow_rule(tool_name: str, tool_args: dict | str) -> None:
    """用户选择「总是允许」时，将 allow 规则写入 config.yaml.

    For mcp_exec_command with a command arg, adds a wildcard pattern.
    For other tools, sets the tool to 'allow'.
    """
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except Exception:
            tool_args = {}

    logger.info(
        "[Persist] START tool_name=%s tool_args_type=%s tool_args=%s",
        tool_name, type(tool_args).__name__, str(tool_args)[:200],
    )

    try:
        from jiuwenclaw.agentserver.deep_agent.permissions.core import get_permission_engine
        from jiuwenclaw.config import (
            _CONFIG_YAML_PATH,
            _load_yaml_round_trip,
            _dump_yaml_round_trip,
        )

        logger.info("[Persist] Config path: %s", _CONFIG_YAML_PATH)
        data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
        permissions = data.get("permissions")
        if permissions is None:
            logger.warning("[Persist] ABORT: No 'permissions' section in config")
            return
        tools_section = permissions.get("tools")
        if tools_section is None:
            permissions["tools"] = {}
            tools_section = permissions["tools"]

        if tool_name == "mcp_exec_command":
            cmd = str(tool_args.get("command", tool_args.get("cmd", "")))
            logger.info("[Persist] Extracted command: '%s'", cmd)
            if cmd:
                new_pattern = build_command_allow_pattern(cmd)
                logger.info("[Persist] Built pattern: %s", new_pattern)

                tool_entry = tools_section.get("mcp_exec_command")
                if not isinstance(tool_entry, dict):
                    tools_section["mcp_exec_command"] = {"*": "ask", "patterns": {}}
                    tool_entry = tools_section["mcp_exec_command"]

                patterns = tool_entry.get("patterns")
                if patterns is None:
                    tool_entry["patterns"] = {}
                    patterns = tool_entry["patterns"]

                if isinstance(patterns, dict):
                    if new_pattern in patterns:
                        logger.info("[Persist] Pattern already exists, skip")
                        return
                    patterns[new_pattern] = "allow"
                else:
                    for p in patterns:
                        if isinstance(p, dict) and p.get("pattern") == new_pattern:
                            logger.info("[Persist] Pattern already exists, skip")
                            return
                    patterns.append({"pattern": new_pattern, "permission": "allow"})
                logger.info("[Persist] Appended pattern: %s", new_pattern)
            else:
                tools_section["mcp_exec_command"] = "allow"
                logger.info("[Persist] Set mcp_exec_command = allow (no command)")
        else:
            tools_section[tool_name] = "allow"
            logger.info("[Persist] Set %s = allow", tool_name)

        _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)
        logger.info("[Persist] YAML written to disk")

        verify_data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
        engine = get_permission_engine()
        engine.update_config(verify_data.get("permissions", {}))
        logger.info("[Persist] Engine hot-reloaded")

    except Exception:
        logger.error("[Persist] FAILED to persist permission allow rule", exc_info=True)


def persist_external_directory_allow(paths: list[str]) -> None:
    """用户选择「总是允许」外部路径时，写入 external_directory 配置."""
    if not paths:
        return
    logger.info("[Persist] external_directory allow: paths=%s", paths[:3])
    try:
        from jiuwenclaw.agentserver.deep_agent.permissions.core import get_permission_engine
        from jiuwenclaw.config import (
            _CONFIG_YAML_PATH,
            _load_yaml_round_trip,
            _dump_yaml_round_trip,
        )
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
        permissions = data.get("permissions")
        if permissions is None:
            permissions = {}
            data["permissions"] = permissions
        ext_cfg = permissions.get("external_directory")
        if not isinstance(ext_cfg, dict):
            ext_cfg = {"*": "ask"}
            permissions["external_directory"] = ext_cfg
        for path_str in paths:
            path_norm = path_str.replace("\\", "/").rstrip("/")
            parent = str(Path(path_norm).parent).replace("\\", "/")
            key = parent if parent and parent != "." else path_norm
            if key not in ext_cfg or ext_cfg[key] != "allow":
                ext_cfg[DoubleQuotedScalarString(key)] = DoubleQuotedScalarString("allow")
                logger.info("[Persist] Added external_directory[%s] = allow", key)
        _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)
        engine = get_permission_engine()
        engine.update_config(data.get("permissions", {}))
        logger.info("[Persist] external_directory written, engine hot-reloaded")
    except Exception:
        logger.error("[Persist] FAILED to persist external_directory allow", exc_info=True)

