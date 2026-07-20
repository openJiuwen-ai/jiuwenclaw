from __future__ import annotations

import re
from datetime import datetime

from zoneinfo import ZoneInfo


# Detects whether a dow token contains day-name letters (MON, FRI, ...).
# croniter 5-field supports English day names natively, so such tokens are
# kept unchanged during Quartz→5-field conversion.
_DOW_HAS_LETTER = re.compile(r"[A-Za-z]")


def cron_field_count(expr: str) -> int:
    return len(str(expr or "").split())


def _convert_quartz_dow_token(token: str) -> str:
    """Convert a single dow token (no commas) from Quartz numeric to croniter 5-field.

    Quartz dow: 1=SUN, 2=MON, ..., 7=SAT
    croniter 5-field dow: 0=SUN, 1=MON, ..., 6=SAT (7 also = SUN)

    - '*' or '?' → unchanged
    - day-name tokens (MON, MON-FRI, SUN, ...) → unchanged (croniter supports)
    - numeric tokens → each number remapped via (n - 1) % 7
    - ranges (a-b), lists (a,b,c), steps (a-b/c or */c) all handled
    """
    token = token.strip()
    if token in ("*", "?"):
        return token
    if _DOW_HAS_LETTER.search(token):
        return token  # day-name expression, croniter supports natively

    if "/" in token:
        base, step = token.split("/", 1)
        return f"{_convert_quartz_dow_base(base)}/{step}"
    return _convert_quartz_dow_base(token)


def _convert_quartz_dow_base(base: str) -> str:
    """Convert a dow base token (no step, no comma) from Quartz to croniter."""
    base = base.strip()
    if base == "*":
        return base
    if "-" in base:
        start_s, end_s = base.split("-", 1)
        start = int(start_s.strip())
        end = int(end_s.strip())
        return f"{(start - 1) % 7}-{(end - 1) % 7}"
    return str((int(base) - 1) % 7)


def _convert_quartz_dow(dow_field: str) -> str:
    """Convert the dow field from Quartz numeric convention to croniter 5-field."""
    parts = dow_field.split(",")
    return ",".join(_convert_quartz_dow_token(p) for p in parts)


def normalize_cron_expr(raw: str) -> str:
    """Normalize cron expression to 5-field standard format.

    Accepts 5-field, 6-field, or 7-field Quartz cron:
      5-field:  ``minute hour day month dow``
      6-field:  ``second minute hour day month dow``
      7-field:  ``second minute hour day month dow year``

    6/7-field Quartz are converted to 5-field by dropping the second and year
    fields and remapping numeric dow from Quartz (1=SUN..7=SAT) to croniter
    (0=SUN..6=SAT, 7=SUN). Day-name dow (``MON``, ``MON-FRI``, etc.) is kept
    as-is since croniter 5-field supports it natively.

    Examples::

        '0 15 10 ? * MON-FRI *'  →  '15 10 ? * MON-FRI'
        '0 0 9 * * ? *'          →  '0 9 * * ?'
        '0 15 10 ? * 2-6 *'      →  '15 10 ? * 1-5'   (numeric dow remapped)
    """
    s = str(raw or "").strip()
    fields = s.split()
    n = len(fields)

    if n == 5:
        return s

    if n == 6:
        # second minute hour day month dow → minute hour day month dow
        minute, hour, day, month, dow = fields[1], fields[2], fields[3], fields[4], fields[5]
        return f"{minute} {hour} {day} {month} {_convert_quartz_dow(dow)}"

    if n == 7:
        # second minute hour day month dow year → minute hour day month dow
        minute, hour, day, month, dow = fields[1], fields[2], fields[3], fields[4], fields[5]
        return f"{minute} {hour} {day} {month} {_convert_quartz_dow(dow)}"

    raise ValueError(
        f"cron_expr must have 5, 6, or 7 fields, got {n} fields. "
        "5-field: minute hour day month dow. "
        "7-field Quartz: second minute hour day month dow year."
    )


def iso_to_five_field_cron(at_iso: str, *, timezone: str) -> str:
    """Convert ISO8601 datetime into 5-field cron: ``minute hour day month dow``.

    The year is dropped — 5-field cron has no year field, so the expression
    repeats yearly. Pair with ``delete_after_run=True`` for one-shot semantics:
    the task fires once at the matching month/day/hour/minute, then the
    scheduler marks it expired after the push.

    If the input has no timezone, interpret it in ``timezone``.
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
    # dow in standard cron: 0-6 (0=Sunday). Python weekday(): 0=Monday..6=Sunday.
    # croniter 5-field expects 0=Sunday, so convert Python weekday() to cron dow.
    cron_dow = (dt.weekday() + 1) % 7
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} {cron_dow}"


def validate_cron_expression(expr: str, *, timezone: str) -> None:
    """Validate a cron expression, accepting 5/6/7-field Quartz.

    6/7-field Quartz are first normalized to 5-field via
    :func:`normalize_cron_expr`, then validated with croniter.
    """
    from croniter import croniter  # type: ignore

    raw = str(expr or "").strip()
    if not raw:
        raise ValueError("cron_expr is empty")

    # normalize first (handles 7-field→5-field conversion), then validate
    normalized = normalize_cron_expr(raw)
    if cron_field_count(normalized) != 5:
        raise ValueError(
            f"cron_expr normalized to {cron_field_count(normalized)} fields, expected 5. "
            "5-field: minute hour day month dow."
        )

    if not croniter.is_valid(normalized):
        raise ValueError(f"invalid cron expression: '{raw}' (normalized: '{normalized}')")
    _ = ZoneInfo(timezone)
    croniter(normalized, datetime.now(tz=ZoneInfo(timezone)))

