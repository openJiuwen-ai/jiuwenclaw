# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store Utilities

Utility functions for graph store operations
"""

__all__ = ["batched"]

import itertools
import uuid
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Awaitable, Callable, Generator, Iterable, Mapping, Optional, TypeVar

from openjiuwen.core.common.logging import store_logger

batched: Callable[[Iterable, int], Generator]
T = TypeVar("T")  # result type
M = TypeVar("M")  # metadata type

# itertools.batch was added in Python 3.12
if hasattr(itertools, "batched"):
    batched = getattr(itertools, "batched")
else:

    def batched(iterable: Iterable, n: int, **kwargs) -> Generator:
        """Taken from https://docs.python.org/3/library/itertools.html#itertools.batched with modifications"""
        if n < 1:
            raise ValueError("n must be at least one")
        iterator = iter(iterable)
        batch = tuple(itertools.islice(iterator, n))
        while batch:
            if kwargs.get("strict") and len(batch) != n:
                raise ValueError("batched(): incomplete batch")
            yield batch
            batch = tuple(itertools.islice(iterator, n))


async def with_metadata(coro: Awaitable[T], metadata: M) -> tuple[T, M]:
    """Await a coroutine and return its result together with metadata.

    Useful when migrating synchronous code that used `concurrent.futures` to
    `asyncio`. It replaces the common pattern of mapping futures to metadata.

    Examples:
        Original `concurrent.futures` pattern:

        >>> futures = {executor.submit(fn, arg1, ...): arg1 for arg1, ... in iterable}
        >>> for future in concurrent.futures.as_completed(futures):
        ...     result = future.result()
        ...     arg1 = futures[future]

        Equivalent `asyncio` usage:

        >>> tasks = [
        ...     asyncio.create_task(
        ...         with_metadata(fn(arg1, ...), arg1)
        ...     )
        ...     for arg1, ... in iterable
        ... ]
        >>> for task in asyncio.as_completed(tasks):
        ...     result, arg1 = await task

    Args:
        coro: Awaitable to execute.
        metadata: Metadata to return alongside the result.

    Returns:
        Tuple containing `(result, metadata)`.
    """
    result = await coro
    return result, metadata


def get_uuid() -> str:
    """Generate UUID from uuid4.

    Returns:
        str: uuid with length of 32.
    """
    return uuid.uuid4().hex


def ensure_unique_uuids(backend: Any, ids: list[Any], collection: str, skip: bool = False) -> list[Any]:
    """De-duplicate given uuids in a specific collection of graph memory database.

    Args:
        backend (GraphBackend): graph memory database backend.
        ids (list[Any]): list of uuids to de-duplicate.
        collection (str): name of collection (entities/relations/episodes).
        skip (bool): skip de-duplication to avoid certain errors.

    Returns:
        list[Any]: list of unique uuids.
    """
    unique_ids = [_id or get_uuid() for _id in ids]
    if skip:
        return unique_ids
    kwargs = dict(collection=collection, output_fields=["uuid"])
    if not backend.is_empty(collection):
        dup_list = [dup["uuid"] for dup in backend.query(ids=unique_ids, **kwargs)]
        while dup_list:
            new_uuids = []
            for dup_uuid in dup_list:
                unique_ids[unique_ids.index(dup_uuid)] = tmp_uuid = get_uuid()
                new_uuids.append(tmp_uuid)
            dup_list = [dup["uuid"] for dup in backend.query(ids=new_uuids, **kwargs)]
    return unique_ids


def format_list_of_messages(
    messages: list[dict], role_replace: Optional[Mapping[str, str]] = None, template: str = "{role}: {content}\n"
) -> str:
    """Format a list of messages into a string.

    Args:
        messages (list[dict]): list of messages representing a conversation.
        role_replace (Optional[Mapping[str, str]]): mapping from roles to names (assistant -> Writing Assistant Agent)
        template (str, optional): formatting template for each message entry. Defaults to "{role}: {content}\n".

    Returns:
        str: formatted list of messages.
    """
    result = ""
    role_replace = role_replace or dict()
    for msg in messages:
        msg = msg.copy()
        role = msg.pop("role", "")
        result += template.format(role=role_replace.get(role, role), **msg)
    return result


def safe_timestamp(datetime_obj: datetime) -> float:
    """Some Operating System (i.e. Windows) cannot handle negative timestamps natively"""
    if datetime_obj.year < 1970:
        this_tzinfo = timezone.utc if datetime_obj.tzinfo else None
        return (datetime_obj - datetime(1970, 1, 1, tzinfo=this_tzinfo)).total_seconds()
    return datetime_obj.timestamp()


def get_current_utc_timestamp() -> int:
    """Get current UTC timestamp as integer.

    Returns:
        int: UTC timestamp.
    """
    return int(safe_timestamp(datetime.now(timezone.utc)))


def format_timestamp(t: int | float, tz: tzinfo = timezone.utc, fmt: str = r"(%a) %Y/%b/%d %H:%M:%S") -> str:
    """Format a UNIX timestamp into readable representation like "(Wed) 2025/Sep/10 15:56:53".

    Args:
        t (int | float): timestamp.
        tz (tzinfo, optional): timezone. Defaults to timezone.utc.
        fmt (str, optional): format for datetime representation. Defaults to "(%a) %Y/%b/%d %H:%M:%S".

    Returns:
        str: formatted datetime.
    """
    if t != -1:
        datetime.fromtimestamp(t, tz).isoformat(timespec="seconds")
        return datetime.fromtimestamp(t, tz).strftime(fmt)
    return "Unknown Datetime"


def format_timestamp_iso(t: int | float, tz: Optional[tzinfo] = timezone.utc) -> str:
    """Format a UNIX timestamp into ISO 8601 representation like "2025-09-10T15:56:53+08:00".
    The "+00:00" suffix would be omitted if tz=None.

    Args:
        t (int | float): timestamp.
        tz (Optional[tzinfo], optional): timezone. Defaults to timezone.utc.

    Returns:
        str: iso-formatted datetime.
    """
    if t != -1:
        return datetime.fromtimestamp(t, tz).isoformat(timespec="seconds")
    return "Unknown Datetime"


def iso2timestamp(iso_str: str) -> tuple[int, int]:
    """Convert time from ISO 8601 representation like "2025-09-10T15:56:53+08:00" into UNIX timestamp.
    The "+00:00" suffix may be omitted.

    Args:
        iso_str (str): time in iso format.

    Returns:
        tuple[int, int]: UNIX timestamp and offset (in units of 15 min), (-1, 0) if input is invalid.
    """
    try:
        iso_str = iso_str.replace("24:00:00", "23:59:59").removesuffix("+")
        datetime_obj = datetime.fromisoformat(iso_str)
        return int(safe_timestamp(datetime_obj)), _store_tz_offset(datetime_obj.tzname())
    except Exception as e:
        store_logger.error(f"Graph Store: invalid iso -> timestamp conversion ({iso_str}): {e}")
        return -1, 0


def load_stored_time_from_db(timestamp: int | float, offset: int) -> Optional[datetime]:
    """Load stored timestamp and offset from database into datetime object.

    Args:
        timestamp (int | float): timestamp.
        offset (int): timezone offset, in units of 15 min.

    Returns:
        Optional[datetime]: datetime object if timestamp is non-negative.
    """
    if timestamp != -1:
        tz = _load_tz_offset(offset)
        return datetime.fromtimestamp(timestamp, tz)
    return None


def _store_tz_offset(tz_str: str) -> int:
    """Parse timezone string and return integer offset (in unit of 15 minutes)"""
    if tz_str and tz_str.removeprefix("UTC"):
        offsets = tz_str.removeprefix("UTC+").split(":")
        hr = mi = 0
        if offsets:
            hr = offsets[0]
            if len(offsets) > 1:
                mi = offsets[1]
        return int(hr) * 4 + int(mi) // 15
    return 0


def _load_tz_offset(tz_offset: int) -> timezone:
    """Load timezone offset integer from database and convert to timezone"""
    min_offset = tz_offset * 15
    return timezone(timedelta(minutes=min_offset))
