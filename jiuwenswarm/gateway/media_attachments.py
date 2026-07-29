# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for normalizing browser-uploaded media attachments."""

from __future__ import annotations

import base64
import binascii
import re
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any

from jiuwenswarm.common.utils import get_agent_sessions_dir

_SAFE_FILENAME_RE = re.compile(r"[\x00-\x1f\x7f/\\]+")
_SESSION_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")

_SUPPORTED_IMAGE_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_COUNT = 8

_SUPPORTED_DOCUMENT_MIME_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/zip": ".zip",
}
_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_MAX_DOCUMENT_COUNT = 8


def normalize_chat_media_attachments(params: dict[str, Any], session_id: str | None) -> None:
    """Validate browser media_items, persist images/documents, and enrich the chat params.

    The frontend sends files as base64 for cross-platform browser compatibility.
    The gateway stores files under the current session uploads directory (reusing
    the 30-day cleanup mechanism) and returns structured file records. Downstream
    multimodal rails can load images from these paths without sending long base64
    payloads through normal text context. Document files are stored but NOT
    auto-parsed; only path/MIME annotations are provided to the agent/LLM.
    """

    raw_items = params.get("media_items")
    if not isinstance(raw_items, list) or not raw_items:
        return

    stored_images: list[dict[str, Any]] = []
    stored_documents: list[dict[str, Any]] = []
    img_idx = 0
    doc_idx = 0
    for item in raw_items[:_MAX_IMAGE_COUNT + _MAX_DOCUMENT_COUNT]:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "image":
            if img_idx >= _MAX_IMAGE_COUNT:
                continue
            stored_item = _store_image_item(item, session_id=session_id, index=img_idx)
            if stored_item:
                stored_images.append(stored_item)
                img_idx += 1
        elif item_type == "document":
            if doc_idx >= _MAX_DOCUMENT_COUNT:
                continue
            stored_item = _store_document_item(item, session_id=session_id, index=doc_idx)
            if stored_item:
                stored_documents.append(stored_item)
                doc_idx += 1

    all_stored = stored_images + stored_documents
    if not all_stored:
        params.pop("media_items", None)
        return

    params["media_items"] = all_stored
    files = params.get("files")
    if not isinstance(files, dict):
        files = {}
    if stored_images:
        files["uploaded_images"] = [
            {
                "filename": item.get("filename"),
                "path": item.get("path"),
                "mime_type": item.get("mime_type"),
                "size_bytes": item.get("size_bytes"),
            }
            for item in stored_images
        ]
    if stored_documents:
        files["uploaded_documents"] = [
            {
                "filename": item.get("filename"),
                "path": item.get("path"),
                "mime_type": item.get("mime_type"),
                "size_bytes": item.get("size_bytes"),
                "url": item.get("url"),
            }
            for item in stored_documents
        ]
    params["files"] = files


def _store_image_item(item: dict[str, Any], *, session_id: str | None, index: int) -> dict[str, Any] | None:
    mime_type = str(item.get("mimeType") or item.get("mime_type") or "").lower().strip()
    suffix = _SUPPORTED_IMAGE_MIME_TYPES.get(mime_type)
    if suffix is None:
        return None

    raw_base64 = item.get("base64Data") or item.get("base64_data")
    if not isinstance(raw_base64, str) or not raw_base64.strip():
        return None
    data: bytes | None = None
    with suppress(binascii.Error):
        data = base64.b64decode(raw_base64, validate=True)
    if not data or len(data) > _MAX_IMAGE_BYTES:
        return None

    safe_session_id = _safe_session_id(session_id)
    upload_dir = get_agent_sessions_dir() / safe_session_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(
        str(item.get("filename") or f"image-{index + 1}{suffix}"),
        fallback=f"image-{index + 1}{suffix}",
    )
    if Path(filename).suffix.lower() not in set(_SUPPORTED_IMAGE_MIME_TYPES.values()):
        filename = f"{filename}{suffix}"
    path = _unique_path(upload_dir / filename)
    path.write_bytes(data)

    return {
        "type": "image",
        "filename": path.name,
        "mime_type": mime_type,
        "path": str(path),
        "size_bytes": len(data),
    }


def _store_document_item(item: dict[str, Any], *, session_id: str | None, index: int) -> dict[str, Any] | None:
    """Store a document file to sessions/{sid}/uploads/ and return metadata.

    If the item has a ``targetPath`` field, the file is moved there after
    initial storage. The target path is validated to prevent path traversal.
    """
    mime_type = str(item.get("mimeType") or item.get("mime_type") or "").lower().strip()
    suffix = _SUPPORTED_DOCUMENT_MIME_TYPES.get(mime_type)
    if suffix is None:
        return None

    raw_base64 = item.get("base64Data") or item.get("base64_data")
    if not isinstance(raw_base64, str) or not raw_base64.strip():
        return None
    data: bytes | None = None
    with suppress(binascii.Error):
        data = base64.b64decode(raw_base64, validate=True)
    if not data or len(data) > _MAX_DOCUMENT_BYTES:
        return None

    safe_session_id = _safe_session_id(session_id)
    upload_dir = get_agent_sessions_dir() / safe_session_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(
        str(item.get("filename") or f"document-{index + 1}{suffix}"),
        fallback=f"document-{index + 1}{suffix}",
    )
    if Path(filename).suffix.lower() not in set(_SUPPORTED_DOCUMENT_MIME_TYPES.values()):
        filename = f"{filename}{suffix}"
    path = _unique_path(upload_dir / filename)
    path.write_bytes(data)

    result: dict[str, Any] = {
        "type": "document",
        "filename": path.name,
        "mime_type": mime_type,
        "path": str(path),
        "size_bytes": len(data),
    }

    # If user specified a fixed target path, move the file there
    target_path_raw = item.get("targetPath") or item.get("target_path")
    if isinstance(target_path_raw, str) and target_path_raw.strip():
        target_path = _validate_target_path(target_path_raw)
        if target_path is not None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with suppress(shutil.Error):
                shutil.move(str(path), str(target_path))
                result["path"] = str(target_path)
                result["filename"] = target_path.name

    return result


def _safe_session_id(session_id: str | None) -> str:
    text = str(session_id or "default").strip() or "default"
    return _SESSION_ID_RE.sub("_", text)[:120]


def _safe_filename(filename: str, *, fallback: str) -> str:
    name = Path(filename).name.strip()
    if not name or name in {".", ".."}:
        name = fallback
    return _SAFE_FILENAME_RE.sub("_", name)[:180]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(1, 1000):
        candidate = path.with_name(f"{stem}-{idx}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}-overflow{suffix}")


def _validate_target_path(raw_path: str) -> Path | None:
    """Validate a user-specified target path to prevent path traversal.

    Only allows absolute paths under the agent root directory or the
    workspace directory. Returns None if the path is unsafe.
    """
    from jiuwenswarm.common.utils import get_agent_root_dir, get_agent_workspace_dir

    try:
        target = Path(raw_path).expanduser().resolve()
    except (ValueError, OSError):
        return None

    # Allow paths under agent root or workspace
    allowed_roots = [
        get_agent_root_dir().resolve(),
        get_agent_workspace_dir().resolve(),
    ]
    for root in allowed_roots:
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue

    return None
