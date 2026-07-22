# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-wide TLS environment coordination for in-process DeepResearch SDK setup."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import AsyncIterable, AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import TypeVar


_MISSING = object()
DEEPRESEARCH_TLS_ENV_LOCK = threading.Lock()
TASK_MANAGER_TLS_ENV = {
    "LLM_SSL_VERIFY": "false",
    "LLM_SSL_CERT": "",
    "TOOL_SSL_VERIFY": "false",
    "TOOL_SSL_CERT": "",
}
_T = TypeVar("_T")


@asynccontextmanager
async def scoped_deepresearch_tls_env(
    overrides: Mapping[str, str] | Callable[[], Mapping[str, str]],
):
    """Temporarily install SDK TLS env without losing unrelated concurrent writes."""
    acquired = False
    backoff = 0.001
    try:
        while not acquired:
            acquired = DEEPRESEARCH_TLS_ENV_LOCK.acquire(blocking=False)
            if not acquired:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 0.01)

        resolved_overrides = dict(overrides() if callable(overrides) else overrides)
        previous = {key: os.environ.get(key, _MISSING) for key in resolved_overrides}
        for key, value in resolved_overrides.items():
            os.environ[key] = value
        try:
            yield
        finally:
            for key, installed_value in resolved_overrides.items():
                if os.environ.get(key, _MISSING) != installed_value:
                    continue
                previous_value = previous[key]
                if previous_value is _MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous_value
    finally:
        if acquired:
            DEEPRESEARCH_TLS_ENV_LOCK.release()


async def iterate_with_scoped_tls_initialization(
    source: AsyncIterable[_T] | Callable[[], AsyncIterable[_T]],
    overrides: Mapping[str, str],
) -> AsyncIterator[_T]:
    """Scope SDK env reads to the first iteration that initializes models and tools."""
    async with scoped_deepresearch_tls_env(overrides):
        resolved_source = source() if callable(source) else source
        iterator = aiter(resolved_source)
        try:
            first_item = await anext(iterator)
        except StopAsyncIteration:
            return

    yield first_item
    async for item in iterator:
        yield item
