# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-model token prices, and the arithmetic that turns usage into money.

This module is the **fallback** half of the cost cascade. Prefer
provider-reported USD on the usage event (OpenRouter ``usage.cost``, etc.);
only when that is absent do callers multiply tokens by rates from
``models.pricing`` (see :mod:`jiuwenswarm.common.usage_cost`).

Prices are **configuration, not code**. ``api_base`` is user-supplied, so the
same ``model_name`` can front a hosted provider, a reseller, or a self-hosted
endpoint that costs nothing per token -- there is no correct built-in answer.
``DEFAULT_PRICES`` therefore ships empty on purpose: a bundled table would go
stale silently and report confident wrong money, which is worse than reporting
none. Operators declare rates under ``models.pricing`` in ``config.yaml`` for
APIs that only return token counts (DeepSeek direct, local Gemma, …).

A model with no rate and no provider cost is **unpriced**, never free.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional

CostStatus = Literal["priced", "partial", "unpriced"]

# Rates are quoted per million tokens, which is how every provider publishes
# them; keeping the same unit avoids a conversion error at the point where
# someone copies a number off a pricing page into config.
_PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class ModelPrice:
    """Rates for one model, per million tokens.

    Prompt tokens can be billed at three different rates, and providers that
    offer prompt caching charge all three:

    - ``input`` -- ordinary, uncached prompt tokens.
    - ``cache_read`` -- tokens served from the provider's cache. Much cheaper.
      ``None`` falls back to the input rate, which overstates rather than
      understates.
    - ``cache_write`` -- tokens written *into* the cache. Anthropic bills these
      at a **premium over** the input rate, not a discount. ``None`` falls back
      to the input rate, which is a **floor**: it understates for any provider
      charging a cache-creation premium. There is no safe default here, because
      guessing the multiplier would be inventing a number.
    """

    input: float
    output: float
    cache_read: Optional[float] = None
    cache_write: Optional[float] = None
    currency: str = "USD"
    # ISO date the rates were last checked against the provider's page. Carried
    # on chat.usage_summary.by_model and shown in /usage as "rates as of".
    last_verified: str = ""

    @property
    def effective_cache_read(self) -> float:
        return self.input if self.cache_read is None else self.cache_read

    @property
    def effective_cache_write(self) -> float:
        return self.input if self.cache_write is None else self.cache_write


@dataclass(frozen=True)
class PricedUsage:
    """One priced usage event, or an unpriced one."""

    input_cost: float
    output_cost: float
    total_cost: float
    priced: bool


# Intentionally empty -- see the module docstring.
DEFAULT_PRICES: dict[str, ModelPrice] = {}


def _coerce_price(raw: Any) -> Optional[ModelPrice]:
    """Build a ModelPrice from a config mapping, or None if unusable.

    Returns None rather than raising: a malformed entry for one model must not
    stop the turn or poison the other models' rates.
    """
    if not isinstance(raw, Mapping):
        return None
    try:
        input_rate = float(raw.get("input"))
        output_rate = float(raw.get("output"))
    except (TypeError, ValueError):
        return None
    if input_rate < 0 or output_rate < 0:
        return None

    def _optional_rate(key: str) -> Optional[float]:
        value = raw.get(key)
        if value is None:
            return None
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        return None if rate < 0 else rate

    return ModelPrice(
        input=input_rate,
        output=output_rate,
        cache_read=_optional_rate("cache_read"),
        cache_write=_optional_rate("cache_write"),
        currency=str(raw.get("currency") or "USD").strip().upper() or "USD",
        last_verified=str(raw.get("last_verified") or "").strip(),
    )


def _pricing_section(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    models = config.get("models")
    if not isinstance(models, Mapping):
        return {}
    pricing = models.get("pricing")
    return pricing if isinstance(pricing, Mapping) else {}


def resolve_price(
    model_name: Any,
    config: Optional[Mapping[str, Any]] = None,
) -> Optional[ModelPrice]:
    """Rate for ``model_name``, or None when nothing declares one.

    Configuration wins over the built-in table, so an operator can correct a
    bundled rate without editing code.
    """
    name = str(model_name or "").strip()
    if not name:
        return None

    configured = _pricing_section(config).get(name)
    price = _coerce_price(configured)
    if price is not None:
        return price

    return DEFAULT_PRICES.get(name)


def price_usage(
    price: Optional[ModelPrice],
    *,
    input_tokens: int,
    output_tokens: int,
    cache_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> PricedUsage:
    """Cost of one model call.

    ``input_tokens`` is the **whole prompt**, and both cache counts are subsets
    of it, not additional quantities::

        input_tokens = uncached + cache_read + cache_write

    Pricing either cache count on top of the input total would double-bill the
    cached prefix, which on a long session is most of the prompt: a traced run
    here reached ~98% cache on a 44k-token input.

    ``cache_write_tokens`` is **usually 0 today**, and not because caching is
    unused. The Anthropic client computes the count from
    ``cache_creation_input_tokens`` and then drops it, forwarding only the read
    count. Until that reaches the usage event, cache-writes are billed at the
    input rate here, which understates any provider charging a cache-creation
    premium. The parameter exists so the arithmetic is already correct the day
    the count arrives.
    """
    if price is None:
        return PricedUsage(0.0, 0.0, 0.0, priced=False)

    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))

    # Clamp against the prompt total, then against what is left, so a provider
    # over-reporting either count can never drive the uncached remainder
    # negative and turn a cost into a credit.
    cache_read = min(max(0, int(cache_tokens or 0)), inp)
    cache_write = min(max(0, int(cache_write_tokens or 0)), inp - cache_read)
    uncached = inp - cache_read - cache_write

    input_cost = (
        uncached * price.input
        + cache_read * price.effective_cache_read
        + cache_write * price.effective_cache_write
    ) / _PER_MILLION
    output_cost = out * price.output / _PER_MILLION

    return PricedUsage(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
        priced=True,
    )


def cost_status(priced_calls: int, unpriced_calls: int) -> CostStatus:
    """How much of a turn or session the reported cost actually covers.

    ``partial`` is the case that occurs in practice: a team drawing from a model
    pool that mixes a priced hosted model with a self-hosted one.
    """
    if priced_calls and not unpriced_calls:
        return "priced"
    if priced_calls and unpriced_calls:
        return "partial"
    return "unpriced"
