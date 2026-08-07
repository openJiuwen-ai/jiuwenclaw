# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for normalizing browser-uploaded document attachments."""

from __future__ import annotations

import base64
import binascii
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from jiuwenswarm.common.document_parser import (
    DEFAULT_MAX_CHARS,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    is_supported_document,
    parse_document_file,
    resolve_document_suffix,
    supported_formats,
)
from jiuwenswarm.common.utils import get_agent_sessions_dir
from jiuwenswarm.gateway.upload_storage import (
    safe_session_dirname,
    safe_upload_filename,
    unique_upload_path,
)

logger = logging.getLogger(__name__)

_MAX_DOCUMENT_BYTES = 30 * 1024 * 1024
_MAX_DOCUMENT_COUNT = 20
# Binary documents that read_file cannot open directly — persist a .txt sidecar.
_TEXT_SIDECAR_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx"})

_TRUE_STRINGS = frozenset({"true", "1", "yes"})
_FALSE_STRINGS = frozenset({"false", "0", "no"})


def coerce_document_parse_flag(value: Any, *, default: bool = True) -> bool:
    """Normalize document.persist ``parse`` flag without ``bool("false")`` pitfalls.

    - bool is returned as-is
    - strings ``true``/``false``/``1``/``0``/``yes``/``no`` (case-insensitive) are mapped
    - any other type/value falls back to ``default``
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
        return default
    return default


async def persist_and_parse_documents(
    params: dict[str, Any],
    session_id: str | None,
    *,
    parse: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Validate browser document items, persist files, optionally parse, enrich params.

    Accepts either ``params["documents"]`` or ``params["media_items"]`` entries with
    ``type == "document"``. Each item must provide base64 payload plus filename/mime.

    Mutates and returns ``params`` with:
    - ``media_items``: stored document records (path + optional parsed text)
    - ``files.uploaded_documents``: lightweight path metadata for chat.send
    """
    raw_items = _collect_document_items(params)
    if not raw_items:
        params.pop("documents", None)
        return params

    stored: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    accepted_items = raw_items[:_MAX_DOCUMENT_COUNT]
    overflow_items = raw_items[_MAX_DOCUMENT_COUNT:]
    if overflow_items:
        overflow_count = len(overflow_items)
        logger.warning(
            "[document.persist] dropping %s document(s) over limit=%s session_id=%s",
            overflow_count,
            _MAX_DOCUMENT_COUNT,
            session_id,
        )
        for index, item in enumerate(overflow_items, start=_MAX_DOCUMENT_COUNT):
            filename = ""
            if isinstance(item, dict):
                filename = str(item.get("filename") or item.get("name") or "")
            errors.append(
                {
                    "index": index,
                    "filename": filename,
                    "error": (
                        f"Document count exceeds limit ({_MAX_DOCUMENT_COUNT}); "
                        "this item was ignored"
                    ),
                    "code": "DOCUMENT_COUNT_LIMIT",
                }
            )

    for index, item in enumerate(accepted_items):
        try:
            stored_item = await _store_and_maybe_parse_item(
                item,
                session_id=session_id,
                index=index,
                parse=parse,
                max_chars=max_chars,
            )
            if stored_item:
                stored.append(stored_item)
        except Exception as exc:
            logger.exception("[document.persist] failed for item %s: %s", index, exc)
            errors.append(
                {
                    "index": index,
                    "filename": str(item.get("filename") or item.get("name") or ""),
                    "error": str(exc),
                }
            )

    if stored:
        # Keep previously persisted images if caller mixed them in; only replace documents.
        existing_media = params.get("media_items")
        kept_images: list[dict[str, Any]] = []
        if isinstance(existing_media, list):
            for entry in existing_media:
                if isinstance(entry, dict) and entry.get("type") == "image" and entry.get("path"):
                    kept_images.append(entry)
        params["media_items"] = kept_images + stored
        files = params.get("files")
        if not isinstance(files, dict):
            files = {}
        files["uploaded_documents"] = [
            {
                "filename": item.get("filename"),
                "path": item.get("path"),
                "original_path": item.get("original_path"),
                "text_path": item.get("text_path"),
                "mime_type": item.get("mime_type"),
                "size_bytes": item.get("size_bytes"),
                "parser": item.get("parser"),
                "text_truncated": item.get("text_truncated"),
            }
            for item in stored
        ]
        params["files"] = files
    else:
        params.pop("documents", None)

    if errors:
        params["document_errors"] = errors
    params["supported_formats"] = supported_formats()
    return params


async def parse_existing_document(
    path: str,
    *,
    session_id: str | None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Parse an already-persisted document path under the session uploads dir."""
    safe_path = resolve_session_upload_path(path, session_id=session_id)
    result = await parse_document_file(safe_path, max_chars=max_chars)
    return {
        "type": "document",
        "path": str(safe_path),
        "filename": safe_path.name,
        "text": result["text"],
        "text_truncated": result["truncated"],
        "parser": result["parser"],
        "file_ext": result["file_ext"],
        "char_count": result["char_count"],
        "documents_count": result["documents_count"],
    }


def session_uploads_dir(session_id: str | None) -> Path:
    """Return the canonical uploads directory for a session."""
    return (get_agent_sessions_dir() / safe_session_dirname(session_id) / "uploads").resolve()


def resolve_session_upload_path(path: str | Path, *, session_id: str | None) -> Path:
    """Resolve ``path`` and require it to stay under the session uploads directory.

    Prevents path traversal via ``../`` and symlink/junction escapes outside uploads.
    """
    text = str(path or "").strip()
    if not text:
        raise ValueError("path is required")

    upload_root = session_uploads_dir(session_id)
    try:
        resolved = Path(text).expanduser().resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"Invalid path: {text}") from exc

    try:
        resolved.relative_to(upload_root)
    except ValueError as exc:
        raise PermissionError(
            f"path must be under session uploads directory: {upload_root}"
        ) from exc

    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {resolved}")
    return resolved


def _collect_document_items(params: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    documents = params.get("documents")
    if isinstance(documents, list):
        for item in documents:
            if isinstance(item, dict):
                items.append(item)

    media_items = params.get("media_items")
    if isinstance(media_items, list):
        for item in media_items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "document" or (
                item_type is None
                and is_supported_document(
                    filename=str(item.get("filename") or item.get("name") or ""),
                    mime_type=str(item.get("mimeType") or item.get("mime_type") or ""),
                )
            ):
                items.append(item)
    return items


async def _store_and_maybe_parse_item(
    item: dict[str, Any],
    *,
    session_id: str | None,
    index: int,
    parse: bool,
    max_chars: int,
) -> dict[str, Any] | None:
    filename_hint = str(item.get("filename") or item.get("name") or "")
    mime_type = str(item.get("mimeType") or item.get("mime_type") or "").lower().strip()
    suffix = resolve_document_suffix(filename=filename_hint, mime_type=mime_type)
    if suffix is None:
        raise ValueError(
            f"Unsupported document type: filename={filename_hint!r} mime={mime_type!r}. "
            f"Supported: {supported_formats()}"
        )

    raw_base64 = item.get("base64Data") or item.get("base64_data")
    if not isinstance(raw_base64, str) or not raw_base64.strip():
        # Allow re-parse of already-persisted path without base64.
        existing_path = str(item.get("path") or "").strip()
        if existing_path:
            path = resolve_session_upload_path(existing_path, session_id=session_id)
            data_size = path.stat().st_size
            stored = {
                "type": "document",
                "filename": path.name,
                "mime_type": mime_type or _mime_from_suffix(suffix),
                "path": str(path),
                "size_bytes": data_size,
            }
            # Binary re-parse / sidecar: always materialize .txt for read_file.
            need_sidecar = path.suffix.lower() in _TEXT_SIDECAR_SUFFIXES
            if parse or need_sidecar:
                await _attach_parse_and_text_sidecar(
                    stored,
                    original_path=path,
                    parse_meta=parse,
                    max_chars=max_chars,
                )
            return stored
        raise ValueError("Missing base64Data / base64_data for document upload")

    data: bytes | None = None
    with suppress(binascii.Error):
        data = base64.b64decode(raw_base64, validate=True)
    if not data:
        raise ValueError("Invalid base64 document payload")
    if len(data) > _MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"Document exceeds size limit ({_MAX_DOCUMENT_BYTES // (1024 * 1024)}MB): "
            f"{filename_hint or f'document-{index + 1}'}"
        )

    safe_session_id = safe_session_dirname(session_id)
    upload_dir = get_agent_sessions_dir() / safe_session_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    fallback = f"document-{index + 1}{suffix}"
    filename = safe_upload_filename(filename_hint or fallback, fallback=fallback)
    if Path(filename).suffix.lower() not in SUPPORTED_DOCUMENT_EXTENSIONS:
        filename = f"{filename}{suffix}"
    path = unique_upload_path(upload_dir / filename)
    path.write_bytes(data)

    stored = {
        "type": "document",
        "filename": path.name,
        "mime_type": mime_type or _mime_from_suffix(suffix),
        "path": str(path),
        "size_bytes": len(data),
    }
    need_sidecar = path.suffix.lower() in _TEXT_SIDECAR_SUFFIXES
    if parse or need_sidecar:
        await _attach_parse_and_text_sidecar(
            stored,
            original_path=path,
            parse_meta=parse,
            max_chars=max_chars,
        )
    return stored


async def _attach_parse_and_text_sidecar(
    stored: dict[str, Any],
    *,
    original_path: Path,
    parse_meta: bool,
    max_chars: int,
) -> None:
    """Parse ``original_path``; for pdf/docx/xlsx also write a full ``.txt`` sidecar.

    After a successful sidecar write, ``stored["path"]`` points at the ``.txt``
    so Agent ``read_file`` can open it. ``original_path`` / ``text_path`` keep both.
    A PDF with no text layer (scanned) yields empty text — keep ``path`` on the
    original binary so page-level tools can still work on it.
    """
    suffix = original_path.suffix.lower()
    write_sidecar = suffix in _TEXT_SIDECAR_SUFFIXES

    if write_sidecar:
        # Full text on disk for read_file (no truncation).
        parsed_full = await parse_document_file(original_path, max_chars=None)
        text = parsed_full.get("text") or ""
        stored["original_path"] = str(original_path)
        stored["parser"] = parsed_full.get("parser")
        stored["char_count"] = parsed_full.get("char_count")
        stored["documents_count"] = parsed_full.get("documents_count")
        # Only PDFs skip the empty sidecar: a scanned PDF still has page-level
        # tools (read_pdf/vision) that need the original binary path. docx/xlsx
        # keep the historical behavior — always write the sidecar, even empty.
        if text.strip() or suffix != ".pdf":
            text_path = _write_text_sidecar(original_path, text)
            stored["text_path"] = str(text_path)
            # Prefer .txt for downstream read_file / chat path hints.
            stored["path"] = str(text_path)
        else:
            logger.info(
                "[document.persist] no extractable text (scanned PDF?) file=%s",
                original_path.name,
            )
        if parse_meta:
            full_len = int(parsed_full.get("char_count") or 0)
            stored["text_truncated"] = full_len > max(1, int(max_chars))
        return

    if not parse_meta:
        return

    parsed = await parse_document_file(original_path, max_chars=max_chars)
    stored.update(
        {
            "text_truncated": parsed["truncated"],
            "parser": parsed["parser"],
            "char_count": parsed["char_count"],
            "documents_count": parsed["documents_count"],
        }
    )


def _write_text_sidecar(original_path: Path, text: str) -> Path:
    """Write parsed document content next to the original as ``stem.txt``."""
    candidate = original_path.with_suffix(".txt")
    txt_path = unique_upload_path(candidate)
    txt_path.write_text(text if isinstance(text, str) else str(text or ""), encoding="utf-8")
    logger.info(
        "[document.persist] wrote text sidecar original=%s text=%s chars=%s",
        original_path.name,
        txt_path.name,
        len(text or ""),
    )
    return txt_path


_SUFFIX_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".ipynb": "application/x-ipynb+json",
}


def _mime_from_suffix(suffix: str) -> str:
    return _SUFFIX_TO_MIME.get(suffix.lower(), "application/octet-stream")


