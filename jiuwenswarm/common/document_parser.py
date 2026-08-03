# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared document parsing helpers for Web upload and Agent tools.

Wraps openjiuwen ``AutoFileParser`` and adds ``.ipynb`` support (not registered
in AutoFileParser by default).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 50_000
_IPYNB_REGISTERED = False

# Formats accepted by the Web document upload/parse API.
SUPPORTED_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".xlsx",
        ".csv",
        ".tsv",
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".json",
        ".ipynb",
    }
)

SUPPORTED_DOCUMENT_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "application/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/json": ".json",
    "application/x-ipynb+json": ".ipynb",
    "application/jupyter": ".ipynb",
    "application/vnd.jupyter": ".ipynb",
}


def resolve_document_suffix(*, filename: str | None, mime_type: str | None) -> str | None:
    """Resolve a supported document suffix from filename and/or MIME type."""
    mime = (mime_type or "").lower().strip()
    if mime in SUPPORTED_DOCUMENT_MIME_TYPES:
        return SUPPORTED_DOCUMENT_MIME_TYPES[mime]

    name = Path(str(filename or "")).name
    suffix = Path(name).suffix.lower()
    if suffix in SUPPORTED_DOCUMENT_EXTENSIONS:
        return suffix
    return None


def is_supported_document(*, filename: str | None = None, mime_type: str | None = None) -> bool:
    return resolve_document_suffix(filename=filename, mime_type=mime_type) is not None


def _ensure_ipynb_parser_registered() -> None:
    """Register a lightweight IpynbParser into AutoFileParser once."""
    global _IPYNB_REGISTERED
    if _IPYNB_REGISTERED:
        return

    from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import (
        AutoFileParser,
        _PARSER_REGISTRY,
    )
    from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser

    if ".ipynb" in _PARSER_REGISTRY:
        _IPYNB_REGISTERED = True
        return

    class IpynbParser(Parser):
        """Parse Jupyter notebook cells into readable Markdown-ish text."""

        async def _parse(self, file_path: str, *args, **kwargs) -> str | None:
            path = Path(file_path)
            raw = path.read_text(encoding="utf-8", errors="replace")
            notebook = json.loads(raw)
            cells = notebook.get("cells") or []
            blocks: list[str] = []
            for idx, cell in enumerate(cells, 1):
                if not isinstance(cell, dict):
                    continue
                cell_type = str(cell.get("cell_type") or "unknown")
                source = cell.get("source") or []
                if isinstance(source, list):
                    source_text = "".join(str(part) for part in source)
                else:
                    source_text = str(source)
                blocks.append(f"## Cell {idx} [{cell_type}]")
                if source_text.strip():
                    blocks.append(source_text.rstrip("\n"))
                outputs = cell.get("outputs") or []
                if not isinstance(outputs, list) or not outputs:
                    continue
                blocks.append("### Outputs")
                for out in outputs:
                    if not isinstance(out, dict):
                        continue
                    text = ""
                    if "text" in out:
                        text_val = out.get("text")
                        text = "".join(text_val) if isinstance(text_val, list) else str(text_val or "")
                    elif isinstance(out.get("data"), dict):
                        plain = out["data"].get("text/plain")
                        if isinstance(plain, list):
                            text = "".join(str(part) for part in plain)
                        elif isinstance(plain, str):
                            text = plain
                    elif "ename" in out and "evalue" in out:
                        text = f"{out.get('ename')}: {out.get('evalue')}"
                    if text:
                        blocks.append(text.rstrip("\n"))
            return "\n".join(blocks).strip() or None

    AutoFileParser.register_new_parser(".ipynb", IpynbParser)
    _IPYNB_REGISTERED = True


async def parse_document_file(
    file_path: str | Path,
    *,
    max_chars: int | None = DEFAULT_MAX_CHARS,
    doc_id: str = "",
) -> dict[str, Any]:
    """Parse a local document and return text plus metadata.

    Args:
        max_chars: Truncate returned text to this many characters. Pass ``None``
            to keep the full parsed text (used when writing ``.txt`` sidecars).

    Returns:
        dict with keys: text, truncated, parser, file_ext, char_count, documents_count
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(
            f"Unsupported format: {suffix}. Supported: {sorted(SUPPORTED_DOCUMENT_EXTENSIONS)}"
        )

    _ensure_ipynb_parser_registered()

    from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser

    parser = AutoFileParser()
    output_dir = str(path.parent / ".parsed_images")
    docs = await parser.parse(
        str(path),
        doc_id=doc_id or path.stem,
        file_name=path.name,
        output_dir=output_dir,
    )
    if not docs:
        return {
            "text": "",
            "truncated": False,
            "parser": "AutoFileParser",
            "file_ext": suffix,
            "char_count": 0,
            "documents_count": 0,
        }

    # Prefer the concrete parser name from registry when available.
    from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import (
        _PARSER_REGISTRY,
    )

    concrete = _PARSER_REGISTRY.get(suffix)
    parser_name = concrete().__class__.__name__ if concrete else "AutoFileParser"

    parts: list[str] = []
    for doc in docs:
        text = getattr(doc, "text", None)
        if text:
            parts.append(str(text))
    joined = "\n\n".join(parts).strip()
    full_len = len(joined)
    truncated = False
    if max_chars is not None:
        limit = max(1, int(max_chars))
        if full_len > limit:
            joined = joined[:limit] + f"\n\n[内容过长已截断，完整约 {full_len} 字符]"
            truncated = True

    return {
        "text": joined,
        "truncated": truncated,
        "parser": parser_name,
        "file_ext": suffix,
        "char_count": full_len,
        "documents_count": len(docs),
    }


def supported_formats() -> list[str]:
    """Return sorted list of supported extensions (including .ipynb)."""
    return sorted(SUPPORTED_DOCUMENT_EXTENSIONS)
