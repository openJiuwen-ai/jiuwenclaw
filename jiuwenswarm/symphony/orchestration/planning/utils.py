# -*- coding: utf-8 -*-
"""Utility helpers for orchestration planning."""

from __future__ import annotations

from typing import Any


def skill_id(node_id: Any) -> str:
    text = str(node_id or "")
    return text.removeprefix("skill:")


def clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
