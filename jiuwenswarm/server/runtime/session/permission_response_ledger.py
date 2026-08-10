# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-local deduplication for permission continuation responses."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


_MAX_RECENT_KEYS = 1024
_PermissionResponseKey = tuple[str, str]


@dataclass(eq=False)
class PermissionResponseReservation:
    """A single permission response's right to enter the runtime."""

    _ledger: PermissionResponseLedger
    _key: _PermissionResponseKey
    _started: bool = False

    def start(self) -> bool:
        """Claim runtime entry if this reservation is still current."""
        return self._ledger._start(self)

    def complete(self) -> None:
        """Remember a response after it has entered the runtime."""
        self._ledger._complete(self)

    def release_if_unstarted(self) -> None:
        """Release a queued reservation so a retry can replace it."""
        self._ledger._release_if_unstarted(self)


class PermissionResponseLedger:
    """Track active and recently executed permission responses."""

    def __init__(self, *, max_recent_keys: int = _MAX_RECENT_KEYS) -> None:
        if max_recent_keys < 0:
            raise ValueError("max_recent_keys must be non-negative")
        self._max_recent_keys = max_recent_keys
        self._active: dict[
            _PermissionResponseKey, PermissionResponseReservation
        ] = {}
        self._recent: OrderedDict[_PermissionResponseKey, None] = OrderedDict()

    def reserve(
        self,
        session_id: str,
        response_id: str,
    ) -> PermissionResponseReservation | None:
        """Reserve an opaque response ID once for a session."""
        key = (session_id, response_id)
        if key in self._active or key in self._recent:
            return None
        reservation = PermissionResponseReservation(self, key)
        self._active[key] = reservation
        return reservation

    def _start(self, reservation: PermissionResponseReservation) -> bool:
        if (
            self._active.get(reservation._key) is not reservation
            or reservation._started
        ):
            return False
        reservation._started = True
        return True

    def _complete(self, reservation: PermissionResponseReservation) -> None:
        if self._active.get(reservation._key) is not reservation:
            return
        self._active.pop(reservation._key, None)
        if not reservation._started or self._max_recent_keys == 0:
            return
        self._recent[reservation._key] = None
        while len(self._recent) > self._max_recent_keys:
            self._recent.popitem(last=False)

    def _release_if_unstarted(
        self,
        reservation: PermissionResponseReservation,
    ) -> None:
        if (
            not reservation._started
            and self._active.get(reservation._key) is reservation
        ):
            self._active.pop(reservation._key, None)
