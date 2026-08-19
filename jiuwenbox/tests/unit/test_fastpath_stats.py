# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for FastPath stats accounting and throttled flush.

Covers the observability contract:

* **Counting invariant** ``requests == hits + fallbacks``. ``record_request``
  is taken at the *entry* of ``_try_fastpath_exec`` (so ``not_eligible`` is
  counted), and ``record_hit`` is only taken on a normal ``exit_code``
  response (so a ``worker_error`` response is a fallback, never also a hit).
* **``not_eligible`` is visible**: the path triggers a throttled
  ``write_snapshot`` (previously it never wrote).
* **throttle / dirty / flush**: a write skipped inside the throttle window
  sets ``_dirty``; ``flush`` (idle-reaper) force-writes the accumulated
  counters, so a one-off fallback followed by silence is not lost.

The ``_try_fastpath_exec`` routing tests stub ``_fastpath_plan`` and
``_FORK_POOL.submit`` -- no real ForkServer fork is needed, so they run on
every platform.
"""

# 豁免 G.CLS.11 protected-access：本文件为 FastPath stats 计数的白盒单测，
# 需直接访问 sd._FORK_POOL / stats._last_write / stats._dirty 等受保护成员；
# 重命名为 public 会破坏封装语义，故仅在本测试侧豁免（不改产品符号可见性）。
# pylint: disable=protected-access

from __future__ import annotations

import json
import socket
import threading
import time
import types

import pytest

from jiuwenbox.supervisor import sandbox_daemon as sd
from jiuwenbox.supervisor.sandbox_daemon import (
    FastPathExecUncertain,
    FastPathUnavailable,
    FastPathStats,
    _FastPathRequest,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# FastPathStats: counter invariant and reasons
# --------------------------------------------------------------------------- #
def test_invariant_hits_plus_fallbacks_equals_requests():
    stats = FastPathStats()
    # Simulate the six terminal paths of _try_fastpath_exec, each starting
    # with record_request (taken at _try_fastpath_exec entry).
    paths = [
        "not_eligible",
        "breaker_open",
        "worker_unavailable",
        "spawn_failed",
        "worker_error",
    ]
    for reason in paths:
        stats.record_request()
        stats.record_fallback(reason)
    # Two hits on top.
    for _ in range(2):
        stats.record_request()
        stats.record_hit()

    snap = stats.snapshot()
    assert snap["requests"] == 7
    assert snap["hits"] == 2
    assert snap["fallbacks"] == 5
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]
    # Every reason counted exactly once; no double-counted "hit" reason.
    assert snap["fallback_reasons"] == {r: 1 for r in paths}


def test_record_fallback_accumulates_reasons():
    stats = FastPathStats()
    stats.record_request()
    stats.record_fallback("not_eligible")
    stats.record_request()
    stats.record_fallback("not_eligible")
    stats.record_request()
    stats.record_fallback("breaker_open")
    snap = stats.snapshot()
    assert snap["fallback_reasons"] == {"not_eligible": 2, "breaker_open": 1}
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]


# --------------------------------------------------------------------------- #
# _try_fastpath_exec routing: each terminal path lands in exactly one bucket
# --------------------------------------------------------------------------- #
@pytest.fixture
def isolated_stats(monkeypatch):
    """Give the module pool a fresh stats instance pointed at a tmp file."""
    stats = FastPathStats()
    monkeypatch.setattr(sd._FORK_POOL, "stats", stats)
    return stats


@pytest.fixture
def drain_conn():
    """A socketpair: daemon writes into ``srv``, test drains from ``cli``."""
    srv, cli = socket.socketpair()
    try:
        yield srv, cli
    finally:
        srv.close()
        cli.close()


def _drain(cli: socket.socket) -> dict:
    """Read one framed JSON response the daemon wrote to the peer socket."""
    cli.settimeout(2.0)
    hdr = b""
    while len(hdr) < 4:
        chunk = cli.recv(4 - len(hdr))
        if not chunk:
            break
        hdr += chunk
    (size,) = sd.struct.unpack(">I", hdr)
    body = b""
    while len(body) < size:
        chunk = cli.recv(size - len(body))
        if not chunk:
            break
        body += chunk
    return json.loads(body.decode("utf-8"))


def test_route_not_eligible_counts_request_and_writes(isolated_stats, monkeypatch, drain_conn):
    # plan None -> not_eligible fallback, return False, no submit call.
    monkeypatch.setattr(sd, "_fastpath_plan", lambda cmd, hdr: None)
    called = {"submit": False}
    monkeypatch.setattr(sd._FORK_POOL, "submit", lambda *a, **k: called.__setitem__("submit", True))

    srv, _cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-I", "-c", "x"], {}, b"")

    assert rc is False
    assert called["submit"] is False
    snap = isolated_stats.snapshot()
    assert snap["requests"] == 1
    assert snap["hits"] == 0
    assert snap["fallbacks"] == 1
    assert snap["fallback_reasons"] == {"not_eligible": 1}
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]


def test_route_hit_counts_hit_not_fallback(isolated_stats, monkeypatch, drain_conn):
    monkeypatch.setattr(
        sd, "_fastpath_plan", lambda cmd, hdr: {"mode": "code", "code": "pass"}
    )
    monkeypatch.setattr(
        sd._FORK_POOL, "submit", lambda *a, **k: {"exit_code": 0, "stdout": "", "stderr": ""}
    )

    srv, cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-c", "pass"], {}, b"")
    resp = _drain(cli)

    assert rc is True
    assert resp["exit_code"] == 0
    snap = isolated_stats.snapshot()
    assert snap["requests"] == 1
    assert snap["hits"] == 1
    assert snap["fallbacks"] == 0
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]


def test_route_worker_error_is_fallback_not_hit(isolated_stats, monkeypatch, drain_conn):
    # A worker that responded with an internal error: fastpath attempted and
    # failed -> fallback("worker_error"), NOT a hit. Previously this also
    # counted a hit, breaking the invariant.
    monkeypatch.setattr(
        sd, "_fastpath_plan", lambda cmd, hdr: {"mode": "code", "code": "x"}
    )
    monkeypatch.setattr(
        sd._FORK_POOL, "submit", lambda *a, **k: {"error": "worker error: boom"}
    )

    srv, cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-c", "x"], {}, b"")
    resp = _drain(cli)

    assert rc is True
    assert resp["exit_code"] == 1
    assert resp["error"] == "fastpath_internal"
    snap = isolated_stats.snapshot()
    assert snap["requests"] == 1
    assert snap["hits"] == 0          # the fix: not double-counted
    assert snap["fallbacks"] == 1
    assert snap["fallback_reasons"] == {"worker_error": 1}
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]


@pytest.mark.parametrize(
    "reason",
    ["breaker_open", "worker_unavailable", "spawn_failed"],
)
def test_route_submit_raises_counts_fallback(isolated_stats, monkeypatch, drain_conn, reason):
    monkeypatch.setattr(
        sd, "_fastpath_plan", lambda cmd, hdr: {"mode": "code", "code": "x"}
    )

    def _raise(*a, **k):
        raise FastPathUnavailable(reason)

    monkeypatch.setattr(sd._FORK_POOL, "submit", _raise)

    srv, _cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-c", "x"], {}, b"")

    assert rc is False
    snap = isolated_stats.snapshot()
    assert snap["requests"] == 1
    assert snap["hits"] == 0
    assert snap["fallbacks"] == 1
    assert snap["fallback_reasons"] == {reason: 1}


# --------------------------------------------------------------------------- #
# post-dispatch no-replay boundary
# --------------------------------------------------------------------------- #
def _fake_live_worker():
    """A live, idle worker triple stub: (proc, sock, wlock) all fake.

    ``poll`` returns ``None`` (alive); the lock is unheld so the round-robin
    acquires it immediately and ``chosen`` is set. ``sock``/``proc`` accept the
    methods ``submit`` calls in its round-trip (settimeout/close, kill). No
    fork, so this runs on every platform including under pytest capture.
    """
    lock = threading.Lock()
    proc = types.SimpleNamespace(poll=lambda: None, kill=lambda: None)
    sock = types.SimpleNamespace(
        settimeout=lambda _t: None, close=lambda: None)
    return (proc, sock, lock)


def test_post_dispatch_failure_raises_exec_uncertain(monkeypatch):
    """A failure AFTER ``_send_frame`` raises ``FastPathExecUncertain`` (no replay).

    Stubs the pool with one fake live worker, then makes ``_send_frame`` raise.
    Because dispatch begins at ``_send_frame``, this is a post-dispatch
    failure: ``submit`` must raise ``FastPathExecUncertain`` (NOT
    ``FastPathUnavailable``), so the caller cannot fall back to Popen. The
    worker is still dropped and the breaker bumped (a real worker failure).
    """
    pool = sd._FORK_POOL
    monkeypatch.setattr(pool, "_workers", [_fake_live_worker()])
    monkeypatch.setattr(pool, "_ensure", lambda: True)
    monkeypatch.setattr(pool, "_next", 0)
    monkeypatch.setattr(pool, "_breaker_state", "closed")
    monkeypatch.setattr(pool, "_breaker_failures", 0)
    drops = {"n": 0}
    monkeypatch.setattr(pool, "_drop", lambda _p: drops.__setitem__("n", drops["n"] + 1))
    bumps = {"n": 0}
    monkeypatch.setattr(pool, "_bump_failure_locked",
                        lambda: bumps.__setitem__("n", bumps["n"] + 1))

    def _boom(_sock, _payload):
        raise OSError("send failed mid-frame")
    monkeypatch.setattr(sd, "_send_frame", _boom)

    with pytest.raises(FastPathExecUncertain) as ei:
        pool.submit(_FastPathRequest(code="pass", stdin_bytes=b"", workdir=None,
                                     env_overrides=None, timeout=1.0, plan=None))
    assert ei.value.reason == "post_dispatch_failure"
    # It must NOT be a FastPathUnavailable (the safe-fallback type).
    assert not isinstance(ei.value, FastPathUnavailable)
    # Real worker failure: worker dropped and breaker bumped.
    assert drops["n"] == 1
    assert bumps["n"] == 1


def test_pre_dispatch_failure_still_safe_unavailable(monkeypatch):
    """A failure BEFORE ``_send_frame`` stays ``FastPathUnavailable`` (safe fallback).

    ``sock.settimeout`` raising simulates a pre-dispatch failure (the header
    frame was never sent, so the worker cannot have executed). ``submit`` must
    raise ``FastPathUnavailable`` so the daemon can safely Popen. This guards
    the boundary: only post-``_send_frame`` failures are no-replay.
    """
    pool = sd._FORK_POOL
    proc, sock, lock = _fake_live_worker()
    monkeypatch.setattr(pool, "_workers", [(proc, sock, lock)])
    monkeypatch.setattr(pool, "_ensure", lambda: True)
    monkeypatch.setattr(pool, "_next", 0)
    monkeypatch.setattr(pool, "_breaker_state", "closed")
    monkeypatch.setattr(pool, "_breaker_failures", 0)
    monkeypatch.setattr(pool, "_drop", lambda _p: None)
    monkeypatch.setattr(pool, "_bump_failure_locked", lambda: None)
    # settimeout raises before _send_frame -> pre-dispatch.
    sock.settimeout = lambda _t: (_ for _ in ()).throw(OSError("settimeout failed"))

    with pytest.raises(FastPathUnavailable) as ei:
        pool.submit(_FastPathRequest(code="pass", stdin_bytes=b"", workdir=None,
                                     env_overrides=None, timeout=1.0, plan=None))
    assert ei.value.reason == "worker_unavailable"
    # Must NOT be the no-replay type.
    assert not isinstance(ei.value, FastPathExecUncertain)


def test_exec_uncertain_no_popen_sends_error_response(monkeypatch, drain_conn):
    """``_try_fastpath_exec`` on ``FastPathExecUncertain`` sends an error, no Popen.

    The post-dispatch path must NOT return ``False`` (that would make the
    daemon Popen / replay). It sends an ``fastpath_exec_uncertain`` response
    and returns ``True`` (a response was sent). The request is bucketed as
    ``exec_uncertain`` and the counting invariant stays closed.
    """
    stats = FastPathStats()
    monkeypatch.setattr(sd._FORK_POOL, "stats", stats)
    monkeypatch.setattr(sd, "_fastpath_plan",
                        lambda cmd, hdr: {"mode": "code", "code": "x"})

    def _raise(*a, **k):
        raise FastPathExecUncertain("post_dispatch_failure")
    monkeypatch.setattr(sd._FORK_POOL, "submit", _raise)

    srv, cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-c", "x"], {}, b"")
    resp = _drain(cli)

    assert rc is True            # response sent; daemon must NOT Popen
    assert resp["error"] == "fastpath_exec_uncertain"
    assert resp["exit_code"] == 1
    snap = stats.snapshot()
    assert snap["requests"] == 1
    assert snap["hits"] == 0
    assert snap["fallbacks"] == 1
    assert snap["fallback_reasons"] == {"exec_uncertain": 1}
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]


def test_exec_uncertain_not_caught_as_unavailable(monkeypatch, drain_conn):
    """``except FastPathUnavailable`` must not swallow ``FastPathExecUncertain``.

    The two types are siblings (both subclass ``RuntimeError`` directly);
    a handler for one cannot catch the other. If they were parent/child a
    no-replay condition could be silently converted back into a Popen fallback.
    """
    assert not issubclass(FastPathExecUncertain, FastPathUnavailable)
    assert not issubclass(FastPathUnavailable, FastPathExecUncertain)

    # And the routing proves it end-to-end: submit raising exec_uncertain lands
    # in the exec_uncertain bucket, not worker_unavailable.
    stats = FastPathStats()
    monkeypatch.setattr(sd._FORK_POOL, "stats", stats)
    monkeypatch.setattr(sd, "_fastpath_plan",
                        lambda cmd, hdr: {"mode": "code", "code": "x"})
    monkeypatch.setattr(
        sd._FORK_POOL, "submit",
        lambda *a, **k: (_ for _ in ()).throw(FastPathExecUncertain("post_dispatch_failure")))

    srv, _cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-c", "x"], {}, b"")
    assert rc is True
    assert stats.snapshot()["fallback_reasons"] == {"exec_uncertain": 1}


def test_mixed_traffic_invariant_closed(isolated_stats, monkeypatch, drain_conn):
    """A mixed sequence must keep requests == hits + fallbacks throughout."""
    srv, cli = drain_conn
    monkeypatch.setattr(sd, "_fastpath_plan", lambda cmd, hdr: None)  # all not_eligible
    for _ in range(5):
        sd._try_fastpath_exec(srv, ["python3", "-I", "-c", "x"], {}, b"")

    monkeypatch.setattr(
        sd, "_fastpath_plan", lambda cmd, hdr: {"mode": "code", "code": "pass"}
    )
    monkeypatch.setattr(
        sd._FORK_POOL, "submit", lambda *a, **k: {"exit_code": 0, "stdout": "", "stderr": ""}
    )
    for _ in range(3):
        sd._try_fastpath_exec(srv, ["python3", "-c", "pass"], {}, b"")
        _drain(cli)  # discard the framed response so the socket buffer drains

    monkeypatch.setattr(
        sd._FORK_POOL, "submit", lambda *a, **k: {"error": "worker error: x"}
    )
    for _ in range(2):
        sd._try_fastpath_exec(srv, ["python3", "-c", "x"], {}, b"")
        _drain(cli)

    snap = isolated_stats.snapshot()
    assert snap["requests"] == 10
    assert snap["hits"] == 3
    assert snap["fallbacks"] == 7
    assert snap["fallback_reasons"] == {"not_eligible": 5, "worker_error": 2}
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]


def test_nonempty_stdin_falls_back_without_submit(isolated_stats, monkeypatch,
                                                   drain_conn):
    """A non-empty stdin never reaches the worker (deadlock guard).

    The request is counted and bucketed as ``nonempty_stdin``; ``submit`` must
    not run, so the daemon takes the normal Popen path before any child.
    """
    monkeypatch.setattr(sd, "_fastpath_plan",
                        lambda cmd, hdr: {"mode": "code", "code": "pass"})
    called = {"submit": False}
    monkeypatch.setattr(sd._FORK_POOL, "submit",
                        lambda *a, **k: called.__setitem__("submit", True))

    srv, _cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-c", "pass"], {}, b"some stdin")

    assert rc is False
    assert called["submit"] is False
    snap = isolated_stats.snapshot()
    assert snap["requests"] == 1
    assert snap["hits"] == 0
    assert snap["fallbacks"] == 1
    assert snap["fallback_reasons"] == {"nonempty_stdin": 1}
    assert snap["requests"] == snap["hits"] + snap["fallbacks"]


def test_capacity_busy_falls_back(monkeypatch, drain_conn):
    """All workers busy -> ``capacity_busy`` fallback, no breaker bump.

    ``submit`` raising ``capacity_busy`` is recorded as a fallback reason; the
    daemon takes the Popen path. Capacity is a signal, not a failure, so the
    breaker stays closed (the routing path never bumps it on this reason).
    """
    stats = FastPathStats()
    monkeypatch.setattr(sd._FORK_POOL, "stats", stats)
    monkeypatch.setattr(sd, "_fastpath_plan",
                        lambda cmd, hdr: {"mode": "code", "code": "x"})
    monkeypatch.setattr(
        sd._FORK_POOL, "submit",
        lambda *a, **k: (_ for _ in ()).throw(FastPathUnavailable("capacity_busy")))

    srv, _cli = drain_conn
    rc = sd._try_fastpath_exec(srv, ["python3", "-c", "x"], {}, b"")

    assert rc is False
    snap = stats.snapshot()
    assert snap["requests"] == 1
    assert snap["fallbacks"] == 1
    assert snap["fallback_reasons"] == {"capacity_busy": 1}


def test_capacity_busy_submit_raises_without_bumping_breaker(monkeypatch):
    """The real ``submit`` round-robin: all-live-busy workers raise immediately.

    Stubs the pool with two fake live workers whose locks are already held
    (busy). ``submit`` must raise ``capacity_busy`` without spawning or sending
    a frame, and must NOT call ``_bump_failure_locked`` (breaker unaffected).
    No real worker / fork is needed, so this runs on every platform.
    """
    pool = sd._FORK_POOL
    locks = [threading.Lock(), threading.Lock()]
    for lk in locks:
        lk.acquire()  # simulate both workers busy
    fake_workers = [
        (types.SimpleNamespace(poll=lambda: None), object(), locks[0]),
        (types.SimpleNamespace(poll=lambda: None), object(), locks[1]),
    ]
    monkeypatch.setattr(pool, "_workers", fake_workers)
    monkeypatch.setattr(pool, "_ensure", lambda: True)
    monkeypatch.setattr(pool, "_next", 0)
    monkeypatch.setattr(pool, "_breaker_state", "closed")
    monkeypatch.setattr(pool, "_breaker_failures", 0)
    bumps = {"n": 0}
    monkeypatch.setattr(pool, "_bump_failure_locked",
                        lambda: bumps.__setitem__("n", bumps["n"] + 1))

    with pytest.raises(FastPathUnavailable) as ei:
        pool.submit(_FastPathRequest(code="pass", stdin_bytes=b"", workdir=None,
                                     env_overrides=None, timeout=1.0, plan=None))
    assert ei.value.reason == "capacity_busy"
    assert bumps["n"] == 0  # breaker NOT advanced on capacity busy
    # Worker locks still held by the test (submit never acquired/released them).
    for lk in locks:
        assert lk.locked()


# --------------------------------------------------------------------------- #
# throttle / dirty / flush
# --------------------------------------------------------------------------- #
def test_first_record_writes_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "stats.json"
    monkeypatch.setattr(sd, "FASTPATH_STATS_PATH", str(path))
    stats = FastPathStats()
    stats.record_fallback("not_eligible")
    assert path.exists()
    assert json.loads(path.read_text("utf-8"))["fallback_reasons"] == {"not_eligible": 1}


def test_throttled_write_sets_dirty_and_flush_persists(monkeypatch, tmp_path):
    path = tmp_path / "stats.json"
    monkeypatch.setattr(sd, "FASTPATH_STATS_PATH", str(path))
    stats = FastPathStats()
    stats.record_fallback("not_eligible")  # first write (last_write was 0)
    assert json.loads(path.read_text("utf-8"))["fallbacks"] == 1

    # Simulate a write that just happened: subsequent records are throttled.
    stats._last_write = time.monotonic()
    stats.record_fallback("not_eligible")  # throttled -> dirty, file stale
    assert stats._dirty is True
    assert json.loads(path.read_text("utf-8"))["fallbacks"] == 1  # still stale

    # The idle-reaper flush force-writes the accumulated counters.
    stats.flush()
    assert stats._dirty is False
    assert json.loads(path.read_text("utf-8"))["fallbacks"] == 2


def test_not_eligible_path_writes_snapshot_via_record(monkeypatch, tmp_path):
    """Regression: ``not_eligible`` was never flushed."""
    path = tmp_path / "stats.json"
    monkeypatch.setattr(sd, "FASTPATH_STATS_PATH", str(path))
    monkeypatch.setattr(sd._FORK_POOL, "stats", FastPathStats())
    monkeypatch.setattr(sd, "_fastpath_plan", lambda cmd, hdr: None)
    monkeypatch.setattr(sd._FORK_POOL, "submit", lambda *a, **k: pytest.fail("submit must not run"))

    # Build a throwaway conn; not_eligible returns False before any send.
    srv, cli = socket.socketpair()
    try:
        got = sd._try_fastpath_exec(srv, ["python3", "-I", "-c", "x"], {}, b"")
    finally:
        srv.close()
        cli.close()

    assert got is False
    assert path.exists()
    data = json.loads(path.read_text("utf-8"))
    assert data["fallback_reasons"] == {"not_eligible": 1}
    assert data["requests"] == data["hits"] + data["fallbacks"]
