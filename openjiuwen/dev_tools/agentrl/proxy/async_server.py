# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
PatchedvLLMServer
-----------------

Ray-remote vLLM async inference server with instrumentation patches.
Extends verl's AsyncvLLMServer with custom chat completion handling.
"""

from copy import deepcopy

import ray
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from vllm.entrypoints.openai.protocol import ChatCompletionRequest, ErrorResponse
from verl.workers.rollout.vllm_rollout.vllm_async_server import AsyncvLLMServer


def _unwrap_ray_remote(cls):
    if hasattr(cls, "__ray_actor_class__"):
        cls = cls.__ray_actor_class__
    return cls


@ray.remote(num_cpus=1)
class PatchedvLLMServer(_unwrap_ray_remote(AsyncvLLMServer)):

    def __init__(self, *args, **kwargs):
        """Initialize PatchedvLLMServer and disable tool_config_path for agent-RL mode."""
        super().__init__(*args, **kwargs)

        self.config = deepcopy(self.config)
        self.config.rollout.multi_turn.tool_config_path = "/dev/null"

    async def chat_completion(self, raw_request: Request):
        """OpenAI-compatible HTTP Chat Completion endpoint."""
        request_json = await raw_request.json()
        request = ChatCompletionRequest(**request_json)
        generator = await self.openai_serving_chat.create_chat_completion(
            request, raw_request
        )

        if isinstance(generator, ErrorResponse):
            return JSONResponse(
                content=generator.model_dump(), status_code=generator.code
            )
        if request.stream:
            return StreamingResponse(
                content=generator, media_type="text/event-stream"
            )
        else:
            return JSONResponse(content=generator.model_dump())
