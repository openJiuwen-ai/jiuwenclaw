# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-member failed-correction tracking and team-collapse-safe dropout."""

from __future__ import annotations

from jiuwenswarm.agents.dropout.types import DropoutDecision


class MemberDropoutTracker:
    """Track consecutive failed corrections and decide when to drop a member."""

    def __init__(
        self,
        *,
        drop_after_failures: int = 2,
        min_active_members: int = 2,
    ) -> None:
        self.drop_after_failures = max(1, int(drop_after_failures))
        self.min_active_members = max(1, int(min_active_members))
        self._failures: dict[str, int] = {}
        self._dropped: set[str] = set()

    def reset(self) -> None:
        self._failures.clear()
        self._dropped.clear()

    def failure_count(self, member_name: str) -> int:
        return self._failures.get(member_name, 0)

    def is_dropped(self, member_name: str) -> bool:
        return member_name in self._dropped

    def record_pass(self, member_name: str) -> None:
        """Clear failure streak when a contribution passes audit."""
        self._failures.pop(member_name, None)

    def record_failure(
        self,
        member_name: str,
        *,
        active_members: int,
    ) -> DropoutDecision:
        """Increment failures and decide whether the member should be dropped."""
        if member_name in self._dropped:
            return DropoutDecision(
                should_drop=False,
                reason="member already dropped",
                failure_count=self._failures.get(member_name, 0),
                active_members=active_members,
                collapse_fallback=False,
            )

        count = self._failures.get(member_name, 0) + 1
        self._failures[member_name] = count

        if count < self.drop_after_failures:
            return DropoutDecision(
                should_drop=False,
                reason="below drop threshold",
                failure_count=count,
                active_members=active_members,
                collapse_fallback=False,
            )

        # Collapse fallback: never drop if remaining members would fall below floor.
        remaining = max(0, int(active_members) - 1)
        if remaining < self.min_active_members:
            return DropoutDecision(
                should_drop=False,
                reason=(
                    f"team-collapse fallback: remaining={remaining} "
                    f"< min_active_members={self.min_active_members}"
                ),
                failure_count=count,
                active_members=active_members,
                collapse_fallback=True,
            )

        self._dropped.add(member_name)
        return DropoutDecision(
            should_drop=True,
            reason=(
                f"exceeded failed-correction threshold "
                f"({count} >= {self.drop_after_failures})"
            ),
            failure_count=count,
            active_members=active_members,
            collapse_fallback=False,
        )

    def mark_dropped(self, member_name: str) -> None:
        self._dropped.add(member_name)

    def active_non_dropped(self, member_names: list[str]) -> list[str]:
        return [name for name in member_names if name not in self._dropped]
