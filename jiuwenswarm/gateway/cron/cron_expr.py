from __future__ import annotations

import re
from datetime import datetime

from zoneinfo import ZoneInfo


# Detects whether a dow token contains day-name letters (MON, FRI, ...).
# Such tokens are converted to numeric croniter 5-field dow (0=SUN..6=SAT)
# so that stored cron expressions never contain English letters.
_DOW_HAS_LETTER = re.compile(r"[A-Za-z]")

# croniter 5-field dow convention: 0=SUN, 1=MON, 2=TUE, ..., 6=SAT.
# English day names map directly to these numbers (no Quartz offset needed).
_DOW_NAME_TO_NUM: dict[str, int] = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
}


def cron_field_count(expr: str) -> int:
    return len(str(expr or "").split())


def _convert_dow_name_to_num_token(token: str) -> str:
    """Convert a single dow token with English day names to numeric croniter 5-field.

    Handles ``MON``, ``MON-FRI``, ``SUN``, ``MON-FRI/2``, ``SUN-SAT/3`` etc.
    Unknown day names raise ValueError. ``*`` and ``?`` are returned unchanged.

    The output uses croniter 5-field convention: 0=SUN, 1=MON, ..., 6=SAT.
    """
    token = token.strip()
    if token in ("*", "?"):
        return token

    def _name_to_num(name: str) -> int:
        key = name.strip().upper()
        if key not in _DOW_NAME_TO_NUM:
            raise ValueError(f"unknown day-of-week name: '{name}'")
        return _DOW_NAME_TO_NUM[key]

    if "/" in token:
        base, step = token.split("/", 1)
        return f"{_convert_dow_name_to_num_token(base)}/{step}"

    if "-" in token:
        start_s, end_s = token.split("-", 1)
        start = _name_to_num(start_s)
        end = _name_to_num(end_s)
        return f"{start}-{end}"

    return str(_name_to_num(token))


def _convert_quartz_dow_token(token: str) -> str:
    """Convert a single dow token (no commas) from Quartz to croniter 5-field.

    Quartz dow: 1=SUN, 2=MON, ..., 7=SAT
    croniter 5-field dow: 0=SUN, 1=MON, ..., 6=SAT (7 also = SUN)

    - '*' or '?' → unchanged
    - day-name tokens (MON, MON-FRI, SUN, ...) → converted to numeric
      (croniter 5-field: 0=SUN..6=SAT); no Quartz offset since English names
      share the same semantic in both conventions.
    - numeric tokens → each number remapped via (n - 1) % 7
    - ranges (a-b), lists (a,b,c), steps (a-b/c or */c) all handled
    """
    token = token.strip()
    if token in ("*", "?"):
        return token
    if _DOW_HAS_LETTER.search(token):
        # English day-name expression → numeric (no Quartz offset needed).
        return _convert_dow_name_to_num_token(token)

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


def _normalize_5field_dow(dow_field: str) -> str:
    """Normalize the dow field of a 5-field cron expression to numeric.

    English day-name dow (``MON``, ``MON-FRI``, etc.) is converted to numeric
    croniter 5-field values (0=SUN..6=SAT). Numeric dow and ``*``/``?`` are
    returned unchanged (no Quartz offset — 5-field input is already croniter).
    """
    parts = dow_field.split(",")
    out: list[str] = []
    for p in parts:
        token = p.strip()
        if token in ("*", "?") or not _DOW_HAS_LETTER.search(token):
            out.append(token)
        else:
            out.append(_convert_dow_name_to_num_token(token))
    return ",".join(out)


def _expand_dow_range_token(token: str) -> str:
    """Expand a single dow token (no commas) to a comma-separated list of numbers.

    Ranges are expanded to explicit lists so the stored dow field uses only
    comma-separated single numbers (no ``-`` ranges)::

        '1-5'    → '1,2,3,4,5'
        '1-5/2'  → '1,3,5'        (range + step → apply step over expanded range)
        '5-1'    → '5,6,0,1'       (wrap-around: FRI→MON)
        '0'      → '0'             (single value unchanged)
        '*'      → '*'             (wildcard unchanged)
        '?'      → '?'             (wildcard unchanged)
        '*/2'    → '*/2'           (wildcard + step unchanged)
        '1'      → '1'

    Numeric input only; English names must be converted to numbers first via
    :func:`_convert_dow_name_to_num_token`.
    """
    token = token.strip()
    # '?' is Quartz-only; croniter treats it as '*'. Normalize to '*' so the
    # stored 5-field expression is pure standard cron (no Quartz '?').
    if token == "?":
        return "*"
    if token == "*":
        return token

    # wildcard + step (*/c) — keep as-is, do not expand.
    if token.startswith("*/"):
        return token

    # range + optional step: "a-b" or "a-b/c"
    if "-" in token:
        base, _, step = token.partition("/")
        start_s, end_s = base.split("-", 1)
        start = int(start_s.strip())
        end = int(end_s.strip())
        # Expand the range, handling wrap-around (start > end).
        if start <= end:
            nums = list(range(start, end + 1))
        else:
            # wrap-around: e.g. 5-1 → 5,6,0,1 (cron dow 0-6, wrap via mod 7)
            nums = []
            v = start
            while True:
                nums.append(v % 7)
                if v % 7 == end:
                    break
                v += 1
        # Apply step if present (step applies to positions in the expanded range).
        if step:
            s = int(step)
            nums = nums[::s]
        return ",".join(str(n) for n in nums)

    # single value + optional step (e.g. "5/2") — keep as-is.
    return token


def _expand_dow_field(dow_field: str) -> str:
    """Expand the dow field to comma-separated single numbers.

    Each comma-separated part is expanded via :func:`_expand_dow_range_token`,
    then the parts are joined with commas. ``*`` / ``?`` / ``*/c`` tokens are
    preserved as-is within their part.

    Examples::

        '1-5'      → '1,2,3,4,5'
        '1-5/2'    → '1,3,5'
        '1-3,5-6'  → '1,2,3,5,6'
        '*'        → '*'
        '0'        → '0'
    """
    parts = dow_field.split(",")
    out: list[str] = []
    for p in parts:
        out.append(_expand_dow_range_token(p))
    return ",".join(out)


def _normalize_and_expand_dow(dow_field: str, *, from_quartz: bool = False) -> str:
    """Normalize dow to numeric then expand ranges to comma-separated lists.

    Pipeline: (1) English names → numeric, (2) Quartz offset if ``from_quartz``,
    (3) expand ranges to comma-separated single numbers.

    - ``from_quartz=False``: 5-field input, names→num, no offset, then expand.
    - ``from_quartz=True``: 6/7-field Quartz input, names→num, numeric Quartz
      offset (1=SUN..7=SAT → 0=SUN..6=SAT), then expand.
    """
    if from_quartz:
        numeric = _convert_quartz_dow(dow_field)
    else:
        numeric = _normalize_5field_dow(dow_field)
    return _expand_dow_field(numeric)


def _normalize_day_field(day_field: str) -> str:
    """Normalize the day-of-month field: Quartz ``?`` → ``*``.

    Standard 5-field cron has no ``?`` (it is Quartz-only). croniter tolerates
    ``?`` by treating it as ``*``, but we normalize to ``*`` so the stored
    expression is pure standard cron. Other tokens are returned unchanged.
    """
    token = day_field.strip()
    if token == "?":
        return "*"
    return token


def normalize_cron_expr(raw: str) -> str:
    """Normalize cron expression to 5-field standard format with numeric dow.

    Accepts 5-field, 6-field, or 7-field Quartz cron:
      5-field:  ``minute hour day month dow``
      6-field:  ``second minute hour day month dow``
      7-field:  ``second minute hour day month dow year``

    6/7-field Quartz are converted to 5-field by dropping the second and year
    fields and remapping numeric dow from Quartz (1=SUN..7=SAT) to croniter
    (0=SUN..6=SAT, 7=SUN). English day-name dow (``MON``, ``MON-FRI``, etc.)
    is converted to numeric (0=SUN..6=SAT) so the stored expression never
    contains letters. 5-field input dow is also normalized to numeric.

    The dow field is then expanded to a comma-separated list of single numbers
    (no ``-`` ranges) so the stored dow is fully explicit::

        '1-5'    → '1,2,3,4,5'
        '1-5/2'  → '1,3,5'

    Quartz-only ``?`` (in day-of-month or dow) is normalized to ``*`` so the
    stored 5-field expression is pure standard cron (no Quartz ``?``).

    Examples::

        '0 15 10 ? * MON-FRI *'  →  '15 10 * * 1,2,3,4,5'
        '0 0 9 * * ? *'          →  '0 9 * * *'
        '0 15 10 ? * 2-6 *'      →  '15 10 * * 1,2,3,4,5'   (numeric dow remapped)
        '15 10 ? * MON-FRI'      →  '15 10 * * 1,2,3,4,5'   (5-field, name→num)
        '15 10 ? * 1-5/2'        →  '15 10 * * 1,3,5'       (range+step expanded)
        '55 10 ? * 1,2,3,4,5'    →  '55 10 * * 1,2,3,4,5'   (? in day → *)
    """
    s = str(raw or "").strip()
    fields = s.split()
    n = len(fields)

    if n == 5:
        # 5-field: normalize dow (English names → numeric), expand ranges, keep other fields.
        minute, hour, day, month, dow = fields
        return f"{minute} {hour} {_normalize_day_field(day)} {month} {_normalize_and_expand_dow(dow)}"

    if n == 6:
        # second minute hour day month dow → minute hour day month dow
        minute, hour, day, month, dow = fields[1], fields[2], fields[3], fields[4], fields[5]
        return f"{minute} {hour} {_normalize_day_field(day)} {month} {_normalize_and_expand_dow(dow, from_quartz=True)}"

    if n == 7:
        # second minute hour day month dow year → minute hour day month dow
        minute, hour, day, month, dow = fields[1], fields[2], fields[3], fields[4], fields[5]
        return f"{minute} {hour} {_normalize_day_field(day)} {month} {_normalize_and_expand_dow(dow, from_quartz=True)}"

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


