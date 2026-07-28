"""Native OpenJiuwen model client backed by a fresh Claude CLI turn."""

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

from .claude_constants import CLAUDE_BILLING_MODE, CLAUDE_MODEL_ALIAS, CLAUDE_PROVIDER_NAME
from .claude_consumer_policy import filter_claude_tools, require_claude_enabled
from .claude_contracts import normalize_claude_messages, normalize_claude_tools
from .claude_process import ClaudeProcessRunner
from .errors import ClaudeProviderError


class ClaudeSubscriptionModelClient(BaseModelClient):
    """Expose one fresh Claude CLI turn through OpenJiuwen's model contract.

    A normal, visible, default-enabled provider (like the Codex provider): it
    carries no credential in Jiuwen config and never accepts an API key.
    Authentication is the operator's own ``claude`` subscription login, resolved
    natively by the CLI from the environment (outside this product) and
    positively verified before every turn. An administrator kill switch can
    disable it per instance, but it defaults on.
    """

    __client_name__ = CLAUDE_PROVIDER_NAME
    __client_type__ = "llm"

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        self._runner = ClaudeProcessRunner()
        super().__init__(model_config=model_config, model_client_config=model_client_config)

    def _validate_config(self) -> None:
        provider = self.model_client_config.client_provider
        provider_name = provider.value if hasattr(provider, "value") else str(provider)
        if provider_name != CLAUDE_PROVIDER_NAME:
            raise ClaudeProviderError("invalid_config", "The Claude provider is misconfigured.")
        # The Claude provider carries no credential in Jiuwen config: the CLI
        # resolves the operator's own Claude login natively from the environment
        # (done outside this product). Subscription-login-only means no API key
        # is ever accepted; the config must therefore contain neither an API key
        # nor an API base URL.
        if self.model_client_config.api_key or self.model_client_config.api_base:
            raise ClaudeProviderError(
                "invalid_config",
                "The Claude provider configuration must not contain an API key or API base URL; "
                "credentials are resolved by the Claude CLI from the environment.",
            )
        configured_model = str(getattr(self.model_config, "model_name", "") or "")
        if configured_model not in {"", CLAUDE_MODEL_ALIAS}:
            raise ClaudeProviderError("invalid_config", "The Claude provider model alias is invalid.")

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
        del temperature, top_p, max_tokens, stop, kwargs
        # Subscription-only: authorization is the operator's own Claude
        # subscription login, resolved and positively verified by the runner from
        # the environment (unlike Codex's shared subscription, which requires a
        # call-bound permit). The gate here is the administrator kill switch,
        # which defaults to enabled.
        require_claude_enabled()
        if model not in (None, "", CLAUDE_MODEL_ALIAS):
            raise ClaudeProviderError("invalid_request", "The requested Claude model alias is unsupported.")
        converted_messages = normalize_claude_messages(self._convert_messages_to_dict(messages))
        converted_tools = filter_claude_tools(
            normalize_claude_tools(self._convert_tools_to_dict(tools))
        )
        result = await self._runner.run(
            messages=converted_messages,
            tools=converted_tools,
            timeout=self.model_client_config.timeout if timeout is None else timeout,
        )
        if result.reasoning_content:
            raise ClaudeProviderError(
                "invalid_output",
                "Claude returned reasoning content outside the provider contract.",
            )
        usage = None
        if result.usage is not None:
            usage = UsageMetadata(
                model_name=CLAUDE_MODEL_ALIAS,
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
                "model_provider": CLAUDE_PROVIDER_NAME,
                "billing_mode": CLAUDE_BILLING_MODE,
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
        raise ClaudeProviderError("unsupported_modality", "The Claude provider does not provide image generation.")

    async def generate_speech(
        self, messages: List[UserMessage], *, model: Optional[str] = None,
        voice: Optional[str] = "Cherry", language_type: Optional[str] = "Auto", **kwargs: Any,
    ) -> AudioGenerationResponse:
        del messages, model, voice, language_type, kwargs
        raise ClaudeProviderError("unsupported_modality", "The Claude provider does not provide speech generation.")

    async def generate_video(
        self, messages: List[UserMessage], *, img_url: Optional[str] = None,
        audio_url: Optional[str] = None, model: Optional[str] = None,
        size: Optional[str] = None, resolution: Optional[str] = None,
        duration: Optional[int] = 5, prompt_extend: bool = True,
        watermark: bool = False, negative_prompt: Optional[str] = None,
        seed: Optional[int] = None, **kwargs: Any,
    ) -> VideoGenerationResponse:
        del (messages, img_url, audio_url, model, size, resolution, duration,
             prompt_extend, watermark, negative_prompt, seed, kwargs)
        raise ClaudeProviderError("unsupported_modality", "The Claude provider does not provide video generation.")
