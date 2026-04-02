# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""File operation tools implemented with openjiuwen @tool style."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.utils import get_agent_root_dir, get_workspace_dir


_MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB

_BINARY_EXTENSIONS = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".pyc", ".pyo", ".class", ".o", ".obj",
})

_ALLOWED_ROOTS: tuple[Path, ...] = (
    get_agent_root_dir(),   # ~/.jiuwenclaw/agent  (covers workspace, skills, home, memory)
    get_workspace_dir(),    # fallback: resolved workspace dir
)


def _resolve_file_path(file_path: str) -> Path:
    # Default behavior: allow any resolved path.
    # Set JIUWENCLAW_RESTRICT_FILE_PATH=1/true/on to enable sandbox restriction to _ALLOWED_ROOTS.
    restrict_to_allowed_roots = os.getenv("JIUWENCLAW_RESTRICT_FILE_PATH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    default_root = get_workspace_dir()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = default_root / candidate
    candidate = candidate.resolve()
    if not restrict_to_allowed_roots:
        return candidate
    for root in _ALLOWED_ROOTS:
        try:
            candidate.relative_to(root.resolve())
            return candidate
        except ValueError:
            continue
    raise ValueError(f"Path is outside allowed directories: {candidate}")


def _is_binary_path(path: Path) -> bool:
    return path.suffix.lower() in _BINARY_EXTENSIONS


@tool(
    name="write_file",
    description=(
        "Create or overwrite a file with the given content. "
        "file_path can be relative (resolved from workspace) or any absolute path. "
        "Set JIUWENCLAW_RESTRICT_FILE_PATH=true to restrict writes to agent/workspace roots. "
        "Parent directories are created automatically. "
        "Refuses to write binary files. "
        "Use create_only=true to prevent overwriting existing files. "
        "Returns JSON with status and file path."
    ),
)
async def write_file(
    file_path: str,
    content: str,
    encoding: str = "utf-8",
    create_only: bool = False,
) -> str:
    file_path_str = (file_path or "").strip()
    if not file_path_str:
        return json.dumps({"error": "file_path cannot be empty."}, ensure_ascii=False)

    try:
        resolved = _resolve_file_path(file_path_str)
    except (ValueError, Exception):
        return json.dumps({"error": "file_path is outside allowed agent directories."}, ensure_ascii=False)

    if _is_binary_path(resolved):
        return json.dumps(
            {"error": f"Refusing to write binary file type: {resolved.suffix}"},
            ensure_ascii=False,
        )

    if create_only and resolved.exists():
        return json.dumps(
            {"error": f"File already exists: {resolved}. Set create_only=false to overwrite."},
            ensure_ascii=False,
        )

    content_bytes = (content or "").encode(encoding, errors="replace")
    if len(content_bytes) > _MAX_FILE_SIZE:
        return json.dumps(
            {"error": f"Content too large ({len(content_bytes)} bytes). Max allowed: {_MAX_FILE_SIZE} bytes."},
            ensure_ascii=False,
        )

    try:
        def _write() -> dict:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content or "", encoding=encoding, errors="replace")
            return {
                "status": "ok",
                "file_path": str(resolved),
                "bytes_written": len(content_bytes),
            }

        result = await asyncio.to_thread(_write)
    except Exception as exc:
        return json.dumps({"error": f"Failed to write file: {exc}"}, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False, indent=2)
