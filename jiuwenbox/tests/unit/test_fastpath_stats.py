# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for Phase 6C-2 FastPath stats accounting and throttled flush.

Covers the observability contract fixed in Phase 6C-2:

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
import time

import pytest

from jiuwenbox.supervisor import sandbox_daemon as sd
from jiuwenbox.supervisor.sandbox_daemon import FastPathUnavailable, FastPathStats

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
    """Regression for the Phase 6B bug: not_eligible never flushed."""
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
