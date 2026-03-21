# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for vision tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

IMAGE_OCR_DESCRIPTION: Dict[str, str] = {
    "cn": "读取图片中的可见文本，适合 OCR、票据文本提取和截图文字识别。",
    "en": "Extract visible text from an image for OCR, screenshot text recognition, and document snippets.",
}

VISUAL_QUESTION_ANSWERING_DESCRIPTION: Dict[str, str] = {
    "cn": "理解图片内容并回答问题，可选先做 OCR 再结合识别到的文字回答。",
    "en": "Understand an image and answer questions, optionally grounding the answer with OCR first.",
}

IMAGE_OCR_PARAMS: Dict[str, Dict[str, str]] = {
    "image_path_or_url": {
        "cn": "本地图片路径或公网 http(s) 图片 URL",
        "en": "Local image path or public http(s) image URL",
    },
    "prompt": {
        "cn": "可选，自定义 OCR 提示词",
        "en": "Optional custom OCR prompt",
    },
}

VISUAL_QUESTION_ANSWERING_PARAMS: Dict[str, Dict[str, str]] = {
    "image_path_or_url": {
        "cn": "本地图片路径或公网 http(s) 图片 URL",
        "en": "Local image path or public http(s) image URL",
    },
    "question": {
        "cn": "要询问图片的问题",
        "en": "Question to ask about the image",
    },
    "include_ocr": {
        "cn": "是否先执行 OCR 并把结果拼接进问答提示词，默认 true",
        "en": "Whether to run OCR first and inject the result into the VQA prompt, default true",
    },
    "ocr_prompt": {
        "cn": "可选，自定义 OCR 提示词，仅在 include_ocr 为 true 时使用",
        "en": "Optional custom OCR prompt used only when include_ocr is true",
    },
}


def get_image_ocr_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return JSON Schema for image_ocr input_params."""
    p = IMAGE_OCR_PARAMS
    return {
        "type": "object",
        "properties": {
            "image_path_or_url": {
                "type": "string",
                "description": p["image_path_or_url"].get(
                    language, p["image_path_or_url"]["cn"]
                ),
            },
            "prompt": {
                "type": "string",
                "description": p["prompt"].get(
                    language, p["prompt"]["cn"]
                ),
            },
        },
        "required": ["image_path_or_url"],
    }


def get_visual_question_answering_input_params(
    language: str = "cn",
) -> Dict[str, Any]:
    """Return JSON Schema for visual_question_answering input_params."""
    p = VISUAL_QUESTION_ANSWERING_PARAMS
    return {
        "type": "object",
        "properties": {
            "image_path_or_url": {
                "type": "string",
                "description": p["image_path_or_url"].get(
                    language, p["image_path_or_url"]["cn"]
                ),
            },
            "question": {
                "type": "string",
                "description": p["question"].get(
                    language, p["question"]["cn"]
                ),
            },
            "include_ocr": {
                "type": "boolean",
                "description": p["include_ocr"].get(
                    language, p["include_ocr"]["cn"]
                ),
            },
            "ocr_prompt": {
                "type": "string",
                "description": p["ocr_prompt"].get(
                    language, p["ocr_prompt"]["cn"]
                ),
            },
        },
        "required": ["image_path_or_url", "question"],
    }


class ImageOCRMetadataProvider(ToolMetadataProvider):
    """Metadata provider for image_ocr."""

    def get_name(self) -> str:
        return "image_ocr"

    def get_description(self, language: str = "cn") -> str:
        return IMAGE_OCR_DESCRIPTION.get(language, IMAGE_OCR_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_image_ocr_input_params(language)


class VisualQuestionAnsweringMetadataProvider(ToolMetadataProvider):
    """Metadata provider for visual_question_answering."""

    def get_name(self) -> str:
        return "visual_question_answering"

    def get_description(self, language: str = "cn") -> str:
        return VISUAL_QUESTION_ANSWERING_DESCRIPTION.get(
            language,
            VISUAL_QUESTION_ANSWERING_DESCRIPTION["cn"],
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_visual_question_answering_input_params(language)
