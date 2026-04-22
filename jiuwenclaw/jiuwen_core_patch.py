# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import os
from typing import Any, Optional

from pydantic import Field
import httpx
from openjiuwen.core.common.logging import llm_logger, LogEventType
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import \
    AssistantMessageChunk, OpenAIModelClient, ToolCall, UsageMetadata


_ORIGINAL_BUILD_REQUEST_PARAMS = None


def _patched_build_request_params(self, *, stream: bool, **kwargs) -> dict:
    """Patched version: ensure streaming requests carry ``stream_options={"include_usage": true}``.

    Required for OpenAI-compatible streaming APIs to return the usage payload in
    the final chunk; without it ``chunk.usage`` is always ``None`` and the
    ``gen_ai.client.token.usage`` metric never gets recorded.

    Respects a user-provided ``stream_options`` override.
    """
    params = _ORIGINAL_BUILD_REQUEST_PARAMS(self, stream=stream, **kwargs)
    if stream:
        existing = params.get("stream_options")
        if existing is None:
            params["stream_options"] = {"include_usage": True}
        elif isinstance(existing, dict) and "include_usage" not in existing:
            existing["include_usage"] = True
    return params


class PatchOpenAIModelClient(OpenAIModelClient):

    def _create_async_openai_client(self, timeout: Optional[float] = None) -> "openai.AsyncOpenAI":
        """
        Create an OpenAI Async client with configured SSL/proxy/http client settings.
        
        Args:
            timeout: Optional timeout override for this specific request
        """
        from openai import AsyncOpenAI
        
        ssl_verify, ssl_cert = self.model_client_config.verify_ssl, self.model_client_config.ssl_cert
        verify = SslUtils.create_strict_ssl_context(ssl_cert) if ssl_verify else ssl_verify

        http_client = httpx.AsyncClient(
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            verify=verify
        )

        # Use method-level timeout if provided, otherwise use config timeout
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        llm_logger.info(
            "Before create openai client, model client config params ready.",
            event_type=LogEventType.LLM_CALL_START,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries
        )
        default_headers = os.getenv("default_headers", None)
        try:
            default_headers = json.loads(default_headers) if default_headers else None
        except json.decoder.JSONDecodeError as error:
            llm_logger.warning(f"Model default headers parse failed: {error}")
            default_headers = None
        return AsyncOpenAI(
            api_key=self.model_client_config.api_key,
            base_url=self.model_client_config.api_base,
            http_client=http_client,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
            default_headers=default_headers
        )
    
    def _parse_stream_chunk(self, chunk: Any) -> Optional[AssistantMessageChunk]:
        """Parse OpenAI streaming response chunk

        Args:
            chunk: OpenAI streaming response chunk

        Returns:
            AssistantMessageChunk or None
        """

        # Detect Huawei MaaS by api_base (workaround for malformed tool_calls delta)
        # Only apply workaround for glm-5.1 model on MaaS endpoint
        _is_huawei_maas = False
        _has_mcc = hasattr(self, "model_client_config")
        _mcc_is_none = self.model_client_config is None if _has_mcc else True
        _has_mc = hasattr(self, "model_config")
        _mc_is_none = self.model_config is None if _has_mc else True
        _api_base = ""
        _model_name = ""
        if _has_mcc and not _mcc_is_none:
            _api_base = getattr(self.model_client_config, "api_base", "") or ""
            _model_name = getattr(self.model_client_config, "model_name", "") or ""
        if _has_mc and not _mc_is_none and not _model_name:
            _model_name = getattr(self.model_config, "model_name", "") or ""
        # Check if it's Huawei MaaS AND model is glm-5.1 (the affected model)
        _is_maas_endpoint = (
            "modelarts-maas.com" in _api_base
            or "modelarts" in _api_base.lower()
            or "huaweiapaas.com" in _api_base
            or "agentarts" in _api_base.lower()
        )
        _is_glm51 = _model_name.lower() in ("glm-5.1", "glm5.1")
        _is_huawei_maas = _is_maas_endpoint and _is_glm51

        # Check for usage-only chunk (empty choices with usage data - final chunk with stream_options)
        _has_usage = hasattr(chunk, 'usage') and chunk.usage
        _has_choices = bool(chunk.choices) if hasattr(chunk, 'choices') else False
        if _has_usage and not _has_choices:
            # This is the final usage chunk - parse it even if choices is empty
            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=getattr(chunk.usage, 'prompt_tokens', 0) or 0,
                output_tokens=getattr(chunk.usage, 'completion_tokens', 0) or 0,
                total_tokens=getattr(chunk.usage, 'total_tokens', 0) or 0,
            )
            # Return a chunk with only usage metadata (no content)
            return AssistantMessageChunk(
                content="",
                reasoning_content=None,
                tool_calls=None,
                usage_metadata=usage_metadata,
                finish_reason="stop",  # Usage chunk always indicates completion
            )


        # When stream_options={"include_usage": true}, OpenAI sends a final
        # usage-only chunk with ``choices=[]`` and ``usage`` populated. Emit a
        # usage-only AssistantMessageChunk so downstream aggregation picks it up.
        if not chunk.choices:
            chunk_usage = getattr(chunk, 'usage', None)
            if chunk_usage:
                input_cost, output_cost, total_cost = self._extract_cost_info(chunk_usage)
                usage_metadata = UsageMetadata(
                    model_name=self.model_config.model_name,
                    input_tokens=getattr(chunk_usage, 'prompt_tokens', 0) or 0,
                    output_tokens=getattr(chunk_usage, 'completion_tokens', 0) or 0,
                    total_tokens=getattr(chunk_usage, 'total_tokens', 0) or 0,
                    input_cost=input_cost,
                    output_cost=output_cost,
                    total_cost=total_cost,
                )
                return AssistantMessageChunk(
                    content="",
                    reasoning_content=None,
                    tool_calls=None,
                    usage_metadata=usage_metadata,
                    finish_reason="null",
                )
            return None

        choice = chunk.choices[0]
        delta = choice.delta

        # Extract content
        content = getattr(delta, 'content', None) or ""
        reasoning_content = getattr(delta, 'reasoning_content', None)

        # Parse tool_calls delta
        tool_calls = []
        if hasattr(delta, 'tool_calls') and delta.tool_calls:
            for tc_delta in delta.tool_calls:
                if hasattr(tc_delta, 'function') and tc_delta.function:
                    index = getattr(tc_delta, 'index', None)
                    function_name = getattr(tc_delta.function, 'name', None) or ""
                    function_arguments = getattr(tc_delta.function, 'arguments', None) or ""

                    tool_call = ToolCall(
                        id=getattr(tc_delta, 'id', '') or "",
                        type="function",
                        name=function_name,
                        arguments=function_arguments,
                        index=index if index is not None else 0,
                    )
                    tool_calls.append(tool_call)

        # Huawei MaaS workaround: merge tool_calls with same index
        # MaaS sometimes returns multiple tool_calls deltas with identical index,
        # where the second one only contains arguments increment.
        if _is_huawei_maas and len(tool_calls) > 1:
            merged_by_index: dict[int, ToolCall] = {}
            for tc in tool_calls:
                idx = tc.index if tc.index is not None else 0
                if idx not in merged_by_index:
                    merged_by_index[idx] = tc
                else:
                    existing = merged_by_index[idx]
                    new_id = tc.id or existing.id
                    new_name = tc.name or existing.name
                    new_args = existing.arguments + tc.arguments
                    merged_by_index[idx] = ToolCall(
                        id=new_id,
                        type="function",
                        name=new_name,
                        arguments=new_args,
                        index=idx,
                    )
            tool_calls = list(merged_by_index.values())

        # Build usage_metadata (usually only in the last chunk)
        usage_metadata = None
        if hasattr(chunk, 'usage') and chunk.usage:
            # Extract cost information if available
            input_cost, output_cost, total_cost = self._extract_cost_info(chunk.usage)

            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=getattr(chunk.usage, 'prompt_tokens', 0) or 0,
                output_tokens=getattr(chunk.usage, 'completion_tokens', 0) or 0,
                total_tokens=getattr(chunk.usage, 'total_tokens', 0) or 0,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
            )

        return AssistantMessageChunk(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason=choice.finish_reason or "null"
        )


def apply_openai_model_client_patch() -> None:
    """Monkey-patch upstream OpenAIModelClient with JiuwenClaw SSL/headers/stream behavior."""
    global _ORIGINAL_BUILD_REQUEST_PARAMS
    if _ORIGINAL_BUILD_REQUEST_PARAMS is None:
        _ORIGINAL_BUILD_REQUEST_PARAMS = OpenAIModelClient._build_request_params

    _impl = PatchOpenAIModelClient.__dict__
    setattr(OpenAIModelClient, "_create_async_openai_client", _impl["_create_async_openai_client"])
    setattr(OpenAIModelClient, "_parse_stream_chunk", _impl["_parse_stream_chunk"])
    setattr(OpenAIModelClient, "_build_request_params", _patched_build_request_params)
