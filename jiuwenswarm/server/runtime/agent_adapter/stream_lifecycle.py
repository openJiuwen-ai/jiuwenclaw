"""Structured ownership helpers for nested async iterators."""

from __future__ import annotations

import asyncio
from typing import Any


async def close_owned_async_iterator(iterator: Any) -> None:
    """Close one iterator from its driving task despite repeated cancellation.

    The caller remains the sole owner of ``aclose``.  A cancellation delivered
    while the close is running is remembered, consumed long enough to finish
    closing, and then re-raised.  Non-cancellation cleanup failures propagate.
    """

    close = getattr(iterator, "aclose", None)
    if not callable(close):
        return

    cancellation: asyncio.CancelledError | None = None
    current = asyncio.current_task()
    while True:
        try:
            await close()
            break
        except asyncio.CancelledError as exc:
            if current is None or current.cancelling() == 0:
                raise
            cancellation = cancellation or exc
            while current.cancelling():
                current.uncancel()

    if cancellation is not None:
        raise cancellation
