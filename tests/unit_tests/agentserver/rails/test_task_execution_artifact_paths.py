# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message=r"Protobuf gencode version.*",
)

from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
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
    paths = _extract_image_paths_from_tool_result(
        "Generated /workspace/out/chart.png and /workspace/out/demo.py"
    )
    assert paths == ["/workspace/out/chart.png"]
