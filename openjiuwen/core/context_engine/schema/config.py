# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional
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

    enable_kv_cache_release : bool, default False
        Whether to release KV-cache for offloaded messages to reduce GPU memory
        pressure. When enabled, the attention key-value tensors corresponding to
        offloaded content are freed from GPU memory. The trade-off is that
        reloading these messages requires recomputing the KV-cache from scratch,
        increasing latency during recall. Recommended for long-running sessions
        with tight memory constraints.

    enable_reload : bool, default False
        Whether to enable automatic reloading of offloaded messages when the
        model requests them via reload hints (e.g., [[HANDLE:xxx]]). When enabled,
        the context engine monitors model outputs for reload signals and
        transparently fetches the full content from storage, injecting it back
        into the active context. When disabled, hints remain as-is in the
        conversation, and offloaded content is never automatically restored.
    """

    max_context_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_round_num: Optional[int] = Field(default=None, gt=0)
    enable_kv_cache_release: bool = Field(default=False)
    enable_reload: bool = Field(default=False)