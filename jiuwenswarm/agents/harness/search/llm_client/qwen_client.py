import time
import random
from openai import AsyncOpenAI
from typing import Any, Dict, Optional, List
import asyncio
import logging

from openai.types.chat import ChatCompletionFunctionToolParam

from jiuwenswarm.agents.harness.search.exception_type import ContextExhaustedError
from jiuwenswarm.agents.harness.search.llm_client.openai_client import OpenAIClient, _wait_for_rate_slot, _is_context_overflow, _is_rate_limit_error
from jiuwenswarm.agents.harness.search.llm_client.stream_aggregator import async_stream_with_aggregation


class QwenClient(OpenAIClient):
    async def invoke(self,
                     messages,
                     tools: Optional[List[ChatCompletionFunctionToolParam] | Dict[str, Any]] = None,
                     stream: bool = False,
                     **kwargs):
        """
        非流式调用LLM

        所有 OpenAIClient 实例共享一个全局 Semaphore（控制并发数）
        和请求间隔控制（削峰填谷），避免瞬时集中请求触发 LLM 限流。

        重试策略：
        - 限流错误（429）：指数退避，base=5s, max=120s, 带随机抖动
        - 其他错误：固定随机延迟 3~12s
        """
        final_exception = ValueError("LLM max retries exceeded")
        for retry_idx in range(self.max_retries):
            try:
                # 先获取全局并发槽位，再等待请求间隔
                async with self._semaphore:
                    await _wait_for_rate_slot()
                    if stream:
                        async for event in self.stream(messages=messages,
                                                       tools=tools,
                                                       **kwargs):
                            response = event
                    else:
                        response = await self.chat(
                            messages=messages,
                            tools=tools,
                            **kwargs,
                        )
                return response
            except Exception as E:
                # context超限则不可重试
                if _is_context_overflow(E):
                    self.logger.error(f"上下文长度超限，不可重试: {E}")
                    raise ContextExhaustedError(str(E)) from E

                # 区分限流错误，使用不同的退避策略
                is_rate_limited = _is_rate_limit_error(E)
                if is_rate_limited:
                    # 指数退避：5s * 2^retry，上限 120s，加 ±25% 随机抖动
                    base = 5.0 * (2 ** retry_idx)
                    wait_s = min(base, 120.0) * (0.75 + random.random() * 0.5)
                    self.logger.warning(
                        f"[RateLimit] 限流错误, 指数退避 {wait_s:.1f}s | "
                        f"retry {retry_idx + 1}/{self.max_retries}"
                    )
                else:
                    wait_s = 2 + random.uniform(1, 10)
                    self.logger.error(
                        f"llm invoke error: {E}, {wait_s:.1f}s后重试 | "
                        f"retry {retry_idx + 1}/{self.max_retries} | Timeout:{self.client.timeout}"
                    )
                await asyncio.sleep(wait_s)
                final_exception = E
        raise final_exception

    async def chat(self,
                   messages,
                   tools: Optional[List[ChatCompletionFunctionToolParam] | Dict[str, Any]] = None,
                   **kwargs):
        response = await self.client.chat.completions.create(
            messages=messages,
            tools=tools,
            **self.llm_global_params,
            **kwargs,
        )
        return response

    async def stream(self,
                     messages,
                     tools: Optional[List[ChatCompletionFunctionToolParam] | Dict[str, Any]] = None,
                     only_final_result: bool = True,
                     **kwargs):
        response = await self.client.chat.completions.create(
            messages=messages,
            tools=tools,
            stream=True,
            **self.llm_global_params,
            **kwargs,
        )
        async for event in async_stream_with_aggregation(response):
            # 如果开启聚合
            if only_final_result:
                if event["type"] == "done":
                    yield event["result"]
                else:
                    continue
            else:
                yield event