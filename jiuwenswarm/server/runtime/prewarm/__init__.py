# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""GLM-5 / vLLM KV-cache prewarming for jiuwenswarm.

Prewarms the vLLM prefix cache by firing a max_tokens=1 request that
shares the same message/tool serialization as the real LLM call. The
body is built through the existing InferenceAffinityModelClient's
``_build_and_sanitize_params`` so token sequences match exactly.

Three scenarios:
  - A: static prefix (system + tools) on the first model call
  - B: tool-call round prefix (messages + assistant tool_call) at after_model_call
  - C: post-answer prefix (messages + assistant answer) at after_model_call

The rail is registered on the DeepAgent and bridged to the inner
ReActAgent for BEFORE_MODEL_CALL / AFTER_MODEL_CALL events.
"""
from jiuwenswarm.server.runtime.prewarm.config import PrewarmConfig
from jiuwenswarm.server.runtime.prewarm.coordinator import PrewarmCoordinator
from jiuwenswarm.server.runtime.prewarm.rail import PrewarmRail

__all__ = ["PrewarmConfig", "PrewarmCoordinator", "PrewarmRail"]
