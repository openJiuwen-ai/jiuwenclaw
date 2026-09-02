# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the non-image input guard on the vision tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.agents.harness.common.tools.vision_tools import (
    GuardedImageOCRTool,
    GuardedVisualQuestionAnsweringTool,
    _guard_image_input,
    create_vision_tools,
)


def test_pdf_path_is_redirected_to_render_pdf_page():
    message = _guard_image_input("/tmp/report.pdf")
    assert message is not None
    assert "render_pdf_page" in message
    assert "report.pdf" in message


def test_other_non_image_files_are_rejected_clearly():
    message = _guard_image_input("/tmp/notes.docx")
    assert message is not None
    # A .docx has no render_pdf_page route, so it must not claim one.
    assert "render_pdf_page" not in message
    assert ".docx" in message


@pytest.mark.parametrize(
    "value",
    [
        "/tmp/page_1.png",
        "/tmp/scan.JPEG",
        "https://example.com/chart.png",
        # A URL's suffix says little about what it serves; leave it to the fetch.
        "https://example.com/render?id=7",
        "https://example.com/doc.pdf",
        "",
        None,
    ],
)
def test_images_and_urls_pass_through(value):
    assert _guard_image_input(value) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_cls", [GuardedImageOCRTool, GuardedVisualQuestionAnsweringTool]
)
async def test_guard_short_circuits_before_any_model_call(tool_cls, tmp_path: Path):
    """No vision config is set, so reaching the model would raise instead."""
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    tool = tool_cls(vision_model_config=None)
    result = await tool.invoke(
        {"image_path_or_url": str(pdf_path), "question": "what is in figure 1?"}
    )

    assert result.success is False
    assert "render_pdf_page" in result.error


def test_factory_keeps_the_registered_tool_names():
    """interface_deep registers by card name; the guard must not rename anything."""
    from openjiuwen.harness.schema.config import VisionModelConfig

    config = VisionModelConfig(api_key="k", base_url="https://example.com/v1", model="m")
    names = {tool.card.name for tool in create_vision_tools(vision_model_config=config)}
    assert names == {"image_ocr", "visual_question_answering"}


def test_factory_returns_nothing_when_unconfigured():
    assert create_vision_tools(vision_model_config=None) == []
