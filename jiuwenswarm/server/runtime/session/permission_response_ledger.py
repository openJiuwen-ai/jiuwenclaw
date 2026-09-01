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

    @property
    def key(self) -> _PermissionResponseKey:
        """Return the session-scoped response key."""
        return self._key

    @property
    def started(self) -> bool:
        """Return whether this reservation has entered the runtime."""
        return self._started

    def start(self) -> bool:
        """Claim runtime entry if this reservation is still current."""
        if self._started or not self._ledger.is_current_reservation(self):
            return False
        self._started = True
        return True

    def complete(self) -> None:
        """Remember a response after it has entered the runtime."""
        self._ledger.complete_reservation(self)

    def release_if_unstarted(self) -> None:
        """Release a queued reservation so a retry can replace it."""
        self._ledger.release_if_unstarted(self)


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

    def is_current_reservation(
        self,
        reservation: PermissionResponseReservation,
    ) -> bool:
        """Return whether a reservation is still the current active entry."""
        return self._active.get(reservation.key) is reservation

    def complete_reservation(
        self,
        reservation: PermissionResponseReservation,
    ) -> None:
        """Complete a reservation and remember its key if it was started."""
        if self._active.get(reservation.key) is not reservation:
            return
        self._active.pop(reservation.key, None)
        if not reservation.started or self._max_recent_keys == 0:
            return
        self._recent[reservation.key] = None
        while len(self._recent) > self._max_recent_keys:
            self._recent.popitem(last=False)

    def release_if_unstarted(
        self,
        reservation: PermissionResponseReservation,
    ) -> None:
        """Release a reservation only if it has not started."""
        if (
            not reservation.started
            and self._active.get(reservation.key) is reservation
        ):
            self._active.pop(reservation.key, None)
