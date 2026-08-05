# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for ``jiuwenswarm-start debug`` and ``jiuwenswarm-stop``."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jiuwenswarm import debug_launcher
from jiuwenswarm.debug_launcher import (
    DebugState,
    build_debug_log_path,
    clear_debug_state,
    get_debug_state_path,
    is_debug_service_alive,
    read_debug_state,
    run_debug,
    stop_debug_service,
    write_debug_state,
)
from jiuwenswarm.start_services import _parse_args, _validate_args


@pytest.fixture
def debug_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point REPO_ROOT at a scratch checkout with the layout debug mode needs."""
    frontend = tmp_path / "jiuwenswarm" / "channels" / "web" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    monkeypatch.setattr(debug_launcher, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(debug_launcher, "WEB_DEV_DIR", frontend)
    return tmp_path


def _state(pid: int = 4242, log_file: str = "/tmp/swarm.log") -> DebugState:
    """Build a DebugState with a zero timestamp (skips create_time matching)."""
    return DebugState(
        pid=pid,
        started_at=0.0,
        log_file=log_file,
        command=[sys.executable, "-m", "jiuwenswarm.start_services", "all"],
        cwd="/tmp",
    )


# --------------------------------------------------------------------------
# State file round-trip
# --------------------------------------------------------------------------


def test_state_roundtrip(debug_root: Path):
    state = _state(pid=1234)
    write_debug_state(state)

    assert get_debug_state_path() == debug_root / "logs" / "debug_service.json"
    loaded = read_debug_state()
    assert loaded is not None
    assert loaded.pid == 1234
    assert loaded.log_file == state.log_file
    assert loaded.command == state.command


def test_read_debug_state_missing(debug_root: Path):
    assert read_debug_state() is None


def test_read_debug_state_corrupt(debug_root: Path):
    path = get_debug_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    assert read_debug_state() is None


def test_read_debug_state_rejects_bad_pid(debug_root: Path):
    path = get_debug_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 0, "log_file": "x"}), encoding="utf-8")
    assert read_debug_state() is None


def test_clear_debug_state(debug_root: Path):
    write_debug_state(_state())
    assert clear_debug_state() is True
    assert clear_debug_state() is False


# --------------------------------------------------------------------------
# Log file naming
# --------------------------------------------------------------------------


def test_build_debug_log_path_timestamp_suffix(debug_root: Path):
    now = datetime(2025, 8, 5, 14, 30, 0)
    path = build_debug_log_path(now)
    assert path.name == "swarm-20250805-143000.log"
    assert path.parent == debug_root / "logs"


def test_build_debug_log_path_avoids_collision(debug_root: Path):
    now = datetime(2025, 8, 5, 14, 30, 0)
    log_dir = debug_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "swarm-20250805-143000.log").write_text("", encoding="utf-8")

    path = build_debug_log_path(now)
    assert path.name == "swarm-20250805-143000-2.log"


# --------------------------------------------------------------------------
# PID-reuse guard
# --------------------------------------------------------------------------


def test_is_debug_service_alive_false_when_dead(debug_root: Path):
    with patch.object(debug_launcher, "is_process_alive", return_value=False):
        assert is_debug_service_alive(_state()) is False


def test_is_debug_service_alive_rejects_recycled_pid(debug_root: Path):
    state = DebugState(
        pid=4242,
        started_at=1_000_000.0,
        log_file="/tmp/swarm.log",
        command=["python"],
        cwd="/tmp",
    )
    fake_proc = MagicMock()
    # Unrelated process created long after our recorded spawn time.
    fake_proc.create_time.return_value = 1_500_000.0
    fake_psutil = MagicMock()
    fake_psutil.Process.return_value = fake_proc

    with patch.object(debug_launcher, "is_process_alive", return_value=True):
        with patch.dict(sys.modules, {"psutil": fake_psutil}):
            assert is_debug_service_alive(state) is False


def test_is_debug_service_alive_accepts_matching_create_time(debug_root: Path):
    state = DebugState(
        pid=4242,
        started_at=1_000_000.0,
        log_file="/tmp/swarm.log",
        command=["python"],
        cwd="/tmp",
    )
    fake_proc = MagicMock()
    fake_proc.create_time.return_value = 1_000_000.4
    fake_psutil = MagicMock()
    fake_psutil.Process.return_value = fake_proc

    with patch.object(debug_launcher, "is_process_alive", return_value=True):
        with patch.dict(sys.modules, {"psutil": fake_psutil}):
            assert is_debug_service_alive(state) is True


# --------------------------------------------------------------------------
# run_debug pipeline
# --------------------------------------------------------------------------


def _patch_pipeline(*, run_codes: list[int], spawn_pid: int | None = 9999):
    """Patch every external effect of run_debug and record the executed steps.

    Args:
        run_codes: Exit codes returned by consecutive subprocess.run calls.
        spawn_pid: PID of the fake background process, None to fail the spawn.

    Returns:
        Tuple of (context-manager list, recorded-calls list).
    """
    calls: list[tuple[list[str], str]] = []
    codes = iter(run_codes)

    def _fake_run(cmd, cwd=None, check=False):
        calls.append((list(cmd), str(cwd)))
        result = MagicMock()
        result.returncode = next(codes)
        return result

    def _fake_popen(cmd, **kwargs):
        calls.append((list(cmd), str(kwargs.get("cwd"))))
        if spawn_pid is None:
            raise OSError("spawn failed")
        proc = MagicMock()
        proc.pid = spawn_pid
        return proc

    patches = [
        patch.object(debug_launcher.subprocess, "run", side_effect=_fake_run),
        patch.object(debug_launcher.subprocess, "Popen", side_effect=_fake_popen),
        patch.object(debug_launcher.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"),
        patch.object(debug_launcher.platform, "system", return_value="Linux"),
        patch.object(debug_launcher, "is_process_alive", return_value=True),
        patch.object(debug_launcher.time, "sleep", return_value=None),
    ]
    return patches, calls


def _enter(patches):
    """Enter every patch and return them for teardown."""
    for item in patches:
        item.start()
    return patches


def _exit(patches):
    """Stop every patch."""
    for item in patches:
        item.stop()


def test_run_debug_happy_path(debug_root: Path):
    patches, calls = _patch_pipeline(run_codes=[0, 0, 0])
    _enter(patches)
    try:
        assert run_debug() == 0
    finally:
        _exit(patches)

    executed = [cmd for cmd, _cwd in calls]
    assert executed[0] == ["/usr/bin/npm", "install"]
    assert executed[1] == ["/usr/bin/npm", "run", "build"]
    assert executed[2] == ["/usr/bin/uv", "sync"]
    assert executed[3] == [sys.executable, "-m", "jiuwenswarm.start_services", "all"]

    # npm steps run inside the frontend project, uv sync at the repo root.
    assert calls[0][1] == str(debug_launcher.WEB_DEV_DIR)
    assert calls[1][1] == str(debug_launcher.WEB_DEV_DIR)
    assert calls[2][1] == str(debug_root)

    state = read_debug_state()
    assert state is not None
    assert state.pid == 9999
    log_path = Path(state.log_file)
    assert log_path.exists()
    assert log_path.name.startswith("swarm-")
    assert log_path.suffix == ".log"


def test_run_debug_stops_when_npm_install_fails(debug_root: Path):
    patches, calls = _patch_pipeline(run_codes=[1])
    _enter(patches)
    try:
        assert run_debug() == 1
    finally:
        _exit(patches)

    assert len(calls) == 1
    assert read_debug_state() is None


def test_run_debug_stops_when_build_fails(debug_root: Path):
    patches, calls = _patch_pipeline(run_codes=[0, 2])
    _enter(patches)
    try:
        assert run_debug() == 2
    finally:
        _exit(patches)

    assert len(calls) == 2
    assert read_debug_state() is None


def test_run_debug_stops_when_uv_sync_fails(debug_root: Path):
    patches, calls = _patch_pipeline(run_codes=[0, 0, 3])
    _enter(patches)
    try:
        assert run_debug() == 3
    finally:
        _exit(patches)

    assert len(calls) == 3
    assert read_debug_state() is None


def test_run_debug_reports_immediate_child_exit(debug_root: Path):
    patches, _calls = _patch_pipeline(run_codes=[0, 0, 0])
    _enter(patches)
    try:
        with patch.object(debug_launcher, "is_process_alive", return_value=False):
            with patch.object(
                debug_launcher, "_stop_process_tree", return_value=True
            ) as sweeper:
                assert run_debug() == 1
    finally:
        _exit(patches)

    # A dead child must not leave a state file behind for jiuwenswarm-stop,
    # and its half-started sub-services must not be left orphaned either.
    assert read_debug_state() is None
    sweeper.assert_called_once()


def test_run_debug_requires_source_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(debug_launcher, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(debug_launcher, "WEB_DEV_DIR", tmp_path / "missing")
    assert run_debug() == 1


def test_run_debug_requires_npm(debug_root: Path):
    with patch.object(debug_launcher.shutil, "which", return_value=None):
        with patch.object(debug_launcher.platform, "system", return_value="Linux"):
            assert run_debug() == 1


def test_run_debug_requires_uv(debug_root: Path):
    def _which(name: str) -> str | None:
        return None if name == "uv" else f"/usr/bin/{name}"

    patches, calls = _patch_pipeline(run_codes=[0, 0])
    # Override the which stub so only uv is missing.
    patches[2] = patch.object(debug_launcher.shutil, "which", side_effect=_which)
    _enter(patches)
    try:
        assert run_debug() == 1
    finally:
        _exit(patches)

    assert len(calls) == 2
    assert read_debug_state() is None


def test_run_debug_refuses_when_already_running(debug_root: Path):
    write_debug_state(_state(pid=777))
    with patch.object(debug_launcher, "is_process_alive", return_value=True):
        assert run_debug() == 1
    # The live record must survive the refusal.
    assert read_debug_state().pid == 777


def test_run_debug_clears_stale_state(debug_root: Path):
    write_debug_state(_state(pid=777))
    patches, _calls = _patch_pipeline(run_codes=[0, 0, 0])
    # Stale check needs a dead PID, the post-spawn check needs a live one.
    alive = iter([False, True])
    patches[4] = patch.object(
        debug_launcher, "is_process_alive", side_effect=lambda _pid: next(alive)
    )
    _enter(patches)
    try:
        assert run_debug() == 0
    finally:
        _exit(patches)

    assert read_debug_state().pid == 9999


# --------------------------------------------------------------------------
# stop_debug_service
# --------------------------------------------------------------------------


def test_stop_without_state_is_noop(debug_root: Path):
    assert stop_debug_service() == 0


def test_stop_terminates_recorded_process(debug_root: Path):
    write_debug_state(_state(pid=5555))
    alive = iter([True, False])
    with patch.object(debug_launcher, "is_process_alive", side_effect=lambda _pid: next(alive)):
        with patch.object(debug_launcher, "_stop_process_tree", return_value=True) as stopper:
            assert stop_debug_service(timeout=1.0) == 0

    stopper.assert_called_once_with(5555, timeout=1.0)
    assert read_debug_state() is None


def test_stop_clears_state_when_already_dead(debug_root: Path):
    write_debug_state(_state(pid=5555))
    with patch.object(debug_launcher, "is_process_alive", return_value=False):
        with patch.object(debug_launcher, "_stop_process_tree") as stopper:
            assert stop_debug_service() == 0

    stopper.assert_not_called()
    assert read_debug_state() is None


def test_stop_keeps_state_when_process_survives(debug_root: Path):
    write_debug_state(_state(pid=5555))
    with patch.object(debug_launcher, "is_process_alive", return_value=True):
        with patch.object(debug_launcher, "_stop_process_tree", return_value=False):
            assert stop_debug_service() == 1

    assert read_debug_state().pid == 5555


# --------------------------------------------------------------------------
# Process-group teardown
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
def test_stop_process_tree_signals_whole_group():
    """The group, not just the leader, must receive SIGTERM."""
    import signal

    signalled: list[tuple[int, int]] = []
    # killpg(pgid, 0) probes: alive once, then gone.
    probes = iter([True, False])

    def _fake_killpg(pgid: int, sig: int) -> None:
        if sig == 0:
            if not next(probes):
                raise ProcessLookupError()
            return
        signalled.append((pgid, sig))

    with patch.object(debug_launcher.platform, "system", return_value="Linux"):
        with patch.object(debug_launcher.os, "getpgid", return_value=7000):
            with patch.object(debug_launcher.os, "killpg", side_effect=_fake_killpg):
                with patch.object(debug_launcher.time, "sleep", return_value=None):
                    assert debug_launcher._stop_process_tree(7000, timeout=1.0) is True

    assert signalled == [(7000, signal.SIGTERM)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
def test_stop_process_tree_escalates_to_sigkill():
    """A group that ignores SIGTERM gets SIGKILL before we report success."""
    import signal

    signalled: list[tuple[int, int]] = []
    # Stays alive through the SIGTERM window, dies after SIGKILL.
    state = {"killed": False}

    def _fake_killpg(pgid: int, sig: int) -> None:
        if sig == 0:
            if state["killed"]:
                raise ProcessLookupError()
            return
        signalled.append((pgid, sig))
        if sig == signal.SIGKILL:
            state["killed"] = True

    with patch.object(debug_launcher.platform, "system", return_value="Linux"):
        with patch.object(debug_launcher.os, "getpgid", return_value=7000):
            with patch.object(debug_launcher.os, "killpg", side_effect=_fake_killpg):
                with patch.object(debug_launcher.time, "sleep", return_value=None):
                    assert debug_launcher._stop_process_tree(7000, timeout=0.5) is True

    assert signalled == [(7000, signal.SIGTERM), (7000, signal.SIGKILL)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
def test_stop_process_tree_falls_back_when_not_group_leader():
    """A PID that does not lead its own group must not have the group killed."""
    with patch.object(debug_launcher.platform, "system", return_value="Linux"):
        with patch.object(debug_launcher.os, "getpgid", return_value=1):
            with patch.object(debug_launcher.os, "killpg") as killpg:
                with patch.object(
                    debug_launcher, "stop_process_by_pid", return_value=True
                ) as single:
                    assert debug_launcher._stop_process_tree(7000, timeout=1.0) is True

    killpg.assert_not_called()
    single.assert_called_once_with(7000, timeout=1.0)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
def test_stop_process_tree_handles_already_gone():
    with patch.object(debug_launcher.platform, "system", return_value="Linux"):
        with patch.object(debug_launcher.os, "getpgid", side_effect=ProcessLookupError()):
            assert debug_launcher._stop_process_tree(7000, timeout=1.0) is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
def test_stop_process_tree_sweeps_group_of_dead_leader():
    """A dead leader still gets its group swept, so children are not orphaned."""
    import signal

    signalled: list[tuple[int, int]] = []
    probes = iter([True, False])

    def _fake_killpg(pgid: int, sig: int) -> None:
        if sig == 0:
            if not next(probes):
                raise ProcessLookupError()
            return
        signalled.append((pgid, sig))

    with patch.object(debug_launcher.platform, "system", return_value="Linux"):
        # getpgid on a dead PID would raise; assume_group_leader must skip it.
        with patch.object(debug_launcher.os, "getpgid", side_effect=ProcessLookupError()):
            with patch.object(debug_launcher.os, "killpg", side_effect=_fake_killpg):
                with patch.object(debug_launcher.time, "sleep", return_value=None):
                    assert (
                        debug_launcher._stop_process_tree(
                            7000, timeout=1.0, assume_group_leader=True
                        )
                        is True
                    )

    assert signalled == [(7000, signal.SIGTERM)]


def test_stop_process_tree_uses_taskkill_on_windows():
    """Windows has no process groups here; taskkill /T already walks the tree."""
    with patch.object(debug_launcher.platform, "system", return_value="Windows"):
        with patch.object(
            debug_launcher, "stop_process_by_pid", return_value=True
        ) as single:
            assert debug_launcher._stop_process_tree(7000, timeout=2.0) is True

    single.assert_called_once_with(7000, timeout=2.0)


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


def test_debug_is_a_valid_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["jiuwenswarm-start", "debug"])
    args = _parse_args()
    assert args.mode == "debug"
    assert _validate_args(args) is None


def test_debug_rejects_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["jiuwenswarm-start", "debug", "--name", "dev"])
    args = _parse_args()
    assert _validate_args(args) == 1


def test_dispatch_routes_debug_mode(monkeypatch: pytest.MonkeyPatch):
    from jiuwenswarm.start_services import _dispatch_action

    monkeypatch.setattr(sys, "argv", ["jiuwenswarm-start", "debug"])
    args = _parse_args()
    with patch.object(debug_launcher, "run_debug", return_value=0) as runner:
        assert _dispatch_action(args) == 0
    runner.assert_called_once_with()


def test_stop_arg_parsing_default_timeout():
    from jiuwenswarm.debug_launcher import DEFAULT_STOP_TIMEOUT, _parse_stop_args

    assert _parse_stop_args([]) == DEFAULT_STOP_TIMEOUT
    assert _parse_stop_args(["--timeout", "3.5"]) == 3.5
