# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-mode attachment manifest appended to the raw leader query."""

from __future__ import annotations

from jiuwenswarm.server.runtime.agent_adapter.interface import (
    append_team_attachment_manifest,
    collect_uploaded_file_records,
)


def test_collect_uploaded_file_records_reads_documents_and_images():
    files = {
        "uploaded_documents": [{"filename": "spec.txt", "path": "/uploads/spec.txt"}],
        "uploaded_images": [{"filename": "shot.png", "path": "/uploads/shot.png"}],
    }

    records = collect_uploaded_file_records(files)

    assert records == [
        {"filename": "spec.txt", "path": "/uploads/spec.txt"},
        {"filename": "shot.png", "path": "/uploads/shot.png"},
    ]


def test_collect_uploaded_file_records_skips_entries_without_path():
    files = {"uploaded_documents": [{"filename": "spec.txt"}, "not-a-dict", {"path": "  "}]}

    assert collect_uploaded_file_records(files) == []


def test_collect_uploaded_file_records_falls_back_to_path_basename():
    files = {"uploaded_documents": [{"path": "/uploads/report.pdf"}]}

    assert collect_uploaded_file_records(files) == [
        {"filename": "report.pdf", "path": "/uploads/report.pdf"}
    ]


def test_append_team_attachment_manifest_adds_absolute_paths():
    files = {"uploaded_documents": [{"filename": "spec.txt", "path": "/uploads/spec.txt"}]}

    result = append_team_attachment_manifest("总结这份文档", files)

    assert result.startswith("总结这份文档\n\n")
    assert "- spec.txt: /uploads/spec.txt" in result


def test_append_team_attachment_manifest_skips_paths_already_in_query():
    files = {"uploaded_documents": [{"filename": "spec.txt", "path": "/uploads/spec.txt"}]}
    query = "总结这份文档\n【上传文档】\n- spec.txt: /uploads/spec.txt"

    assert append_team_attachment_manifest(query, files) == query


def test_append_team_attachment_manifest_adds_only_missing_paths():
    files = {
        "uploaded_documents": [{"filename": "spec.txt", "path": "/uploads/spec.txt"}],
        "uploaded_images": [{"filename": "shot.png", "path": "/uploads/shot.png"}],
    }
    query = "看看\n【上传文档】\n- spec.txt: /uploads/spec.txt"

    result = append_team_attachment_manifest(query, files)

    assert result.count("/uploads/spec.txt") == 1
    assert "- shot.png: /uploads/shot.png" in result


def test_append_team_attachment_manifest_uses_english_header():
    files = {"uploaded_documents": [{"filename": "spec.txt", "path": "/uploads/spec.txt"}]}

    result = append_team_attachment_manifest("summarize", files, language="en")

    assert "[Files uploaded in this turn]" in result


def test_append_team_attachment_manifest_without_files_returns_query():
    assert append_team_attachment_manifest("hello", None) == "hello"
    assert append_team_attachment_manifest("hello", {}) == "hello"


def test_append_team_attachment_manifest_passes_through_non_str_query():
    marker = object()

    assert append_team_attachment_manifest(marker, {"uploaded_documents": []}) is marker
