# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RSI 产物文件浏览服务（目录树 + 单文件读取）。"""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiArtifactNotFound,
    RsiBadRequest,
    RsiPathInvalid,
    RsiTaskNotFound,
)


_MAX_PREVIEW_BYTES = 10 * 1024 * 1024
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_TEXT_SUFFIXES = {
    ".bib", ".css", ".htm", ".html", ".js", ".json", ".jsx", ".md", ".markdown",
    ".py", ".sty", ".tex", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}


class RsiArtifactFilesService:
    """按 RSI tasks 根目录约束读取节点产物目录和文件。"""

    def __init__(self, store: Any) -> None:
        self.store = store

    def list_files(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")

        target = self._resolve_path(params.get("path"), task_id)
        if not target.exists():
            raise RsiArtifactNotFound(f"产物路径不存在: {target}")
        initial_path = target if target.is_file() else None
        root = target if target.is_dir() else target.parent

        files: list[dict[str, Any]] = []
        for item in root.rglob("*"):
            if "__MACOSX" in item.parts:
                continue
            files.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "isDirectory": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                    "type": self._mime_type(item),
                }
            )
        files.sort(key=lambda item: (not item["isDirectory"], item["name"].lower()))
        return {
            "root": str(root),
            "initial_path": str(initial_path) if initial_path is not None else None,
            "files": files,
        }

    def read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or "").strip()
        if not task_id:
            raise RsiBadRequest("task_id 必填")

        target = self._resolve_path(params.get("path"), task_id)
        if not target.is_file():
            raise RsiArtifactNotFound(f"产物文件不存在: {target}")
        size = target.stat().st_size
        if size > _MAX_PREVIEW_BYTES:
            raise RsiBadRequest("产物文件超出预览大小限制")

        content_bytes = target.read_bytes()
        if target.suffix.lower() in _TEXT_SUFFIXES:
            content = content_bytes.decode("utf-8", errors="replace")
            encoding = "text"
        else:
            content = base64.b64encode(content_bytes).decode("ascii")
            encoding = "base64"
        return {
            "path": str(target),
            "name": target.name,
            "size": size,
            "type": self._mime_type(target),
            "encoding": encoding,
            "content": content,
        }

    def _resolve_path(self, raw_path: Any, task_id: str) -> Path:
        path_value = str(raw_path or "").strip()
        if not path_value:
            raise RsiBadRequest("path 必填")
        task_root = self._resolve_task_root(task_id)
        candidate = Path(path_value).expanduser()
        if not candidate.is_absolute():
            candidate = task_root / candidate
        candidate = candidate.resolve()
        if candidate == task_root:
            return candidate
        try:
            candidate.relative_to(task_root)
        except ValueError as exc:
            raise RsiPathInvalid("产物路径超出当前任务目录") from exc
        return candidate

    def _resolve_task_root(self, task_id: str) -> Path:
        if not _TASK_ID_RE.fullmatch(task_id):
            raise RsiTaskNotFound(task_id)
        tasks_root = Path(self.store.tasks_root).resolve()
        task_root = tasks_root / task_id
        if task_root.is_symlink() or not (task_root / "task.json").is_file():
            raise RsiTaskNotFound(task_id)
        resolved_root = task_root.resolve()
        try:
            resolved_root.relative_to(tasks_root)
        except ValueError as exc:
            raise RsiTaskNotFound(task_id) from exc
        return resolved_root

    @staticmethod
    def _mime_type(path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
