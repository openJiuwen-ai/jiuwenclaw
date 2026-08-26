# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenswarm.extensions.agentos.agentos_router.retry import (
    compute_backoff,
)


def test_compute_backoff_without_jitter() -> None:
    assert compute_backoff(0, initial=1.0, factor=2.0, cap=30.0, jitter=0) == 1.0
    assert compute_backoff(1, initial=1.0, factor=2.0, cap=30.0, jitter=0) == 2.0
    assert compute_backoff(2, initial=1.0, factor=2.0, cap=30.0, jitter=0) == 4.0
    assert compute_backoff(10, initial=1.0, factor=2.0, cap=30.0, jitter=0) == 30.0


def test_compute_backoff_negative_attempt_is_zero() -> None:
    assert compute_backoff(-3, initial=1.0, jitter=0) == 1.0


def test_compute_backoff_jitter_stays_within_band(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = iter([0.0, 0.5, 1.0])

    def _uniform(low: float, high: float) -> float:
        weight = next(samples)
        return low + (high - low) * weight

    monkeypatch.setattr(
        "jiuwenswarm.extensions.agentos.agentos_router.retry.random.uniform",
        _uniform,
    )
    base = 10.0
    low = compute_backoff(0, initial=base, factor=2.0, cap=30.0, jitter=0.2)
    mid = compute_backoff(0, initial=base, factor=2.0, cap=30.0, jitter=0.2)
    high = compute_backoff(0, initial=base, factor=2.0, cap=30.0, jitter=0.2)
    assert low == pytest.approx(8.0)
    assert mid == pytest.approx(10.0)
    assert high == pytest.approx(12.0)
