# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message=r"Protobuf gencode version.*",
)

from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
    _extract_artifact_paths_from_result,
    _extract_file_paths_from_write_tool,
    _extract_image_paths_from_tool_result,
)


def test_write_tool_prefers_path_from_tool_args() -> None:
    paths = _extract_file_paths_from_write_tool(
        "write_file",
        {"path": "/tmp/demo.py"},
        "Wrote /tmp/other.py",
    )
    assert paths == ["/tmp/demo.py"]


def test_write_tool_fallback_extracts_py_from_result_text() -> None:
    paths = _extract_file_paths_from_write_tool(
        "write_file",
        {},
        "Successfully wrote /workspace/out/demo.py",
    )
    assert paths == ["/workspace/out/demo.py"]


def test_write_tool_fallback_ignores_non_python_paths() -> None:
    paths = _extract_file_paths_from_write_tool(
        "write_file",
        {},
        "Saved /workspace/out/chart.png and /workspace/out/notes.txt",
    )
    assert paths == []


def test_image_path_extraction_keeps_image_whitelist() -> None:
    # generate_image 真实输出格式：每行一个路径，可能含空格（effective_project_dir）
    paths = _extract_image_paths_from_tool_result(
        "Generated 1 image(s) successfully!\n"
        "Local file paths (use for attachments or send_file):\n"
        "- E:\\01 code\\proj\\generated_images\\chart.png\n"
        "- /workspace/out/demo.py\n"
        "Prompt: a chart"
    )
    assert paths == ["E:\\01 code\\proj\\generated_images\\chart.png"]


def test_weak_key_candidate_falls_back_to_body_scan(tmp_path) -> None:
    # P1-1 回归：结构化弱键（result: "ok"）校验失败后，不应屏蔽
    # stdout 正文中的真实产物路径
    base = tmp_path.resolve()
    report = base / "report.xlsx"
    report.write_bytes(b"x")
    paths = _extract_artifact_paths_from_result(
        {"stdout": f"Saved to {report}\nDone.", "result": "ok"},
        workspace_base=base,
    )
    assert paths == [str(report)]


def test_structured_hit_short_circuits_body_scan(tmp_path) -> None:
    # 结构化候选校验通过即返回，不再扫描正文（对齐 clowder-ai 语义）
    base = tmp_path.resolve()
    report = base / "report.xlsx"
    report.write_bytes(b"x")
    junk = base / "junk.txt"
    junk.write_bytes(b"x")
    paths = _extract_artifact_paths_from_result(
        {"output_path": str(report), "stdout": f"see {junk}"},
        workspace_base=base,
    )
    assert paths == [str(report)]
