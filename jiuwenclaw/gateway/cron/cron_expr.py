from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from zoneinfo import ZoneInfo

# Harness / CronJob 常见默认提前量；相对 one-shot 时不可超过 delay。
_DEFAULT_WAKE_OFFSET_SECONDS = 300


def cron_field_count(expr: str) -> int:
    return len(str(expr or "").split())


def clamp_wake_offset_for_delay_seconds(
    wake_offset_seconds: Any,
    delay_seconds: float,
    *,
    default_when_missing: int = _DEFAULT_WAKE_OFFSET_SECONDS,
) -> int:
    """相对 one-shot：将 wake_offset 收敛到不超过 delay，避免 wake_at 早于创建时刻。

    ``wake_offset = min(requested_or_default, max(0, floor(delay_seconds)))``

    例：delay=60、默认 wake=300 → 收敛为 60（创建时即可 wake，到点 push）。
    delay≥300 时仍可保留完整提前量。
    """
    delay = float(delay_seconds)
    if delay <= 0:
        return 0
    if wake_offset_seconds is None:
        requested = int(default_when_missing)
    else:
        try:
            requested = int(wake_offset_seconds)
        except (TypeError, ValueError):
            requested = int(default_when_missing)
    if requested < 0:
        requested = 0
    max_allowed = max(0, math.floor(delay))
    return min(requested, max_allowed)


def iso_to_seven_field_cron(at_iso: str, *, timezone: str) -> str:
    """Convert ISO8601 datetime into croniter 7-field cron:
    minute hour day month dow second year.

    If the input has no timezone, interpret it in `timezone`.
    """
    s = (at_iso or "").strip()
    if not s:
        raise ValueError("at_iso is empty")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    tz = ZoneInfo(timezone)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    # day-of-week is left as '*' because we fixed year/month/day.
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} * {dt.second} {dt.year}"


def validate_cron_expression(expr: str, *, timezone: str) -> None:
    """Validate cron expression format is 5 or 7 fields and croniter accepts it.

    Note: for 7-field one-shot with a fixed past year, `croniter.get_next()`
    can fail; we only validate syntax here.
    """
    from croniter import croniter  # type: ignore

    raw = str(expr or "").strip()
    if not raw:
        raise ValueError("cron_expr is empty")

    n = cron_field_count(raw)
    if n not in (5, 7):
        raise ValueError(
            f"cron_expr must have 5 fields or 7 fields, got {n} fields"
        )
    if not croniter.is_valid(raw):
        raise ValueError("invalid cron expression")
    # Ensure timezone itself is valid. Do not require a future matching date.
    _ = ZoneInfo(timezone)
    croniter(raw, datetime.now(tz=ZoneInfo(timezone)))


def validate_cron_schedule_not_stale(*, cron_expr: str, timezone: str) -> None:
    """Reject 5-field crons that target today but whose next fire is far away (passed one-shot)."""
    from croniter import croniter  # type: ignore

    expr = str(cron_expr or "").strip()
    if not expr:
        raise ValueError("cron_expr is required when delay_seconds is not set")

    fields = expr.split()
    if len(fields) != 5:
        return

    dom_s, month_s = fields[2], fields[3]
    if dom_s == "*" or month_s == "*":
        return
    try:
        dom_i, month_i = int(dom_s), int(month_s)
    except ValueError:
        return

    tz = ZoneInfo(timezone)
    now = datetime.now(tz=tz)
    if now.day != dom_i or now.month != month_i:
        return

    next_dt = croniter(expr, now).get_next(datetime)
    if next_dt.timestamp() - now.timestamp() > 86400:
        raise ValueError(
            f"cron_expr '{expr}' targets today but the time has already passed "
            f"(next run: {next_dt.isoformat()}). Use delay_seconds for relative one-shot tasks."
        )
