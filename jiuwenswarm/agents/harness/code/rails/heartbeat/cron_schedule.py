# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cron calculation kept local to the AgentServer-owned Heartbeat domain."""

from datetime import datetime
from zoneinfo import ZoneInfo


def validate_cron_expression(expr: str, *, timezone: str) -> None:
    """Validate the same 5/7-field syntax as the Gateway Cron helper."""
    from croniter import croniter  # type: ignore

    raw = str(expr or "").strip()
    if not raw:
        raise ValueError("cron_expr is empty")
    count = len(raw.split())
    if count == 5:
        normalized = f"0 {raw} *"
    elif count == 7:
        normalized = raw
    else:
        raise ValueError(
            f"cron_expr must have 5 or 7 fields, got {count} fields. "
            "5-field: minute hour day month dow. "
            "7-field (Quartz): second minute hour day month dow year."
        )
    if not croniter.is_valid(normalized, second_at_beginning=True):
        raise ValueError(f"invalid cron expression: '{raw}'")
    tz = ZoneInfo(timezone)
    croniter(normalized, datetime.now(tz=tz), second_at_beginning=True)


def next_cron_datetime(cron_expr: str, base_dt: datetime) -> datetime:
    from croniter import croniter  # type: ignore

    second_at_beginning = len(cron_expr.strip().split()) == 7
    value = croniter(
        cron_expr,
        base_dt,
        second_at_beginning=second_at_beginning,
    ).get_next(datetime)
    if not isinstance(value, datetime):
        raise RuntimeError("croniter returned invalid datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=base_dt.tzinfo)
    return value


__all__ = ["next_cron_datetime", "validate_cron_expression"]
