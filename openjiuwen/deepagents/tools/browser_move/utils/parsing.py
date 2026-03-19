#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared parsing helpers for runtime/controller responses."""

from __future__ import annotations

import json
from typing import Any


def extract_json_object(text: Any) -> dict[str, Any]:
    """Best-effort JSON extraction from model or tool text."""
    if isinstance(text, dict):
        return text
    if text is None:
        return {}

    raw = str(text).strip()
    if not raw:
        return {}

    marker_result = "### Result"
    marker_ran = "### Ran Playwright code"
    if marker_result in raw and marker_ran in raw:
        start = raw.find(marker_result) + len(marker_result)
        end = raw.find(marker_ran, start)
        if end > start:
            raw = raw[start:end].strip()

    for _ in range(2):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            raw = parsed.strip()
            continue
        break

    if "```json" in raw:
        start = raw.find("```json") + len("```json")
        end = raw.find("```", start)
        if end > start:
            block = raw[start:end].strip()
            try:
                parsed = json.loads(block)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed

    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        snippet = raw[first:last + 1]
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    return {}
