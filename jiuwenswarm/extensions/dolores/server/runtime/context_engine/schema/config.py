# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Optional
from pydantic import BaseModel, Field


class ContextEngineConfig(BaseModel):
    """
    Configuration for the context engine.

    Attributes
    ----------
    max_context_message_num : int, optional
        Hard upper limit on the total number of messages allowed in any context.
        If None (default), no hard limit is enforced.

    default_window_message_num : int, optional
        Number of most-recent messages to retain when a sliding window is created
        without an explicit token or message count.  Must be > 0 if set; None
        (default) means "unlimited".

    default_window_round_num : int, optional
        Number of most-recent conversation rounds to retain when creating a
        sliding window. A round is defined as starting from a user message and
        ending at the next assistant message that contains no tool calls (i.e.,
        the final response in a tool-using turn). This spans across multiple
        messages if the assistant performs tool calls, but counts as one logical
        round. Useful for maintaining dialog coherence without specifying exact
        message counts. If set, takes precedence over `default_window_message_num`
        for round-based truncation. Must be > 0 if set; None (default) disables
        round-based windowing.

    enable_reload : bool, default False
        Whether to enable automatic reloading of offloaded messages when the
        model requests them via reload hints (e.g., [[HANDLE:xxx]]). When enabled,
        the context engine monitors model outputs for reload signals and
        transparently fetches the full content from storage, injecting it back
        into the active context. When disabled, hints remain as-is in the
        conversation, and offloaded content is never automatically restored.

    context_window_tokens : int, optional
        Total context window supported by the runtime model, including input and
        output tokens. Used for context compression telemetry only.

    model_name : str, optional
        Name of the LLM used by this context. Used to look up the default
        context window size from ``model_context_window_tokens``.

    model_context_window_tokens : dict[str, int], optional
        Best-effort fallback mapping from model name to total context window
        tokens. Explicit runtime values and context_window_tokens take priority.

    enable_openrouter_model_context_window_tokens : bool, default False
        Whether to fetch model context windows from OpenRouter when creating a
        context. Results are cached process-wide and explicit
        model_context_window_tokens values take priority.

    openrouter_request_timeout : float, default 3.0
        Timeout in seconds for fetching OpenRouter model metadata.
    """

    max_context_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_round_num: Optional[int] = Field(default=None, gt=0)
    enable_reload: bool = Field(default=False)
    enable_tiktoken_counter: bool = Field(default=False)
    context_window_tokens: Optional[int] = Field(default=None, gt=0)
    model_name: Optional[str] = Field(default=None)
    model_context_window_tokens: Optional[Dict[str, int]] = Field(default=None)
    enable_openrouter_model_context_window_tokens: bool = Field(default=False)
    openrouter_request_timeout: float = Field(default=3.0, gt=0)
