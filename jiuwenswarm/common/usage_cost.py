# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared token→USD accounting for single-agent, team, and subagent streams.

Pricing arithmetic lives in :mod:`jiuwenswarm.common.model_pricing`. This module
owns the accumulator shape, per-call application, and summary building so
``interface_deep`` and ``team_helpers`` do not diverge.

Cost resolution is a cascade:

1. **Provider-reported USD** on the usage event (``input_cost`` /
   ``output_cost`` / ``total_cost``) — e.g. OpenRouter's ``usage.cost``,
   LiteLLM response cost. Preferred when present and non-zero.
2. **Local ``models.pricing``** — multiply tokens by configured rates
   (DeepSeek and any provider that only returns token counts).
3. **Unpriced** — no rate and no provider cost; never pretend the call was free.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

from jiuwenswarm.common.model_pricing import cost_status, price_usage, resolve_price

# Imported at module level so tests can monkeypatch ``usage_cost.get_config``.
from jiuwenswarm.common.config import get_config

BucketKind = Literal["model", "member", "agent"]

_TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens", "cache_tokens")
_COST_KEYS = ("input_cost", "output_cost", "total_cost")
# Half of one 4-decimal display unit. Beyond this, in+out and billed total
# disagree for real (OpenRouter cost vs cost_details), not just float noise.
_COST_SPLIT_MISMATCH = 5e-5


def _attach_cost_fields(
    target: dict[str, Any],
    *,
    input_cost: Any = 0.0,
    output_cost: Any = 0.0,
    total_cost: Any = 0.0,
) -> None:
    """Write cost fields so displayed in + out never contradicts total.

    - Provider total only → ``total_cost`` alone.
    - Local split → round components, set ``total_cost`` to their sum.
    - Billed total disagrees with split → keep ``total_cost``, omit split.
    """
    try:
        inp = max(0.0, float(input_cost or 0.0))
    except (TypeError, ValueError):
        inp = 0.0
    try:
        out = max(0.0, float(output_cost or 0.0))
    except (TypeError, ValueError):
        out = 0.0
    try:
        tot = max(0.0, float(total_cost or 0.0))
    except (TypeError, ValueError):
        tot = 0.0

    split = inp + out
    if tot > 0 and split > 0 and abs(split - tot) > _COST_SPLIT_MISMATCH:
        target["total_cost"] = round(tot, 6)
        return
    if split > 0:
        inp_r = round(inp, 6)
        out_r = round(out, 6)
        if inp_r > 0:
            target["input_cost"] = inp_r
        if out_r > 0:
            target["output_cost"] = out_r
        target["total_cost"] = round(inp_r + out_r, 6)
    elif tot > 0:
        target["total_cost"] = round(tot, 6)


def new_usage_accumulator() -> dict[str, Any]:
    """Empty turn accumulator used by single-agent and team streams."""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_tokens": 0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
        "priced_calls": 0,
        "unpriced_calls": 0,
        "by_model": {},
        "by_member": {},
        "by_agent": {},
    }


def _bucket_key_field(kind: BucketKind) -> str:
    if kind == "model":
        return "model"
    if kind == "member":
        return "member"
    return "agent"


def named_usage_bucket(
    usage_accumulator: dict[str, Any],
    kind: BucketKind,
    name: Any,
) -> dict[str, Any]:
    """Get or create a named totals bucket (model / member / agent)."""
    map_key = f"by_{kind}"
    by_map = usage_accumulator.setdefault(map_key, {})
    key = str(name or "").strip() or "unknown"
    bucket = by_map.get(key)
    if bucket is None:
        bucket = {
            _bucket_key_field(kind): key,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_tokens": 0,
            "input_cost": 0.0,
            "output_cost": 0.0,
            "total_cost": 0.0,
            "priced_calls": 0,
            "unpriced_calls": 0,
            "last_verified": "",
        }
        by_map[key] = bucket
    return bucket


def model_usage_bucket(usage_accumulator: dict[str, Any], model_name: Any) -> dict[str, Any]:
    return named_usage_bucket(usage_accumulator, "model", model_name)


def apply_usage_cost(
    usage_meta: dict[str, Any],
    usage_accumulator: dict[str, Any],
    *,
    config: Optional[Mapping[str, Any]] = None,
) -> None:
    """Fill a usage event's cost fields in place, and record pricing coverage.

    Cascade: provider-reported cost on the event wins; otherwise multiply by
    ``models.pricing``; otherwise mark the call unpriced. Never invent $0.00
    for a missing rate.
    """
    model_bucket = model_usage_bucket(usage_accumulator, usage_meta.get("model_name"))

    existing = 0.0
    for key in _COST_KEYS:
        try:
            existing += float(usage_meta.get(key) or 0.0)
        except (TypeError, ValueError):
            pass
    if existing > 0:
        usage_accumulator["priced_calls"] += 1
        model_bucket["priced_calls"] += 1
        return

    if config is None:
        try:
            config = get_config()
        except Exception:  # noqa: BLE001 - pricing must never break a turn
            config = None

    price = resolve_price(usage_meta.get("model_name"), config)
    priced = price_usage(
        price,
        input_tokens=usage_meta.get("input_tokens", 0) or 0,
        output_tokens=usage_meta.get("output_tokens", 0) or 0,
        cache_tokens=usage_meta.get("cache_tokens", 0) or 0,
        cache_write_tokens=usage_meta.get("cache_write_tokens", 0) or 0,
    )
    if not priced.priced:
        usage_accumulator["unpriced_calls"] += 1
        model_bucket["unpriced_calls"] += 1
        return

    usage_meta["input_cost"] = priced.input_cost
    usage_meta["output_cost"] = priced.output_cost
    usage_meta["total_cost"] = priced.total_cost
    usage_accumulator["priced_calls"] += 1
    model_bucket["priced_calls"] += 1
    if price is not None and price.last_verified:
        model_bucket["last_verified"] = price.last_verified


def _add_to_bucket(bucket: dict[str, Any], usage_meta: Mapping[str, Any]) -> None:
    for token in _TOKEN_KEYS:
        bucket[token] = (bucket.get(token, 0) or 0) + (usage_meta.get(token, 0) or 0)
    for cost in _COST_KEYS:
        bucket[cost] = (bucket.get(cost, 0.0) or 0.0) + float(usage_meta.get(cost, 0.0) or 0.0)


def record_usage_event(
    usage_meta: dict[str, Any],
    usage_accumulator: dict[str, Any],
    *,
    member: Any = None,
    agent: Any = None,
    config: Optional[Mapping[str, Any]] = None,
) -> None:
    """Price one usage event and fold it into the turn accumulator.

    Always updates ``by_model``. When ``member`` / ``agent`` are set, also
    updates those dimensions (team members and DeepAgent subagents).
    """
    priced_before = int(usage_accumulator.get("priced_calls", 0) or 0)
    apply_usage_cost(usage_meta, usage_accumulator, config=config)
    event_priced = int(usage_accumulator.get("priced_calls", 0) or 0) > priced_before
    model_bucket = model_usage_bucket(usage_accumulator, usage_meta.get("model_name"))

    for token in _TOKEN_KEYS:
        amount = usage_meta.get(token, 0) or 0
        usage_accumulator[token] = (usage_accumulator.get(token, 0) or 0) + amount
        model_bucket[token] = (model_bucket.get(token, 0) or 0) + amount
    for cost in _COST_KEYS:
        amount = float(usage_meta.get(cost, 0.0) or 0.0)
        usage_accumulator[cost] = (usage_accumulator.get(cost, 0.0) or 0.0) + amount
        model_bucket[cost] = (model_bucket.get(cost, 0.0) or 0.0) + amount

    for kind, name in (("member", member), ("agent", agent)):
        if not name:
            continue
        bucket = named_usage_bucket(usage_accumulator, kind, name)  # type: ignore[arg-type]
        _add_to_bucket(bucket, usage_meta)
        if event_priced:
            bucket["priced_calls"] = (bucket.get("priced_calls", 0) or 0) + 1
        else:
            bucket["unpriced_calls"] = (bucket.get("unpriced_calls", 0) or 0) + 1
        verified = str(model_bucket.get("last_verified") or "").strip()
        if verified:
            bucket["last_verified"] = verified


def _serialize_named_buckets(
    by_map: Any,
    *,
    name_field: str,
) -> list[dict[str, Any]]:
    if not isinstance(by_map, dict) or not by_map:
        return []
    out: list[dict[str, Any]] = []
    for bucket in by_map.values():
        if not isinstance(bucket, dict):
            continue
        entry: dict[str, Any] = {
            name_field: bucket.get(name_field) or "unknown",
            "input_tokens": bucket.get("input_tokens", 0) or 0,
            "output_tokens": bucket.get("output_tokens", 0) or 0,
            "total_tokens": bucket.get("total_tokens", 0) or 0,
            "cost_status": cost_status(
                int(bucket.get("priced_calls", 0) or 0),
                int(bucket.get("unpriced_calls", 0) or 0),
            ),
        }
        if (bucket.get("cache_tokens", 0) or 0) > 0:
            entry["cache_tokens"] = bucket["cache_tokens"]
        _attach_cost_fields(
            entry,
            input_cost=bucket.get("input_cost", 0.0),
            output_cost=bucket.get("output_cost", 0.0),
            total_cost=bucket.get("total_cost", 0.0),
        )
        last_verified = str(bucket.get("last_verified") or "").strip()
        if last_verified:
            entry["last_verified"] = last_verified
        out.append(entry)
    return out


def build_usage_summary(usage_accumulator: Mapping[str, Any]) -> dict[str, Any]:
    """Build the ``usage`` object embedded in ``chat.usage_summary``."""
    summary: dict[str, Any] = {
        "input_tokens": usage_accumulator.get("input_tokens", 0) or 0,
        "output_tokens": usage_accumulator.get("output_tokens", 0) or 0,
        "total_tokens": usage_accumulator.get("total_tokens", 0) or 0,
    }
    input_tokens = summary["input_tokens"]
    if input_tokens > 0:
        cache_tokens = usage_accumulator.get("cache_tokens", 0) or 0
        summary["cache_tokens"] = cache_tokens
        summary["cache_hit_rate"] = f"{cache_tokens / input_tokens:.1%}"
    _attach_cost_fields(
        summary,
        input_cost=usage_accumulator.get("input_cost", 0.0),
        output_cost=usage_accumulator.get("output_cost", 0.0),
        total_cost=usage_accumulator.get("total_cost", 0.0),
    )

    priced = int(usage_accumulator.get("priced_calls", 0) or 0)
    unpriced = int(usage_accumulator.get("unpriced_calls", 0) or 0)
    if priced or unpriced:
        summary["cost_status"] = cost_status(priced, unpriced)
        if unpriced:
            summary["unpriced_calls"] = unpriced

    by_model = _serialize_named_buckets(usage_accumulator.get("by_model"), name_field="model")
    if by_model:
        summary["by_model"] = by_model
    by_member = _serialize_named_buckets(usage_accumulator.get("by_member"), name_field="member")
    if by_member:
        summary["by_member"] = by_member
    by_agent = _serialize_named_buckets(usage_accumulator.get("by_agent"), name_field="agent")
    if by_agent:
        summary["by_agent"] = by_agent
    return summary
