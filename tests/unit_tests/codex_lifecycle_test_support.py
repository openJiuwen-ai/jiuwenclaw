"""Shared assertions for PID-1-dependent Codex lifecycle tests."""

from __future__ import annotations

import json
import os

import pytest

from jiuwenswarm.integrations.ai4research_subscription.errors import (
    CodexProviderError,
)
from jiuwenswarm.integrations.ai4research_subscription.locking import (
    acquire_profile_lock,
    release_profile_lock,
)
from jiuwenswarm.integrations.ai4research_subscription.process_lifecycle import (
    process_group_snapshot,
)
from jiuwenswarm.integrations.ai4research_subscription.profiles import CodexProfile
from jiuwenswarm.integrations.ai4research_subscription import quarantine
from jiuwenswarm.integrations.ai4research_subscription.quarantine import profile_is_quarantined


def assert_zombie_only_quarantine(
    profile: CodexProfile,
    *,
    pgid: int,
    expected_pids: list[int],
    expect_lock_held: bool = True,
) -> None:
    """Prove cleanup killed every live process and failed closed on zombies."""

    assert profile_is_quarantined(profile)
    marker = json.loads(profile.quarantine_path.read_text(encoding="utf-8"))
    assert marker["pgid"] == pgid

    members = process_group_snapshot(pgid)
    assert members, "the quarantine branch requires an unreaped process-group member"
    assert all(member["state"] == "Z" for member in members)
    assert {int(member["pid"]) for member in members} <= set(expected_pids)

    recorded = {(int(pid), int(start_ticks)) for pid, start_ticks in marker["members"]}
    current = {
        (int(member["pid"]), int(member["start_ticks"])) for member in members
    }
    assert current <= recorded

    if expect_lock_held:
        with pytest.raises(CodexProviderError) as captured:
            acquire_profile_lock(profile)
        assert captured.value.code == "provider_busy"
    else:
        handle = acquire_profile_lock(profile)
        release_profile_lock(handle)


def discard_zombie_only_test_quarantine(profile: CodexProfile) -> None:
    """White-box cleanup for one exact zombie-only pytest quarantine record."""

    assert os.environ.get("PYTEST_CURRENT_TEST"), "test-only cleanup called outside pytest"
    key = profile.root.absolute()
    record = quarantine._QUARANTINES.get(key)
    assert record is not None, "the passed test profile has no retained quarantine owner"
    assert record.profile == profile
    assert profile.quarantine_path.parent == profile.root.parent

    marker = quarantine._read_marker(profile)
    assert marker is not None
    boot_id, pgid, identities, turn_name = quarantine._marker_identity(marker)
    assert boot_id == record.boot_id
    assert pgid == record.pgid
    assert identities == set(record.members)
    expected_turn_name = record.turn_dir.name if record.turn_dir is not None else None
    assert turn_name == expected_turn_name

    members = process_group_snapshot(pgid)
    assert members, "test cleanup requires a nonempty zombie-only process group"
    assert all(member["state"] == "Z" for member in members)
    current = {
        (int(member["pid"]), int(member["start_ticks"])) for member in members
    }
    assert current <= identities

    if record.lock_handle is not None:
        release_profile_lock(record.lock_handle)
        record.lock_handle = None
    quarantine._remove_marker(profile)
    removed = quarantine._QUARANTINES.pop(key)
    assert removed is record
