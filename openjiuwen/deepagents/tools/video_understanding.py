# coding: utf-8
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.schema.config import VisionModelConfig
from openjiuwen.deepagents.tools.base_tool import ToolOutput


def _normalize_video_url(video_path: str) -> str:
    """
    Normalize input video path to:
    - keep http/https URL as-is
    - convert local file path to pure base64 string
    """
    value = (video_path or "").strip()
    if not value:
        raise ValueError("video_path cannot be empty")

    if value.startswith(("http://", "https://")):
        return value

    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"video file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"video_path is not a file: {path}")

    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return encoded


def _extract_response_text(response: Any) -> str:
    """Best-effort extraction from framework model response."""
    if response is None:
        return ""

    if isinstance(response, str):
        return response.strip()

    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    texts.append(str(text))
            else:
                text = getattr(item, "text", None)
                if text:
                    texts.append(str(text))
        return "\n".join(texts).strip()

    if content is not None:
        return str(content).strip()

    return str(response).strip()


class VideoUnderstandingTool(Tool):
    """Use VisionModelConfig-backed model to understand a video and answer a user query."""

    def __init__(
        self,
        language: str = "cn",
        vision_model_config: Optional[VisionModelConfig] = None,
        default_timeout_seconds: int = 120,
        default_max_tokens: int = 2048,
        default_temperature: float = 0.2,
    ):
        super().__init__(
            build_tool_card(
                "video_understanding",
                "VideoUnderstandingTool",
                language,
            )
        )
        self.language = language
        self.vision_model_config = vision_model_config
        self.default_timeout_seconds = default_timeout_seconds
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature

        self.model = None
        if vision_model_config is not None and vision_model_config.api_key:
            self.model = init_model(
                provider="OpenAI",
                model_name=vision_model_config.model,
                api_key=vision_model_config.api_key,
                api_base=vision_model_config.base_url,
                max_retries=vision_model_config.max_retries,
                verify_ssl=False,
            )

    async def invoke(
        self,
        inputs: Dict[str, Any],
        **kwargs,
    ) -> ToolOutput:
        if self.vision_model_config is None:
            return ToolOutput(
                success=False,
                error="vision_model_config is not configured.",
            )

        if self.model is None:
            return ToolOutput(
                success=False,
                error="video understanding model is not configured.",
            )

        query = str(inputs.get("query", "") or "").strip()
        video_path = str(inputs.get("video_path", "") or "").strip()
        model_name = str(
            inputs.get("model", self.vision_model_config.model)
            or self.vision_model_config.model
        ).strip()

        max_tokens = int(inputs.get("max_tokens", self.default_max_tokens))
        temperature = float(inputs.get("temperature", self.default_temperature))
        timeout_seconds = int(
            inputs.get("timeout_seconds", self.default_timeout_seconds)
        )

        if not query:
            return ToolOutput(
                success=False,
                error="query cannot be empty.",
            )

        if not video_path:
            return ToolOutput(
                success=False,
                error="video_path cannot be empty.",
            )

        if not model_name:
            return ToolOutput(
                success=False,
                error="video understanding model name is empty.",
            )

        max_tokens = max(128, min(max_tokens, 8192))
        temperature = max(0.0, min(temperature, 2.0))
        timeout_seconds = max(10, min(timeout_seconds, 600))

        try:
            video_url = _normalize_video_url(video_path)

            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": video_url,
                            },
                        },
                        {
                            "type": "text",
                            "text": query,
                        },
                    ],
                }
            ]

            response = await self.model.invoke(
                messages=messages,
                model=model_name,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout_seconds,
            )
            answer = _extract_response_text(response)

            if not answer:
                return ToolOutput(
                    success=False,
                    error="model returned empty answer.",
                )

            return ToolOutput(
                success=True,
                data={
                    "query": query,
                    "video_path": video_path,
                    "model": model_name,
                    "answer": answer,
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"video understanding failed: {exc}",
            )

    async def stream(
        self,
        inputs: Dict[str, Any],
        **kwargs,
    ) -> AsyncIterator[Any]:
        if False:
            yield None