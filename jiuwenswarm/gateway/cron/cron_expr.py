from __future__ import annotations

from datetime import datetime

from zoneinfo import ZoneInfo


def cron_field_count(expr: str) -> int:
    return len(str(expr or "").split())


def _normalize_dow_field(dow: str) -> str:
    """Normalize day-of-week field for Quartz 7-field cron.

    Standard 5-field cron allows dow 0-7 (0 and 7 both = Sunday).
    Quartz 7-field only accepts 0-6 (0 = Sunday). Replace standalone 7 with 0.

    Also handles list/range forms: "1,2,3,4,5,6,7" → "1,2,3,4,5,6,0"
    """
    s = str(dow or "").strip()
    if not s or s == "*" or s == "?":
        return s
    # Split by comma, normalize each element
    parts = s.split(",")
    normalized = []
    for part in parts:
        p = part.strip()
        # Handle range like "1-7"
        if "-" in p and not p.startswith("-"):
            range_parts = p.split("-")
            if len(range_parts) == 2:
                lo, hi = range_parts[0].strip(), range_parts[1].strip()
                if hi == "7":
                    hi = "0"
                if lo == "7":
                    lo = "0"
                normalized.append(f"{lo}-{hi}")
                continue
        # Standalone number
        if p == "7":
            normalized.append("0")
        else:
            normalized.append(p)
    return ",".join(normalized)


def normalize_cron_expr(raw: str) -> str:
    """Normalize cron expression to 7-field Quartz format.

    5-field (minute hour day month dow) → prepend "0" (second) and append "*" (year).
    Also normalize day-of-week: standard cron allows 7 (=Sunday), Quartz only 0-6.
    7-field is left unchanged (already Quartz format, but dow 7 still normalized).
    Other field counts raise ValueError.
    """
    s = str(raw or "").strip()
    n = cron_field_count(s)
    if n == 5:
        fields = s.split()
        # fields: minute hour day month dow
        dow = _normalize_dow_field(fields[4])
        return f"0 {fields[0]} {fields[1]} {fields[2]} {fields[3]} {dow} *"
    if n == 7:
        fields = s.split()
        # fields: second minute hour day month dow year
        dow = _normalize_dow_field(fields[5])
        return f"{fields[0]} {fields[1]} {fields[2]} {fields[3]} {fields[4]} {dow} {fields[6]}"
    raise ValueError(
        f"cron_expr must have 5 or 7 fields, got {n} fields. "
        "5-field: minute hour day month dow. "
        "7-field (Quartz): second minute hour day month dow year."
    )


def iso_to_seven_field_cron(at_iso: str, *, timezone: str) -> str:
    """Convert ISO8601 datetime into 7-field cron (Quartz format):
    second minute hour day month dow year.

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
    return f"{dt.second} {dt.minute} {dt.hour} {dt.day} {dt.month} ? {dt.year}"


def validate_cron_expression(expr: str, *, timezone: str) -> None:
    """Validate cron expression (5-field or 7-field Quartz format).

    5-field (minute hour day month dow) is auto-normalized to 7-field by prepending
    second=0 and appending year=*.

    Note: for 7-field one-shot with a fixed past year, `croniter.get_next()`
    can fail; we only validate syntax here.
    """
    from croniter import croniter  # type: ignore

    raw = str(expr or "").strip()
    if not raw:
        raise ValueError("cron_expr is empty")

    normalized = normalize_cron_expr(raw)

    # Use second_at_beginning=True for Quartz 7-field format
    if not croniter.is_valid(normalized, second_at_beginning=True):
        raise ValueError(
            f"invalid cron expression: '{raw}'"
        )
    _ = ZoneInfo(timezone)
    croniter(normalized, datetime.now(tz=ZoneInfo(timezone)), second_at_beginning=True)


def denormalize_cron_expr(stored: str) -> str:
    """Convert internal 7-field Quartz cron back to 5-field standard cron.

    Internal storage uses 7-field Quartz format (``second minute hour day month dow year``)
    via ``normalize_cron_expr``. The A2A protocol doc shows devices expect 5-field
    standard cron (``minute hour day month dow``).

    Conversion rules:
    - Drop second field (field[0]) and year field (field[6]).
    - dow: ``?`` → ``*`` (Quartz ``?`` = no specific value → standard cron ``*``).
    - dow: ``0`` stays ``0`` (Sunday, valid in both formats).

    If the input is already 5-field, return it unchanged.
    """
    s = str(stored or "").strip()
    n = cron_field_count(s)
    if n == 5:
        return s
    if n == 7:
        fields = s.split()
        # fields: second minute hour day month dow year
        dow = fields[5]
        # Quartz uses ? for "no specific value"; standard cron uses *
        if dow == "?":
            dow = "*"
        return f"{fields[1]} {fields[2]} {fields[3]} {fields[4]} {dow}"
    # Unexpected field count; return as-is rather than risk data loss
    return s


def is_one_shot_cron(stored: str) -> bool:
    """Detect whether a stored 7-field Quartz cron represents a one-shot ``at`` task.

    ``iso_to_seven_field_cron`` produces ``second minute hour day month ? year``
    where ``dow == '?'`` and ``year`` is a concrete year (not ``*``). This shape
    uniquely identifies one-shot tasks converted from ``schedule.kind == 'at'``.

    Returns False for 5-field expressions or any 7-field expression that has
    ``year == '*'`` or ``dow != '?'``.
    """
    s = str(stored or "").strip()
    if cron_field_count(s) != 7:
        return False
    fields = s.split()
    # fields: second minute hour day month dow year
    dow = fields[5]
    year = fields[6]
    return dow == "?" and year != "*"


def seven_field_cron_to_iso(stored: str, *, timezone: str) -> str:
    """Reverse ``iso_to_seven_field_cron``: reconstruct ISO8601 datetime from
    a one-shot 7-field Quartz cron.

    Expected input shape: ``second minute hour day month ? year``. Returns an
    ISO8601 string with timezone offset (e.g. ``2026-07-16T16:00:00+08:00``).

    Raises ValueError if the input is not a one-shot cron (see ``is_one_shot_cron``).
    """
    s = str(stored or "").strip()
    if not is_one_shot_cron(s):
        raise ValueError(f"not a one-shot cron expression: '{stored}'")
    fields = s.split()
    # fields: second minute hour day month dow year
    second = int(fields[0])
    minute = int(fields[1])
    hour = int(fields[2])
    day = int(fields[3])
    month = int(fields[4])
    year = int(fields[6])
    tz = ZoneInfo(timezone)
    dt = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    return dt.isoformat()

