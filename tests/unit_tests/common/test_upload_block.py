# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the 【上传文档】 attachment-block helpers."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.upload_block import (
    UPLOAD_BLOCK_HEADER,
    strip_upload_document_blocks,
    upload_document_names,
)

_COMPACT = (
    "What is the title of this paper\n"
    "【上传文档】\n"
    "- DiT.pdf: /s/u/DiT.txt (original file: /s/u/DiT.pdf)"
)
_LEGACY = "hello\n【上传文档: old.pdf】\n路径: /s/old.pdf"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("plain question", "plain question"),
        ("", ""),
        (_COMPACT, "What is the title of this paper"),
        ("q\n【上传文档】\n- a.pdf", "q"),
        ("q\n【上传文档】\n- a.pdf: /s/a.txt\n- b.pdf: /s/b.txt", "q"),
        (_LEGACY, "hello"),
        # Attachment-only message strips down to nothing.
        ("【上传文档】\n- a.pdf: /s/a.txt", ""),
        # The marker only delimits a block when it stands alone on its line.
        ("what does 【上传文档】 mean?", "what does 【上传文档】 mean?"),
    ],
)
def test_strip_upload_document_blocks(content: str, expected: str):
    assert strip_upload_document_blocks(content) == expected


def test_strip_preserves_user_text_after_the_block():
    content = "first\n【上传文档】\n- a.pdf: /s/a.txt\nsecond"
    # Block entries end at the first line that is not a "- " item.
    assert strip_upload_document_blocks(content) == "first\nsecond"


def test_strip_is_idempotent():
    once = strip_upload_document_blocks(_COMPACT)
    assert strip_upload_document_blocks(once) == once


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("no attachments here", []),
        (_COMPACT, ["DiT.pdf"]),
        ("q\n【上传文档】\n- a.pdf: /s/a.txt\n- b.pdf: /s/b.txt", ["a.pdf", "b.pdf"]),
        ("q\n【上传文档】\n- bare.pdf", ["bare.pdf"]),
        (_LEGACY, ["old.pdf"]),
    ],
)
def test_upload_document_names(content: str, expected: list[str]):
    assert upload_document_names(content) == expected


def test_header_constant_matches_the_block_the_client_emits():
    assert UPLOAD_BLOCK_HEADER in _COMPACT
