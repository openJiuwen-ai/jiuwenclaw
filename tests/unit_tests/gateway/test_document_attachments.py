# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from jiuwenswarm.common.document_parser import (
    is_supported_document,
    parse_document_file,
    resolve_document_suffix,
    supported_formats,
)
from jiuwenswarm.gateway.document_attachments import (
    coerce_document_parse_flag,
    parse_existing_document,
    persist_and_parse_documents,
    resolve_session_upload_path,
)


@pytest.mark.asyncio
async def test_persist_and_parse_documents_reports_count_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments._MAX_DOCUMENT_COUNT",
        2,
    )
    documents = []
    for idx in range(4):
        content = f"# doc {idx}"
        documents.append(
            {
                "filename": f"doc-{idx}.md",
                "mime_type": "text/markdown",
                "base64_data": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }
        )
    result = await persist_and_parse_documents(
        {"documents": documents},
        "sess_limit",
        parse=True,
        max_chars=1000,
    )
    items = result.get("media_items") or []
    assert len(items) == 2
    errors = result.get("document_errors") or []
    assert len(errors) == 2
    assert errors[0]["index"] == 2
    assert errors[0]["code"] == "DOCUMENT_COUNT_LIMIT"
    assert "limit" in errors[0]["error"].lower()
    assert errors[1]["filename"] == "doc-3.md"


def test_coerce_document_parse_flag():
    assert coerce_document_parse_flag(True) is True
    assert coerce_document_parse_flag(False) is False
    assert coerce_document_parse_flag("false") is False
    assert coerce_document_parse_flag("FALSE") is False
    assert coerce_document_parse_flag("0") is False
    assert coerce_document_parse_flag("true") is True
    assert coerce_document_parse_flag("yes") is True
    # 非空但无法识别的字符串 / 其它类型：回退默认 True
    assert coerce_document_parse_flag("maybe") is True
    assert coerce_document_parse_flag(1) is True
    assert coerce_document_parse_flag(0, default=True) is True
    assert coerce_document_parse_flag(None, default=True) is True
    assert coerce_document_parse_flag("", default=False) is False


def test_supported_formats_include_ipynb_and_office():
    formats = set(supported_formats())
    assert ".ipynb" in formats
    assert ".pdf" in formats
    assert ".docx" in formats
    assert ".xlsx" in formats
    assert ".md" in formats


def test_resolve_document_suffix_from_mime_and_filename():
    assert resolve_document_suffix(filename="a.PDF", mime_type="") == ".pdf"
    assert (
        resolve_document_suffix(
            filename="x.bin",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        == ".docx"
    )
    assert is_supported_document(filename="note.ipynb")
    assert not is_supported_document(filename="a.exe")


@pytest.mark.asyncio
async def test_parse_ipynb_and_markdown(tmp_path: Path):
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n"]},
            {
                "cell_type": "code",
                "source": ["print(1)\n"],
                "outputs": [{"text": ["1\n"]}],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    ipynb_path = tmp_path / "demo.ipynb"
    ipynb_path.write_text(json.dumps(nb), encoding="utf-8")
    parsed_nb = await parse_document_file(ipynb_path, max_chars=5000)
    assert "Cell 1" in parsed_nb["text"]
    assert "print(1)" in parsed_nb["text"]
    assert parsed_nb["parser"] == "IpynbParser"

    md_path = tmp_path / "note.md"
    md_path.write_text("# Hello\n\nworld", encoding="utf-8")
    parsed_md = await parse_document_file(md_path, max_chars=5000)
    assert "Hello" in parsed_md["text"]
    assert parsed_md["file_ext"] == ".md"


@pytest.mark.asyncio
async def test_persist_and_parse_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )
    content = "# uploaded doc\n\ncontent"
    payload = {
        "documents": [
            {
                "filename": "readme.md",
                "mime_type": "text/markdown",
                "base64_data": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }
        ]
    }
    result = await persist_and_parse_documents(payload, "sess_test", parse=True, max_chars=1000)
    items = result.get("media_items") or []
    assert len(items) == 1
    assert items[0]["type"] == "document"
    assert Path(items[0]["path"]).is_file()
    assert items[0]["text_truncated"] is False
    assert "text" not in items[0]
    assert result["files"]["uploaded_documents"][0]["path"] == items[0]["path"]


@pytest.mark.asyncio
async def test_persist_docx_writes_txt_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )

    async def fake_parse(file_path, *, max_chars=None, doc_id=""):
        return {
            "text": "parsed office body",
            "truncated": False,
            "parser": "FakeDocxParser",
            "file_ext": Path(file_path).suffix.lower(),
            "char_count": len("parsed office body"),
            "documents_count": 1,
        }

    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.parse_document_file",
        fake_parse,
    )

    # Minimal non-empty payload; content is not really a docx — parser is mocked.
    payload = {
        "documents": [
            {
                "filename": "report.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "base64_data": base64.b64encode(b"fake-docx-bytes").decode("ascii"),
            }
        ]
    }
    result = await persist_and_parse_documents(payload, "sess_office", parse=True, max_chars=1000)
    items = result.get("media_items") or []
    assert len(items) == 1
    item = items[0]
    assert item["filename"] == "report.docx"
    assert Path(item["original_path"]).suffix == ".docx"
    assert Path(item["original_path"]).is_file()
    assert Path(item["text_path"]).suffix == ".txt"
    assert Path(item["text_path"]).is_file()
    assert item["path"] == item["text_path"]
    assert Path(item["path"]).read_text(encoding="utf-8") == "parsed office body"
    assert result["files"]["uploaded_documents"][0]["path"] == item["text_path"]
    assert result["files"]["uploaded_documents"][0]["original_path"] == item["original_path"]


@pytest.mark.asyncio
async def test_persist_xlsx_writes_txt_sidecar_even_when_parse_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )

    async def fake_parse(file_path, *, max_chars=None, doc_id=""):
        return {
            "text": "sheet A1 value",
            "truncated": False,
            "parser": "FakeXlsxParser",
            "file_ext": ".xlsx",
            "char_count": 14,
            "documents_count": 1,
        }

    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.parse_document_file",
        fake_parse,
    )

    payload = {
        "documents": [
            {
                "filename": "data.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "base64_data": base64.b64encode(b"fake-xlsx-bytes").decode("ascii"),
            }
        ]
    }
    result = await persist_and_parse_documents(payload, "sess_xlsx", parse=False, max_chars=1000)
    item = (result.get("media_items") or [])[0]
    assert Path(item["original_path"]).suffix == ".xlsx"
    assert Path(item["path"]).suffix == ".txt"
    assert Path(item["path"]).read_text(encoding="utf-8") == "sheet A1 value"


@pytest.mark.asyncio
async def test_persist_pdf_writes_txt_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )

    async def fake_parse(file_path, *, max_chars=None, doc_id=""):
        return {
            "text": "parsed pdf body",
            "truncated": False,
            "parser": "PDFParser",
            "file_ext": ".pdf",
            "char_count": len("parsed pdf body"),
            "documents_count": 1,
        }

    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.parse_document_file",
        fake_parse,
    )

    payload = {
        "documents": [
            {
                "filename": "paper.pdf",
                "mime_type": "application/pdf",
                "base64_data": base64.b64encode(b"%PDF-fake-bytes").decode("ascii"),
            }
        ]
    }
    result = await persist_and_parse_documents(payload, "sess_pdf", parse=True, max_chars=1000)
    items = result.get("media_items") or []
    assert len(items) == 1
    item = items[0]
    assert item["filename"] == "paper.pdf"
    assert Path(item["original_path"]).suffix == ".pdf"
    assert Path(item["original_path"]).is_file()
    assert Path(item["text_path"]).suffix == ".txt"
    assert item["path"] == item["text_path"]
    assert Path(item["path"]).read_text(encoding="utf-8") == "parsed pdf body"
    assert result["files"]["uploaded_documents"][0]["path"] == item["text_path"]
    assert result["files"]["uploaded_documents"][0]["original_path"] == item["original_path"]


@pytest.mark.asyncio
async def test_persist_pdf_without_text_layer_keeps_original_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Scanned PDFs (no text layer) must keep path on the original binary."""
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )

    async def fake_parse(file_path, *, max_chars=None, doc_id=""):
        return {
            "text": "",
            "truncated": False,
            "parser": "PDFParser",
            "file_ext": ".pdf",
            "char_count": 0,
            "documents_count": 1,
        }

    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.parse_document_file",
        fake_parse,
    )

    payload = {
        "documents": [
            {
                "filename": "scan.pdf",
                "mime_type": "application/pdf",
                "base64_data": base64.b64encode(b"%PDF-fake-scan").decode("ascii"),
            }
        ]
    }
    result = await persist_and_parse_documents(payload, "sess_scan", parse=True, max_chars=1000)
    item = (result.get("media_items") or [])[0]
    assert Path(item["path"]).suffix == ".pdf"
    assert item.get("text_path") is None
    assert item["char_count"] == 0
    # No stray empty .txt sidecar on disk.
    upload_dir = Path(item["path"]).parent
    assert not list(upload_dir.glob("*.txt"))


@pytest.mark.asyncio
async def test_persist_empty_docx_still_writes_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Empty-text guard applies to .pdf only; docx/xlsx keep writing a sidecar even when empty."""
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )

    async def fake_parse(file_path, *, max_chars=None, doc_id=""):
        return {
            "text": "",
            "truncated": False,
            "parser": "FakeDocxParser",
            "file_ext": ".docx",
            "char_count": 0,
            "documents_count": 1,
        }

    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.parse_document_file",
        fake_parse,
    )

    payload = {
        "documents": [
            {
                "filename": "empty.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "base64_data": base64.b64encode(b"fake-empty-docx").decode("ascii"),
            }
        ]
    }
    result = await persist_and_parse_documents(payload, "sess_empty", parse=True, max_chars=1000)
    item = (result.get("media_items") or [])[0]
    assert Path(item["path"]).suffix == ".txt"
    assert Path(item["path"]).read_text(encoding="utf-8") == ""


def test_resolve_session_upload_path_blocks_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )
    upload_dir = tmp_path / "sess_test" / "uploads"
    upload_dir.mkdir(parents=True)
    safe_file = upload_dir / "note.md"
    safe_file.write_text("hello", encoding="utf-8")

    outside = tmp_path / "secret.json"
    outside.write_text('{"token":"x"}', encoding="utf-8")

    resolved = resolve_session_upload_path(str(safe_file), session_id="sess_test")
    assert resolved == safe_file.resolve()

    with pytest.raises(PermissionError):
        resolve_session_upload_path(str(outside), session_id="sess_test")

    with pytest.raises(PermissionError):
        resolve_session_upload_path(
            str(upload_dir / ".." / ".." / "secret.json"),
            session_id="sess_test",
        )

    with pytest.raises(PermissionError):
        resolve_session_upload_path("../../../secret.json", session_id="sess_test")


@pytest.mark.asyncio
async def test_parse_existing_document_rejects_outside_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.document_attachments.get_agent_sessions_dir",
        lambda: tmp_path,
    )
    outside = tmp_path / "leak.md"
    outside.write_text("# secret", encoding="utf-8")
    with pytest.raises(PermissionError):
        await parse_existing_document(str(outside), session_id="sess_test", max_chars=1000)
