# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows 沙箱运行时 policy 副本 (user_config) 读写."""

from __future__ import annotations

import copy
import hashlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_RUNTIME_COPY_NAME = "windows-policy.runtime.yaml"


_copy_lock = threading.Lock()


def _config_dir() -> Path:
    """副本所在目录: 与 config.yaml 同目录 (<workspace>/config/)."""
    from jiuwenclaw.utils import get_config_dir  # lazy import
    return get_config_dir()


def _runtime_copy_path() -> Path:
    """运行时副本落点: <config_dir>/windows-policy.runtime.yaml (与 config.yaml 同目录)."""
    return _config_dir() / _RUNTIME_COPY_NAME


def _empty_skeleton() -> dict[str, Any]:
    """副本稀疏空骨架 (首次创建用): 只含 windows 空结构, 不 dump 基底."""
    return {
        "windows": {
            "filesystem": {
                "allow_read": [],
                "allow_write": [],
                "deny_read": [],
                "deny_write": [],
            },
            "network": {
                "disable_all": False,
                "egress": {
                    "allowed_domains": [],
                    "blocked_domains": [],
                },
            },
        }
    }


def _ensure_copy_exists() -> Path:
    """副本不存在时建稀疏空骨架 (不 dump 基底). 返回副本路径. """
    copy_p = _runtime_copy_path()
    copy_p.parent.mkdir(parents=True, exist_ok=True)
    if not copy_p.is_file():
        try:
            copy_p.write_text(
                yaml.safe_dump(_empty_skeleton(), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("已创建运行时 policy 副本骨架: %s", copy_p)
        except OSError as exc:
            logger.warning("写运行时 policy 副本 %s 失败: %s", copy_p, exc)
    return copy_p


def _load_copy() -> dict[str, Any]:
    """读副本 (不存在则建空骨架并返回)."""
    with _copy_lock:
        p = _ensure_copy_exists()
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("读副本 %s 失败: %s", p, exc)
            return copy.deepcopy(_empty_skeleton())
    if not isinstance(data, dict):
        return copy.deepcopy(_empty_skeleton())
    # 补齐结构
    skel = _empty_skeleton()
    win = data.setdefault("windows", {})
    win.setdefault("filesystem", {})
    win.setdefault("network", {})
    win["network"].setdefault("disable_all", False)
    win["network"].setdefault("egress", {})
    for k in ("allow_read", "allow_write", "deny_read", "deny_write"):
        win["filesystem"].setdefault(k, [])
    for k in ("allowed_domains", "blocked_domains"):
        win["network"]["egress"].setdefault(k, [])
    return data


def _save_copy(data: dict[str, Any]) -> None:
    p = _runtime_copy_path()
    with _copy_lock:
        try:
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            os.replace(tmp, p)
        except OSError as exc:
            logger.warning("写副本 %s 失败: %s", p, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _norm_str_list(values: list[Any]) -> list[str]:
    return [str(v) for v in values if str(v).strip()]


def _validate_file_path(value: str) -> str:
    """校验单个文件路径白/黑名单条目, 返回规范化后的绝对路径."""
    from urllib.parse import unquote
    s = str(value).strip()
    if not s:
        raise ValueError("empty path")
    decoded = unquote(s)
    if "\x00" in s or "\x00" in decoded or any(ord(c) < 32 and c not in "\t" for c in s):
        raise ValueError(f"path contains control characters: {s!r}")
    # 绝对路径校验
    from pathlib import PureWindowsPath, PurePosixPath
    is_abs = PureWindowsPath(s).is_absolute() or PurePosixPath(s).is_absolute()
    if not is_abs:
        raise ValueError(f"path must be absolute: {s!r}")
    return s


def _norm_file_paths(values: list[Any]) -> list[str]:
    """规范化并校验文件路径列表 (白/黑名单). 不合格条目记 warning 跳过, 不整体失败."""
    result: list[str] = []
    for v in values:
        try:
            p = _validate_file_path(v)
            if p not in result:
                result.append(p)
        except ValueError as exc:
            logger.warning("[sandbox.files] 跳过非法路径条目: %s", exc)
    return result


_DOMAIN_RE = re.compile(
    r"^(?:\*\.)?"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,}$"
)


def _validate_domain(value: str) -> str:
    """校验单个域名白/黑名单条目, 返回原样字符串 (WFP/win_proxy 按域名比对)."""
    s = str(value).strip().lower()
    if not s:
        raise ValueError("empty domain")
    if "\x00" in s or any(ord(c) < 32 for c in s):
        raise ValueError(f"domain contains control characters: {value!r}")
    # 拒绝含端口/路径/查询的串 (WFP 域名条件不含这些, 误配会被静默不匹配).
    if any(c in s for c in ":/?#"):
        raise ValueError(f"domain must not contain port/path/query: {value!r}")
    if not _DOMAIN_RE.match(s):
        raise ValueError(f"invalid domain format: {value!r}")
    return s


def _norm_domains(values: list[Any]) -> list[str]:
    """规范化并校验域名列表. 不合格条目记 warning 跳过."""
    result: list[str] = []
    for v in values:
        try:
            d = _validate_domain(v)
            if d not in result:
                result.append(d)
        except ValueError as exc:
            logger.warning("[sandbox.network] 跳过非法域名条目: %s", exc)
    return result


# ----------------------------------------------------------------------------
# get / set: 读写副本的 windows 段 (用户配置原始值, 不含基底)
# ----------------------------------------------------------------------------

def get_sandbox_files_config() -> dict[str, Any]:
    """返回用户文件白/黑名单 (副本 windows.filesystem, 不含基底必需集)."""
    data = _load_copy()
    fs = data.get("windows", {}).get("filesystem", {})
    return {
        "allow": _norm_str_list(fs.get("allow_read") or []),
        "deny": _norm_str_list(fs.get("deny_read") or []),
    }


def set_sandbox_files_config(allow: list[Any], deny: list[Any]) -> dict[str, Any]:
    """整体替换用户文件白/黑名单."""
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise ValueError("allow and deny must be lists")
    allow_norm = _norm_file_paths(allow)
    deny_norm = _norm_file_paths(deny)
    data = _load_copy()
    fs = data["windows"]["filesystem"]
    fs["allow_read"] = list(allow_norm)
    fs["allow_write"] = list(allow_norm)
    fs["deny_read"] = list(deny_norm)
    fs["deny_write"] = list(deny_norm)
    _save_copy(data)
    return {"allow": allow_norm, "deny": deny_norm}


def get_sandbox_network_config() -> dict[str, Any]:
    """返回用户网络配置 (副本 windows.network)."""
    data = _load_copy()
    net = data.get("windows", {}).get("network", {})
    eg = net.get("egress", {})
    return {
        "disable_all": bool(net.get("disable_all", False)),
        "allow_domains": _norm_str_list(eg.get("allowed_domains") or []),
        "deny_domains": _norm_str_list(eg.get("blocked_domains") or []),
    }


def set_sandbox_network_config(
    disable_all: bool,
    allow_domains: list[Any],
    deny_domains: list[Any],
) -> dict[str, Any]:
    """整体替换用户网络配置."""
    if not isinstance(disable_all, bool):
        raise ValueError("disable_all must be boolean")
    if not isinstance(allow_domains, list) or not isinstance(deny_domains, list):
        raise ValueError("allow_domains and deny_domains must be lists")
    allow_norm = _norm_domains(allow_domains)
    deny_norm = _norm_domains(deny_domains)
    data = _load_copy()
    net = data["windows"]["network"]
    net["disable_all"] = disable_all
    net["egress"]["allowed_domains"] = list(allow_norm)
    net["egress"]["blocked_domains"] = list(deny_norm)
    _save_copy(data)
    return {
        "disable_all": disable_all,
        "allow_domains": allow_norm,
        "deny_domains": deny_norm,
    }


def fingerprint_runtime_policy() -> str | None:
    """副本内容指纹 (sha256), 供 JiuwenBoxRunner 判断是否需重 spawn."""
    p = _runtime_copy_path()
    if not p.is_file():
        return None
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError as exc:
        logger.debug("计算副本指纹失败: %s", exc)
        return None


__all__ = [
    "fingerprint_runtime_policy",
    "get_sandbox_files_config",
    "set_sandbox_files_config",
    "get_sandbox_network_config",
    "set_sandbox_network_config",
]
