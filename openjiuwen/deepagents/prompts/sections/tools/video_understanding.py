# coding: utf-8
"""Bilingual description and input params for VideoUnderstandingTool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "理解视频内容并回答用户问题，支持远程视频 URL 或本地视频文件路径。",
    "en": (
        "Understand video content and answer user queries. "
        "Supports remote video URLs or local video file paths."
    ),
}

VIDEO_UNDERSTANDING_PARAMS: Dict[str, Dict[str, str]] = {
    "query": {
        "cn": "用户关于视频内容的问题",
        "en": "User query about the video content",
    },
    "video_path": {
        "cn": "本地视频路径或远程视频 URL",
        "en": "Local video path or remote video URL",
    },
    "model": {
        "cn": "可选，指定模型名称",
        "en": "Optional model name",
    },
    "max_tokens": {
        "cn": "可选，最大输出 token 数",
        "en": "Optional maximum output tokens",
    },
    "temperature": {
        "cn": "可选，采样温度",
        "en": "Optional sampling temperature",
    },
    "timeout_seconds": {
        "cn": "可选，请求超时时间（秒）",
        "en": "Optional timeout in seconds",
    },
}


def get_video_understanding_input_params(
    language: str = "cn",
) -> Dict[str, Any]:
    """Return JSON Schema for video_understanding input_params."""
    p = VIDEO_UNDERSTANDING_PARAMS
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": p["query"].get(language, p["query"]["cn"]),
            },
            "video_path": {
                "type": "string",
                "description": p["video_path"].get(
                    language, p["video_path"]["cn"]
                ),
            },
            "model": {
                "type": "string",
                "description": p["model"].get(language, p["model"]["cn"]),
            },
            "max_tokens": {
                "type": "integer",
                "description": p["max_tokens"].get(
                    language, p["max_tokens"]["cn"]
                ),
            },
            "temperature": {
                "type": "number",
                "description": p["temperature"].get(
                    language, p["temperature"]["cn"]
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": p["timeout_seconds"].get(
                    language, p["timeout_seconds"]["cn"]
                ),
            },
        },
        "required": ["query", "video_path"],
    }


class VideoUnderstandingMetadataProvider(ToolMetadataProvider):
    """VideoUnderstandingTool  provider。"""

    def get_name(self) -> str:
        return "video_understanding"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(
        self, language: str = "cn",
    ) -> Dict[str, Any]:
        return get_video_understanding_input_params(language)