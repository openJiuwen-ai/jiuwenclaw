# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import asyncio
from typing import List, Dict, Any, Optional, AsyncIterator, Union
from contextlib import asynccontextmanager
import aiohttp

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema import ImageGenerationResponse, AudioGenerationResponse, \
    VideoGenerationResponse
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage,
    AssistantMessage,
    UserMessage,
    UsageMetadata
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents


class InferenceAffinityModelClient(BaseModelClient):
    """Inference Affinity (vLLM) API client with cache release support"""
    __client_name__ = "inference_affinity"

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)

    def _get_client_name(self) -> str:
        """Get client name for error messages"""
        return "InferenceAffinity client"

    def _build_and_sanitize_params(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            stream: bool = False,
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> Dict[str, Any]:
        """Build and sanitize request parameters"""
        params = self._build_request_params(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs
        )
        # Sanitize tool_calls in messages
        params["messages"] = self._sanitize_tool_calls(params["messages"])
        if enable_cache_sharing and session_id:
            params["cache_sharing"] = True
            params["cache_salt"] = session_id
        return params

    @asynccontextmanager
    async def _create_session(self):
        """Create a new aiohttp session for each request"""
        timeout = aiohttp.ClientTimeout(
            total=self.model_client_config.timeout,
            connect=30,
            sock_read=self.model_client_config.timeout
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            yield session

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
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> AssistantMessage:
        """Async invoke InferenceAffinity API

        Args:
            messages: Input messages
            tools: Available tools
            temperature: Sampling temperature
            top_p: Nucleus sampling parameter
            model: Model name override
            max_tokens: Maximum tokens to generate
            stop: Stop sequences
            output_parser: Optional output parser
            session_id: session id
            enable_cache_sharing: enable cache sharing
            **kwargs: Additional parameters

        Returns:
            AssistantMessage: Model response
        """
        params = self._build_and_sanitize_params(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            max_tokens=max_tokens,
            stream=False,
            session_id=session_id,
            enable_cache_sharing=enable_cache_sharing,
            **kwargs
        )

        try:
            await trigger(
                LLMCallEvents.LLM_INPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"))

            response_data = await self._make_async_request(params)
            logger.info(f"InferenceAffinity API response: {response_data}")

            # Parse response and apply output parser
            assistant_message = await self._parse_response(response_data, output_parser)

            await trigger(
                LLMCallEvents.LLM_OUTPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                response=assistant_message.content,
                usage=assistant_message.usage_metadata,
                tool_calls=assistant_message.tool_calls)

            return assistant_message

        except Exception as e:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
                error=e)
            logger.error(f"InferenceAffinity API async invoke error: {e}")
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"InferenceAffinity API async invoke error: {str(e)}"
            ) from e

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
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Async streaming invoke InferenceAffinity API

        Args:
            messages: Input messages
            tools: Available tools
            temperature: Sampling temperature
            top_p: Nucleus sampling parameter
            model: Model name override
            max_tokens: Maximum tokens to generate
            stop: Stop sequences
            output_parser: Optional output parser
            session_id: session id
            enable_cache_sharing: enable cache sharing
            **kwargs: Additional parameters

        Yields:
            AssistantMessageChunk: Streaming response chunk
        """
        params = self._build_and_sanitize_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=True,
            session_id=session_id,
            enable_cache_sharing=enable_cache_sharing,
            **kwargs
        )

        try:
            await trigger(
                LLMCallEvents.LLM_INPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                is_stream=True)

            if output_parser:
                # Use streaming parser
                async for parsed_result in self._astream_with_parser(params, output_parser):
                    await trigger(
                        LLMCallEvents.LLM_OUTPUT,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                        result=parsed_result,
                        is_stream=True)
                    yield parsed_result
            else:
                # Direct return without parser
                async for chunk in self._stream_response(params):
                    if chunk:
                        await trigger(
                            LLMCallEvents.LLM_OUTPUT,
                            model_name=params.get("model"),
                            model_provider=self.model_client_config.client_provider,
                            result=chunk,
                            is_stream=True)
                        yield chunk

        except Exception as e:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                error=e)
            logger.error(f"InferenceAffinity API async stream error: {e}")
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"InferenceAffinity API async stream error: {str(e)}"
            ) from e

    async def generate_image(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            size: Optional[str] = "1664*928",
            negative_prompt: Optional[str] = None,
            n: Optional[int] = 1,
            prompt_extend: bool = True,
            watermark: bool = False,
            seed: int = 0,
            **kwargs
    ) -> ImageGenerationResponse:
        pass

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        pass

    async def generate_video(
            self,
            messages: List[UserMessage],
            *,
            img_url: Optional[str] = None,
            audio_url: Optional[str] = None,
            model: Optional[str] = None,
            size: Optional[str] = None,
            resolution: Optional[str] = None,
            duration: Optional[int] = 5,
            prompt_extend: bool = True,
            watermark: bool = False,
            negative_prompt: Optional[str] = None,
            seed: Optional[int] = None,
            **kwargs
    ) -> VideoGenerationResponse:
        pass

    async def release(
            self,
            session_id: str,
            messages: List,
            messages_released_index: int,
            *,
            model: Optional[str] = None,
            tools: Optional[List] = None,
            tools_released_index: Optional[int] = None
    ) -> bool:
        """Release model cache or resources

        Args:
            session_id: Cache salt value to identify specific cache
            messages: Message list
            messages_released_index: Message release index
            model: Model name (defaults to config model_name)
            tools: Tool list
            tools_released_index: Tool release index

        Returns:
            bool: Whether release was successful

        Raises:
            BaseError: If release request fails
        """
        try:
            # Build release request parameters
            release_params = {
                "model": model or self.model_config.model_name,
                "cache_salt": session_id,
                "cache_sharing": True,
                "messages": messages,
                "messages_released_index": messages_released_index,
            }

            if tools is not None:
                release_params["tools"] = tools

            if tools_released_index is not None:
                release_params["tools_released_index"] = tools_released_index

            # Call vLLM release API
            url = f"{self.model_client_config.api_base.rstrip('/')}/release_kv_cache"
            headers = {"Content-Type": "application/json"}

            async with self._create_session() as session:
                async with session.post(url, headers=headers, json=release_params) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Release successful: {result}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Release failed with status {response.status}: {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Release error: {e}")
            raise build_error(
                error_code=StatusCode.MODEL_CALL_FAILED,
                error_msg=f"Release error: {str(e)}"
            ) from e

    async def _make_async_request(self, params: Dict) -> Dict:
        """Make async HTTP request with retry logic"""
        url = f"{self.model_client_config.api_base.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

        last_error = None
        for attempt in range(self.model_client_config.max_retries):
            try:
                logger.debug(
                    f"[ASYNC] Non-stream request (attempt {attempt + 1}/{self.model_client_config.max_retries})")

                async with self._create_session() as session:
                    async with session.post(url, headers=headers, json=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise Exception(f"API returned error {response.status}: {error_text}")

                        return await response.json()

            except Exception as e:
                last_error = e
                if isinstance(e, asyncio.TimeoutError):
                    logger.warning(f"[ASYNC] Request timeout: {str(e)}")
                else:
                    logger.error(f"[ASYNC] Request failed: {str(e)}")

                if attempt < self.model_client_config.max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.info(f"Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)

        raise Exception(f"Request failed after {self.model_client_config.max_retries} attempts: {str(last_error)}")

    async def _parse_response(
            self,
            response: Any,
            parser: Optional[BaseOutputParser] = None
    ) -> AssistantMessage:
        """Parse InferenceAffinity API response

        Args:
            response: API response object (dict from JSON)
            parser: Optional output parser, only parses content field

        Returns:
            AssistantMessage: Parsed assistant message
        """
        if not response.get("choices"):
            raise ValueError("API did not return a valid response")

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Get content
        content = "" if message.get("content") is None else message.get("content")

        # Get reasoning_content (if exists)
        reasoning_content = message.get("reasoning_content", None)

        # Parse tool_calls
        tool_calls = None
        if "tool_calls" in message and message["tool_calls"]:
            tool_calls = []
            for idx, tc in enumerate(message.get("tool_calls", [])):
                function = tc.get("function", {})
                tool_call = ToolCall(
                    id=tc.get("id", "") or "",
                    type="function",
                    name=function.get("name", "") or "",
                    arguments=function.get("arguments", "") or "",
                    index=idx
                )
                tool_calls.append(tool_call)

        # Build UsageMetadata
        usage_metadata = None
        usage = response.get("usage")
        if usage:
            # Extract basic token information
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or 0

            # Extract cached token information
            cache_tokens = 0
            prompt_tokens_details = usage.get("prompt_tokens_details")
            if prompt_tokens_details:
                cache_tokens = prompt_tokens_details.get("cached_tokens", 0) or 0

            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cache_tokens=cache_tokens,
            )

        # Apply output parser (only parse content field)
        parser_content = None
        logger.info(f"Before parse content with parser, content: {content}")
        logger.info(f"Before parse content with parser, parser: {parser}")
        if parser and content:
            try:
                parser_content = await parser.parse(content)
                logger.info(f"Parser parse success, parsed content: {parser_content}")
            except Exception as e:
                logger.warning(f"Parser parse error: {e}")
                parser_content = None

        return AssistantMessage(
            content=content,
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
            finish_reason="tool_calls" if tool_calls else "stop",
            reasoning_content=reasoning_content,
            parser_content=parser_content
        )

    async def _astream_with_parser(
            self,
            params: Dict,
            output_parser: BaseOutputParser
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Process streaming response with output parser

        Strategy:
        1. Immediately yield each raw chunk, maintaining streaming characteristics (content is incremental)
        2. Accumulate all content
        3. **Attempt to parse accumulated content every time a new chunk is received**
        4. When parsing succeeds, output parser_content and clear buffer (implementing incremental output)
        5. When parsing fails, parser_content is None, continue accumulating
        """
        accumulated_content = ""

        async for chunk_item in self._stream_response(params):
            if chunk_item:
                # Accumulate content
                if chunk_item.content:
                    accumulated_content += chunk_item.content

                # Attempt to parse accumulated content every time
                parser_content = None
                if accumulated_content and output_parser:
                    try:
                        current_parsed_result = await output_parser.parse(accumulated_content)
                        # When parsing succeeds, output result and clear buffer
                        if current_parsed_result is not None:
                            parser_content = current_parsed_result
                            accumulated_content = ""  # Clear buffer to implement incremental output
                    except Exception as e:
                        logger.debug(f"Stream parser attempt: {e}")
                        parser_content = None

                # Create new chunk with original content and parser_content
                chunk_with_parser = AssistantMessageChunk(
                    content=chunk_item.content,  # Keep original content increment unchanged
                    reasoning_content=chunk_item.reasoning_content,
                    tool_calls=chunk_item.tool_calls,
                    usage_metadata=chunk_item.usage_metadata,
                    finish_reason=chunk_item.finish_reason,
                    parser_content=parser_content  # Has value when parsing succeeds, otherwise None
                )

                yield chunk_with_parser

    async def _stream_response(self, params: Dict) -> AsyncIterator[AssistantMessageChunk]:
        """Stream response from API"""
        url = f"{self.model_client_config.api_base.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

        async with self._create_session() as session:
            async with session.post(url, headers=headers, json=params) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API returned error {response.status}: {error_text}")

                async for line in response.content:
                    line_str = line.decode('utf-8').strip()
                    if not line_str:
                        continue

                    chunk = self._parse_stream_chunk(line_str)
                    if chunk:
                        yield chunk

    def _parse_stream_chunk(self, line: str) -> Optional[AssistantMessageChunk]:
        """Parse streaming response line

        Args:
            line: SSE format single line data (e.g., "data: {...}")

        Returns:
            AssistantMessageChunk or None
        """
        if not line.startswith("data: "):
            return None

        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            return None

        try:
            chunk_data = json.loads(data_str)

            if "choices" in chunk_data and chunk_data["choices"]:
                choice = (chunk_data.get("choices") or [{}])[0]
                delta = choice.get("delta", {})

                # Extract content
                content = delta.get("content", None) or ""
                reasoning_content = delta.get("reasoning_content", None)

                # Parse tool_calls delta
                tool_calls = None
                if "tool_calls" in delta and delta["tool_calls"]:
                    tool_calls = []
                    for tc in delta["tool_calls"]:
                        function_name = (tc.get("function") or {}).get("name", "")
                        function_arguments = (tc.get("function") or {}).get("arguments", "")
                        tool_call = ToolCall(
                            id=tc.get("id", "") or "",
                            type="function",
                            name=function_name,
                            arguments=function_arguments,
                            index=tc.get("index", 0)
                        )
                        tool_calls.append(tool_call)

                # Build usage_metadata (usually only in the last chunk)
                usage_metadata = None
                if "usage" in chunk_data and chunk_data["usage"]:
                    usage = chunk_data["usage"]
                    usage_metadata = UsageMetadata(
                        model_name=self.model_config.model_name,
                        input_tokens=usage.get("prompt_tokens", 0) or 0,
                        output_tokens=usage.get("completion_tokens", 0) or 0,
                        total_tokens=usage.get("total_tokens", 0) or 0,
                    )

                is_contain_content = content or reasoning_content or tool_calls
                if is_contain_content or usage_metadata:
                    return AssistantMessageChunk(
                        content=content,
                        reasoning_content=reasoning_content,
                        tool_calls=tool_calls,
                        usage_metadata=usage_metadata,
                        finish_reason=choice.get("finish_reason") or "null"
                    )

        except json.JSONDecodeError:
            return None

        return None

    def _sanitize_tool_calls(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sanitize tool_calls in messages, keep OpenAI standard fields:
        id, type, function.name, function.arguments
        Force type to "function"

        Args:
            messages: List of message dictionaries

        Returns:
            Sanitized message list
        """
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue

            cleaned = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                # Extract only valid fields
                func = tc.get("function", {})
                cleaned.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "index": tc.get("index"),
                    "function": {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "")
                    }
                })
            msg["tool_calls"] = cleaned
        return messages
