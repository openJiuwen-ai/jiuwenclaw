import time
import random

from openai import AsyncOpenAI
from typing import Any, AsyncGenerator, Dict, Optional, List
import asyncio
import logging
import os

from openai.types.chat import ChatCompletionFunctionToolParam

from jiuwenswarm.agents.harness.search.exception_type import ContextExhaustedError

_CONTEXT_OVERFLOW_KEYWORDS = (
    "maximum context length",
    "context length",
    "context window",
    "reduce the length of the input",
    "input is too long",
    "exceeds the maximum",
)

# ---- 模块级全局限流控制（所有 OpenAIClient 实例共享） ----
_shared_semaphore: Optional[asyncio.Semaphore] = None
_rate_limit_lock = asyncio.Lock()
_last_request_time: float = 0.0

# 任意两次 LLM 请求之间的最小间隔（秒），用于削峰填谷。
# 默认 0.0：搜索子 agent 的 react loop 本身串行，固定间隔只是给每轮
# LLM 调用加地板、无削峰收益；需要削峰时用 SEARCH_LLM_MIN_INTERVAL 调高。
_MIN_REQUEST_INTERVAL = float(os.getenv("SEARCH_LLM_MIN_INTERVAL", "0.0"))


def _is_rate_limit_error(exc: Exception) -> bool:
    """判断异常是否为 LLM 限流错误（HTTP 429 或包含限流关键词）。"""
    try:
        from openai import RateLimitError
    except ImportError:
        RateLimitError = None  # type: ignore
    if RateLimitError is not None and isinstance(exc, RateLimitError):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in ("rate limit", "rate_limit", "too many requests", "429"))


def _is_context_overflow(exc: Exception) -> bool:
    try:
        from openai import BadRequestError
    except ImportError:
        BadRequestError = None  # type: ignore
    if BadRequestError is not None and isinstance(exc, BadRequestError):
        msg = str(exc).lower()
        return any(k in msg for k in _CONTEXT_OVERFLOW_KEYWORDS)
    return False


async def _wait_for_rate_slot() -> None:
    """
    模块级请求间隔控制：确保任意两次 LLM 请求之间有至少 _MIN_REQUEST_INTERVAL 秒的间隔。
    所有 OpenAIClient 实例共享同一个时钟和锁。
    """
    global _last_request_time
    async with _rate_limit_lock:
        elapsed = time.monotonic() - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


class OpenAIClient:
    def __init__(self,
                 model,
                 api_key,
                 base_url,
                 logger: logging.Logger,
                 temperature: Optional[float] = None,
                 top_p: Optional[float] = None,
                 presence_penalty: Optional[float] = None,
                 max_completion_tokens: Optional[int] = None,
                 timeout: Optional[int] = None,
                 max_parallel: int = 1,
                 max_retries: int = 3):
        global _shared_semaphore

        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.presence_penalty = presence_penalty
        self.max_completion_tokens = max_completion_tokens
        self.timeout = timeout

        import httpx
        self.client = AsyncOpenAI(api_key=api_key,
                                  base_url=base_url,
                                  timeout=timeout,
                                  http_client=httpx.AsyncClient(verify=False, trust_env=False))

        if _shared_semaphore is None:
            _shared_semaphore = asyncio.Semaphore(max_parallel)
            logger.info(f"[GlobalRateLimit] 初始化全局 LLM 并发控制: max_parallel={max_parallel}")
        else:
            logger.info(f"[GlobalRateLimit] 复用已有全局 LLM 并发控制 (当前 max_parallel={max_parallel} 被忽略，"
                         f"已由首个实例设定)")

        self._semaphore = _shared_semaphore
        self.max_retries = max_retries
        self.logger = logger

        self.llm_global_params = {
            "model": model,
            "temperature": temperature,
            "top_p": top_p,
            "presence_penalty": presence_penalty,
            "max_completion_tokens": max_completion_tokens,
            "tool_choice": "auto"
        }
        logger.info(f"llm global config is {self.llm_global_params}")

    async def invoke(self,
                     messages,
                     tools: Optional[List[ChatCompletionFunctionToolParam] | Dict[str, Any]] = None,
                     stream=False,
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
                    response = await self.client.chat.completions.create(
                        messages=messages,
                        tools=tools,
                        **self.llm_global_params,
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
