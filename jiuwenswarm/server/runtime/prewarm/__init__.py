# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""GLM-5 / vLLM KV-cache prewarming for jiuwenswarm.

Prewarms the vLLM prefix cache by firing a max_tokens=1 request that
shares the same message/tool serialization as the real LLM call. The
body is built through the existing InferenceAffinityModelClient's
``_build_and_sanitize_params`` so token sequences match exactly.

Scenario:
  - B: tool-call round prefix (messages + assistant tool_call) at after_model_call

The rail is registered on the DeepAgent and bridged to the inner
ReActAgent for the AFTER_MODEL_CALL event. Prewarm is fire-and-forget.
"""
from jiuwenswarm.server.runtime.prewarm.config import PrewarmConfig
from jiuwenswarm.server.runtime.prewarm.coordinator import PrewarmCoordinator
from jiuwenswarm.server.runtime.prewarm.prewarm_rail import PrewarmRail
from jiuwenswarm.server.runtime.prewarm.startup import (
    WarmupModelClient,
    _build_warmup_config_base,
    _cleanup_prewarm_agent,
    run_startup_warmup,
    warmup_deep_agent_query,
    warmup_import_and_checkpointer,
)

__all__ = [
    "PrewarmConfig",
    "PrewarmCoordinator",
    "PrewarmRail",
    "WarmupModelClient",
    "_build_warmup_config_base",
    "_cleanup_prewarm_agent",
    "run_startup_warmup",
    "warmup_deep_agent_query",
    "warmup_import_and_checkpointer",
]
