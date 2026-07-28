"""Native OpenJiuwen model client backed by a fresh Codex CLI turn."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, List, Optional, Union

from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.generation_response import (
    AudioGenerationResponse,
    ImageGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    BaseMessage,
    UsageMetadata,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolInfo

from .codex_process import CodexProcessRunner
from .constants import CODEX_MODEL_ALIAS, CODEX_PROVIDER_NAME
from .consumer_policy import (
    CODEX_CALL_PERMIT_KWARG,
    consume_codex_call_permit,
    filter_codex_tools,
)
from .contracts import normalize_messages, normalize_tools
from .errors import CodexProviderError


class CodexSubscriptionModelClient(BaseModelClient):
    """Expose Codex subscription execution through OpenJiuwen's model contract."""

    __client_name__ = CODEX_PROVIDER_NAME
    __client_type__ = "llm"

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        self._runner = CodexProcessRunner()
        super().__init__(model_config=model_config, model_client_config=model_client_config)

    def _validate_config(self) -> None:
        provider = self.model_client_config.client_provider
        provider_name = provider.value if hasattr(provider, "value") else str(provider)
        if provider_name != CODEX_PROVIDER_NAME:
            raise CodexProviderError("invalid_config", "The Codex subscription provider is misconfigured.")
        if self.model_client_config.api_key or self.model_client_config.api_base:
            raise CodexProviderError(
                "invalid_config",
                "Codex subscription configuration must not contain an API key or API base URL.",
            )
        configured_model = str(getattr(self.model_config, "model_name", "") or "")
        if configured_model not in {"", CODEX_MODEL_ALIAS}:
            raise CodexProviderError("invalid_config", "The Codex subscription model alias is invalid.")

    async def invoke(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        model: str = None,
        max_tokens: Optional[int] = None,
        stop: Union[Optional[str], None] = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        **kwargs: Any,
    ) -> AssistantMessage:
        call_permit = kwargs.pop(CODEX_CALL_PERMIT_KWARG, None)
        del temperature, top_p, max_tokens, stop, kwargs
        consume_codex_call_permit(self, call_permit)
        if model not in (None, "", CODEX_MODEL_ALIAS):
            raise CodexProviderError("invalid_request", "The requested Codex model alias is unsupported.")
        converted_messages = normalize_messages(self._convert_messages_to_dict(messages))
        converted_tools = filter_codex_tools(
            normalize_tools(self._convert_tools_to_dict(tools))
        )
        result = await self._runner.run(
            messages=converted_messages,
            tools=converted_tools,
            timeout=self.model_client_config.timeout if timeout is None else timeout,
        )
        if result.reasoning_content:
            raise CodexProviderError(
                "invalid_output",
                "Codex returned reasoning content outside the provider contract.",
            )
        usage = None
        if result.usage is not None:
            usage = UsageMetadata(
                model_name=CODEX_MODEL_ALIAS,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                total_tokens=result.usage.total_tokens,
                cache_tokens=result.usage.cached_input_tokens,
            )
        tool_calls = [
            ToolCall(
                id=call.id,
                type="function",
                name=call.name,
                arguments=json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
                index=index,
            )
            for index, call in enumerate(result.tool_calls)
        ]
        response = AssistantMessage(
            content=result.content,
            reasoning_content=None,
            tool_calls=tool_calls or None,
            usage_metadata=usage,
            finish_reason=result.finish_reason,
            metadata={
                "model_provider": CODEX_PROVIDER_NAME,
                "billing_mode": "chatgpt_subscription",
                "provider_cost_known": False,
            },
        )
        if output_parser is not None and result.content:
            try:
                response.parser_content = await output_parser.parse(result.content)
            except Exception:
                response.parser_content = None
        return response

    async def stream(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        model: str = None,
        max_tokens: Optional[int] = None,
        stop: Union[Optional[str], None] = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        **kwargs: Any,
    ) -> AsyncIterator[AssistantMessageChunk]:
        response = await self.invoke(
            messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            max_tokens=max_tokens,
            stop=stop,
            output_parser=output_parser,
            timeout=timeout,
            **kwargs,
        )
        yield AssistantMessageChunk(
            content=response.content,
            parser_content=response.parser_content,
            reasoning_content=None,
            tool_calls=response.tool_calls,
            usage_metadata=response.usage_metadata,
            finish_reason=response.finish_reason,
            metadata=response.metadata,
        )

    async def generate_image(
        self, messages: List[UserMessage], *, model: Optional[str] = None,
        size: Optional[str] = "1664*928", negative_prompt: Optional[str] = None,
        n: Optional[int] = 1, prompt_extend: bool = True, watermark: bool = False,
        seed: int = 0, **kwargs: Any,
    ) -> ImageGenerationResponse:
        del messages, model, size, negative_prompt, n, prompt_extend, watermark, seed, kwargs
        raise CodexProviderError("unsupported_modality", "Codex subscription does not provide image generation.")

    async def generate_speech(
        self, messages: List[UserMessage], *, model: Optional[str] = None,
        voice: Optional[str] = "Cherry", language_type: Optional[str] = "Auto", **kwargs: Any,
    ) -> AudioGenerationResponse:
        del messages, model, voice, language_type, kwargs
        raise CodexProviderError("unsupported_modality", "Codex subscription does not provide speech generation.")

    async def generate_video(
        self, messages: List[UserMessage], *, img_url: Optional[str] = None,
        audio_url: Optional[str] = None, model: Optional[str] = None,
        size: Optional[str] = None, resolution: Optional[str] = None,
        duration: Optional[int] = 5, prompt_extend: bool = True,
        watermark: bool = False, negative_prompt: Optional[str] = None,
        seed: Optional[int] = None, **kwargs: Any,
    ) -> VideoGenerationResponse:
        del messages, img_url, audio_url, model, size, resolution, duration, prompt_extend
        del watermark, negative_prompt, seed, kwargs
        raise CodexProviderError("unsupported_modality", "Codex subscription does not provide video generation.")
