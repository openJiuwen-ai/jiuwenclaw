"""Reward normalization helpers used to keep metrics comparable within an iteration."""

from __future__ import annotations


def min_max_normalize(values: list[float]) -> list[float]:
    """Scale ``values`` into ``[0, 1]``. Constant inputs map to all-1.0 (no signal)."""
    if not values:
        return []
    low = min(values)
    high = max(values)
    spread = high - low
    if spread <= 1e-9:
        return [1.0 for _ in values]
    return [(v - low) / spread for v in values]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = ["min_max_normalize", "clamp01"]
