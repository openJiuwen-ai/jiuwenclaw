# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Vision tools with an input guard on the file they are handed."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.schema.config import VisionModelConfig, is_vision_model_config_complete
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.multimodal.vision import (
    ImageOCRTool,
    VisualQuestionAnsweringTool,
)

# Suffixes worth forwarding. Which of these a given endpoint actually accepts is
# the endpoint's business — this guard only catches files that are not images at
# all, and stays out of the way otherwise.
_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
)

_PDF_REDIRECT = (
    "{name} is a PDF, not an image, and vision tools cannot read one. Call "
    "render_pdf_page with this pdf_path and the page you need, then pass the PNG "
    "path it returns to this tool. read_pdf reports which pages carry images."
)

_NOT_AN_IMAGE = (
    "{name} is not an image file ({suffix} is not one of "
    "{supported}). Vision tools need an image path or an https image URL."
)


def _guard_image_input(image_path_or_url: Any) -> str | None:
    """Return an error message when this input cannot be read as an image.

    Only local paths are inspected. URLs are left alone: the suffix of a URL
    says little about what it serves, and the existing remote-fetch path already
    reports its own failures.
    """
    value = str(image_path_or_url or "").strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return None

    suffix = Path(value).suffix.lower()
    if not suffix or suffix in _IMAGE_SUFFIXES:
        return None

    name = Path(value).name
    if suffix == ".pdf":
        return _PDF_REDIRECT.format(name=name)
    return _NOT_AN_IMAGE.format(
        name=name, suffix=suffix, supported=", ".join(sorted(_IMAGE_SUFFIXES))
    )


class GuardedImageOCRTool(ImageOCRTool):
    """``image_ocr`` that names the right tool when handed a non-image."""

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        error = _guard_image_input((inputs or {}).get("image_path_or_url"))
        if error:
            return ToolOutput(success=False, error=error)
        return await super().invoke(inputs, **kwargs)


class GuardedVisualQuestionAnsweringTool(VisualQuestionAnsweringTool):
    """``visual_question_answering`` that names the right tool for a non-image."""

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        error = _guard_image_input((inputs or {}).get("image_path_or_url"))
        if error:
            return ToolOutput(success=False, error=error)
        return await super().invoke(inputs, **kwargs)


def create_vision_tools(
    language: str = "cn",
    vision_model_config: Optional[VisionModelConfig] = None,
    agent_id: Optional[str] = None,
) -> list[Tool]:
    """Drop-in replacement for ``openjiuwen…vision.create_vision_tools``.

    Same tool names and cards, same empty-list-when-unconfigured contract.
    """
    if not is_vision_model_config_complete(vision_model_config):
        return []
    return [
        GuardedImageOCRTool(
            language=language,
            vision_model_config=vision_model_config,
            agent_id=agent_id,
        ),
        GuardedVisualQuestionAnsweringTool(
            language=language,
            vision_model_config=vision_model_config,
            agent_id=agent_id,
        ),
    ]
