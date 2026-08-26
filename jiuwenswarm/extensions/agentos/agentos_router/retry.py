# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared retry backoff for AgentOS registry / cleanup / heartbeat loops."""

from __future__ import annotations

import asyncio
import random

DEFAULT_BACKOFF_INITIAL_SECONDS = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_BACKOFF_CAP_SECONDS = 30.0
DEFAULT_BACKOFF_JITTER = 0.2


def compute_backoff(
    attempt: int,
    *,
    initial: float = DEFAULT_BACKOFF_INITIAL_SECONDS,
    factor: float = DEFAULT_BACKOFF_FACTOR,
    cap: float = DEFAULT_BACKOFF_CAP_SECONDS,
    jitter: float = DEFAULT_BACKOFF_JITTER,
) -> float:
    """Return the delay in seconds for a 0-based failed *attempt*.

    ``attempt=0`` → *initial*; each step multiplies by *factor*, capped at
    *cap*. *jitter* is a fraction of the base delay applied as
    ``±jitter`` uniform noise (``0`` disables jitter).
    """
    step = max(0, int(attempt))
    base = min(float(cap), float(initial) * (float(factor) ** step))
    spread = float(jitter)
    if spread <= 0 or base <= 0:
        return max(0.0, base)
    delta = base * spread
    return max(0.0, random.uniform(base - delta, base + delta))


async def backoff_sleep(
    attempt: int,
    *,
    initial: float = DEFAULT_BACKOFF_INITIAL_SECONDS,
    factor: float = DEFAULT_BACKOFF_FACTOR,
    cap: float = DEFAULT_BACKOFF_CAP_SECONDS,
    jitter: float = DEFAULT_BACKOFF_JITTER,
) -> float:
    """Sleep for :func:`compute_backoff` and return the delay used."""
    delay = compute_backoff(
        attempt, initial=initial, factor=factor, cap=cap, jitter=jitter
    )
    if delay > 0:
        await asyncio.sleep(delay)
    return delay
