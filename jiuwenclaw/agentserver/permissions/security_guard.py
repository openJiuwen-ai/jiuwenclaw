# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Security guard: KIA + RMS checks for file reads.

Centralised implementation so that every file-read path uses the same logic:
  - PermissionEngine.check_permission  (read_file via FileSystemRail)
  - acp_output_tools.read_text_file     (read_text_file via ACP JSON-RPC)

Order: KIA first (ICPM path-based check), then RMS (local byte detection).
Degrade strategy: if ICPM is unavailable or errors, the file passes the KIA
check (degrade-to-allow). RMS detection is pure-local and never degrades.
"""

from __future__ import annotations

import os
import pathlib

# ── RMS detection (pure local, no network) ──

# Office extensions subject to RMS detection
_RMS_CHECKED_EXTENSIONS = frozenset({
    ".docx", ".xlsx", ".xlsm", ".xlsb", ".pptx",
    ".doc", ".xls", ".ppt",
})

# OLE2 magic bytes (8 bytes)
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# "DataSpaces" encoded as UTF-16LE — only present in RMS-protected OLE2 documents
_DATASPACES_UTF16LE = "DataSpaces".encode("utf-16-le")


def _resolve_path(path: str) -> str:
    """规范化路径：解析 ``..``、符号链接、相对路径为绝对规范路径。

    守卫必须用与实际读取（OS/IDE 解析后）一致的路径去查 ICPM / 比对 / 读字节，
    否则 ``..`` / 符号链接 / 相对路径会让守卫查的目录与实际读取的文件不一致，
    形成 KIA 绕过（例如守卫查 ``C:\\projects\\..\\secret`` 无 KIA，实际读取的却是
    ``C:\\secret\\kia.md``）。统一在此 realpath 后再交给 ICPM 与 RMS 检测。
    """
    try:
        return os.path.realpath(os.path.abspath(path))
    except (OSError, ValueError):
        # 路径含非法字符等异常 — 退回原值，由后续 ICPM/读取各自处理
        return path


def detect_rms_file(path: str) -> str | None:
    """Detect RMS encryption in a file. Returns reason string if RMS, None if clean."""
    resolved = _resolve_path(path)
    ext = pathlib.Path(resolved).suffix.lower()
    if ext not in _RMS_CHECKED_EXTENSIONS:
        return None
    try:
        with open(resolved, "rb") as f:
            header = f.read(8)
        if not header.startswith(_OLE2_MAGIC):
            return None
        # OLE2 file — read more to check for DataSpaces storage (RMS indicator)
        with open(resolved, "rb") as f:
            chunk = f.read(8192)
        if _DATASPACES_UTF16LE in chunk:
            return f"RMS-encrypted Office file: {ext}"
    except OSError:
        pass  # Cannot read — degrade to allow
    return None


# ── KIA detection (ICPM HTTP service) ──


async def check_kia_file(path: str) -> bool:
    """Check if a file is KIA-classified via ICPM. Returns True if KIA.

    Mirrors the TypeScript icpm-client.ts logic:
      1. Get parent directory of the file
      2. Call POST /api/queryDirKiaPaths with that directory
      3. Check if the file path appears in the returned kiaPaths list

    Controlled by KIA_GUARD_ENABLED env var. Degrades to allow (returns False)
    if ICPM is unavailable or errors.
    """
    if os.environ.get("KIA_GUARD_ENABLED", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        import http.client
        import json

        # 先规范化路径（解析 .. / 符号链接 / 相对路径），确保守卫查的目录与
        # 实际读取的文件一致，否则 .. / symlink / 相对路径可绕过 KIA 守卫。
        resolved = _resolve_path(path)
        # ICPM service only accepts Windows backslash format paths
        # Normalize to backslash format for ICPM API
        normalized_path = resolved.replace("/", "\\")

        dir_path = _get_parent_directory(normalized_path)
        conn = http.client.HTTPConnection("127.0.0.1", 32200, timeout=3)
        body = json.dumps({"filePath": dir_path, "pageNo": 1, "pageSize": 500})
        conn.request("POST", "/api/queryDirKiaPaths", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status == 200:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") != "success" or not data.get("isExistKia"):
                return False
            kia_paths = data.get("kiaPaths", [])
            return _is_file_in_kia_list(normalized_path, kia_paths)
    except Exception:
        pass  # ICPM unavailable — degrade to allow
    return False


def _get_parent_directory(file_path: str) -> str:
    """Get parent directory with trailing separator (matches ICPM directory matching)."""
    parent = os.path.dirname(file_path)
    if parent and not parent.endswith(("\\", "/")):
        sep = "\\" if "\\" in file_path else "/"
        return parent + sep
    return parent


def _is_file_in_kia_list(file_path: str, kia_paths: list) -> bool:
    """Check if file_path appears in kia_paths (normalised for comparison)."""
    if not kia_paths:
        return False
    normalize = lambda p: p.lower().replace("/", "\\")
    target = normalize(file_path)
    return any(normalize(kp) == target for kp in kia_paths)


# Tool names that read file content and must go through KIA+RMS guards.
_FILE_READ_TOOLS = frozenset({"read_file", "read_text_file"})


def extract_file_path_from_tool_args(tool_name: str, tool_args: dict) -> str | None:
    """Extract the file path from a file-read tool's arguments.

    read_file uses ``file_path`` or ``path``; read_text_file uses ``path``.
    """
    if tool_name not in _FILE_READ_TOOLS:
        return None
    if not isinstance(tool_args, dict):
        return None
    # read_file supports both file_path and path; read_text_file uses path
    path = tool_args.get("file_path") or tool_args.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


__all__ = [
    "detect_rms_file",
    "check_kia_file",
    "extract_file_path_from_tool_args",
]
