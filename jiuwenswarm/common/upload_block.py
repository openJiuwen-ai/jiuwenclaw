# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The agent-facing attachment block appended to messages that carry uploads.

The web client appends this block at submit time
(``InputArea.tsx::buildSubmitContent``) and the gateway fills in storage paths
(``document_attachments.py``). It exists purely so the model knows where the
uploaded files live — it is not something the user typed, so anything that
displays or summarizes a message must strip it first.

Two forms are recognized, mirroring ``documentMessage.ts::stripUploadDocumentBlocks``:

- compact (current)::

      【上传文档】
      - report.pdf: /path/report.txt (original file: /path/report.pdf)

- legacy per-file blocks::

      【上传文档: report.pdf】
      路径: /path/report.pdf
"""

from __future__ import annotations

import re

# Header of the compact form; entries follow on subsequent ``- `` lines.
UPLOAD_BLOCK_HEADER = "【上传文档】"
# Shared prefix of both forms — cheap guard before running the regexes.
_UPLOAD_BLOCK_PREFIX = "【上传文档"

_COMPACT_BLOCK_RE = re.compile(r"(?:^|\n+)【上传文档】(?:\n-[^\n]*)*")
_LEGACY_BLOCK_RE = re.compile(r"(?:^|\n+)【上传文档[:：][^\n]*(?:\n(?!【)[^\n]*)*")
_BLANK_RUN_RE = re.compile(r"\n{3,}")

_COMPACT_ENTRY_RE = re.compile(r"^-\s*(?P<name>[^\n:：]+)", re.MULTILINE)
_LEGACY_HEADER_RE = re.compile(r"【上传文档[:：]\s*(?P<name>[^\n】]+)】?")


def strip_upload_document_blocks(content: str) -> str:
    """Return ``content`` without its attachment blocks — i.e. what the user typed."""
    if not content or _UPLOAD_BLOCK_PREFIX not in content:
        return content
    cleaned = _COMPACT_BLOCK_RE.sub("", content)
    cleaned = _LEGACY_BLOCK_RE.sub("", cleaned)
    return _BLANK_RUN_RE.sub("\n\n", cleaned).strip()


def upload_document_names(content: str) -> list[str]:
    """Return the filenames listed in ``content``'s attachment blocks, in order.

    Used as a label of last resort when a message is nothing but attachments, so
    that such a session is still identifiable rather than untitled.
    """
    if not content or _UPLOAD_BLOCK_PREFIX not in content:
        return []
    names: list[str] = []
    for match in _COMPACT_BLOCK_RE.finditer(content):
        for entry in _COMPACT_ENTRY_RE.finditer(match.group(0)):
            name = entry.group("name").strip()
            if name:
                names.append(name)
    for match in _LEGACY_HEADER_RE.finditer(content):
        name = match.group("name").strip()
        if name:
            names.append(name)
    return names
