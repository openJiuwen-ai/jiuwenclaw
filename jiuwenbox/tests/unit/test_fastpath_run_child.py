# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the FastPath worker ``_run_child`` reap race.

These tests target the ``_run_child`` function embedded in
``FORKSERVER_WORKER_SOURCE`` inside ``sandbox_daemon.py``. They exec the
worker source into a private namespace to obtain the function and drive it
directly, so they exercise the real fork/waitpid/pipe-drain loop without
needing a live sandbox or box-server.

The central regression (``test_child_reaped_before_pipe_eof_no_echild``)
deterministically constructs the window that previously leaked
``ChildProcessError`` (ECHILD, errno 10): a child that forks a grandchild
holding the stdout/stderr pipes open, then exits. The parent reaps the child
(``child_done``) while the pipes are still not EOF, so the loop must keep
draining *without* calling ``waitpid(pid)`` a second time. Before the fix the
second ``waitpid`` raised ``ChildProcessError`` which surfaced to clients as
``worker error: [Errno 10] No child processes`` on ~0.1% of requests.

These tests must run on POSIX (they call ``os.fork``).
"""

from __future__ import annotations

import os
import sys

import pytest

from jiuwenbox.supervisor.sandbox_daemon import FORKSERVER_WORKER_SOURCE


pytestmark = pytest.mark.unit

_SKIP_NON_POSIX = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="_run_child requires os.fork (POSIX)",
)


def _load_worker_ns() -> dict:
    """Exec the worker source into a fresh namespace and return it."""
    ns: dict = {}
    exec(compile(FORKSERVER_WORKER_SOURCE, "<worker_src>", "exec"), ns)
    return ns


def _run_child(ns: dict, code: str, stdin_bytes: bytes | None = None,
               timeout: float | None = None):
    """Convenience wrapper calling the worker's ``_run_child``.

    The worker's ``_run_child(code, stdin_bytes, workdir, env_overrides,
    timeout, control_fd)`` needs a ``control_fd``; we pass a spare fd (the
    read end of a pipe) that the child closes immediately.
    """
    run_child = ns["_run_child"]
    # Flush the parent's stdout/stderr before the fork inside ``_run_child``
    # so the child does not inherit (and later flush) pytest's captured
    # output into the capture pipe.
    sys.stdout.flush()
    sys.stderr.flush()
    r, w = os.pipe()
    try:
        return run_child(code, stdin_bytes or b"", None, {}, timeout, r)
    finally:
        for fd in (r, w):
            try:
                os.close(fd)
            except OSError:
                pass


@_SKIP_NON_POSIX
def test_normal_exit_zero_captures_stdout():
    ns = _load_worker_ns()
    exit_code, out, err = _run_child(ns, "print('hello')")
    assert exit_code == 0
    assert out == b"hello\n"
    assert err == b""


@_SKIP_NON_POSIX
def test_nonzero_exit_preserves_code_and_stderr():
    ns = _load_worker_ns()
    code = "import sys; sys.stderr.write('boom\\n'); sys.exit(7)"
    exit_code, out, err = _run_child(ns, code)
    assert exit_code == 7
    assert err == b"boom\n"
    assert out == b""


@_SKIP_NON_POSIX
def test_dash_c_main_module_matches_real_interpreter():
    """Phase 8A-1: ``import __main__`` under ``-c`` sees the code's own globals.

    Previously the FastPath ``-c`` path exec'd into a bare dict, so
    ``import __main__`` returned the worker's own module (exposing ``_run_child``
    etc.) and code-defined names were invisible. Now it installs a fresh
    ``__main__`` module whose namespace is the code's globals, matching CPython.
    The forked child mutates ``sys.modules`` copy-on-write and ``os._exit``s,
    so the parent worker is unaffected.
    """
    import subprocess
    code = ("import __main__ as m\n"
            "X = 1\n"
            "print(m.__name__, hasattr(m, 'X'), m.X, hasattr(m, '_run_child'))\n")
    ns = _load_worker_ns()
    fp_rc, fp_out, fp_err = _run_child(ns, code)
    assert fp_rc == 0, fp_err
    rl = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert fp_out == rl.stdout, f"fastpath={fp_out!r}\nreal={rl.stdout!r}"
    # Fixed behaviour: the worker's own symbols must NOT leak into __main__.
    assert b"__main__ True 1 False" in fp_out


@_SKIP_NON_POSIX
def test_stdin_forwarded_and_json_stdout():
    ns = _load_worker_ns()
    code = ("import json,sys; d=json.loads(sys.stdin.read()); "
            "d['result']='ok'; print(json.dumps(d))")
    exit_code, out, err = _run_child(ns, code, stdin_bytes=b'{"a":1,"b":2}')
    assert exit_code == 0
    # Must round-trip the JSON and tag the result.
    assert b'"result": "ok"' in out or b'"result":"ok"' in out
    assert b'"a": 1' in out or b'"a":1' in out


@_SKIP_NON_POSIX
def test_long_lived_grandchild_marker_written_once():
    """Phase 8A-2: a long-lived grandchild must not cause double execution.

    The child writes a side-effect marker, forks a grandchild that holds the
    stdout/stderr pipe write-ends longer than the request timeout, then exits.
    ``_run_child`` reaps the child while the pipes are still open, so the drain
    loop keeps running until the grandchild releases the pipes. The marker
    must appear exactly once: ``_run_child`` forks the code a single time; the
    no-replay guarantee against a *Popen* replay lives at the ``submit`` /
    daemon layer and is covered by the Phase 8A-2 pool-level marker bench.

    The grandchild sleeps longer than ``timeout`` but is bounded so the test
    completes: the deadline kill fires on the (already dead) child, the
    grandchild then exits, the pipes reach EOF, and ``_run_child`` returns the
    child's real exit code -- no hang, no ECHILD leak, marker == 1.
    """
    import tempfile
    ns = _load_worker_ns()
    marker = tempfile.mktemp(prefix="p8a2_marker_")
    code = (
        "import os, sys, time\n"
        "open(%r, 'w').write('1\\n')\n"  # side effect, exactly once
        "sys.stdout.write('hello\\n')\n"
        "sys.stdout.flush()\n"
        "gc = os.fork()\n"
        "if gc == 0:\n"
        "    # Grandchild outlives the deadline kill (which only targets the\n"
        "    # direct child) and holds the pipes open, then exits so the test\n"
        "    # is bounded and the drain loop converges.\n"
        "    time.sleep(2.0)\n"
        "    os._exit(0)\n"
        "os._exit(7)\n" % marker
    )
    exit_code, out, err = _run_child(ns, code, timeout=1.0)
    # The child exited 7 immediately, but the grandchild holds the pipes past
    # the 1.0s deadline, so ``_run_child`` cannot observe pipe EOF in time and
    # reports a timeout (124) -- the same wedge the daemon socket timeout later
    # breaks. ``exit_code`` is a return value (from waitpid status), so it is
    # robust to pytest fd-capture; the stdout/stderr *content* is not (the
    # documented pytest-capture x fork incompatibility), so the property under
    # test here is the marker count, not the captured bytes.
    assert exit_code == 124, f"expected timeout 124, got {exit_code} (out={out!r}, err={err!r})"
    # The side effect ran exactly once: the replay guarantee is enforced at the
    # daemon layer, but this confirms ``_run_child`` itself never re-forks even
    # when a grandchild outlives the deadline.
    with open(marker) as fh:
        assert len(fh.read().splitlines()) == 1


@_SKIP_NON_POSIX
def test_child_reaped_before_pipe_eof_no_echild():
    """Deterministic regression for the ECHILD reap race.

    Child forks a grandchild that inherits the stdout/stderr pipe write-ends
    and sleeps, then the child exits with code 7. The parent reaps the child
    while the pipes are still open, so the drain loop must NOT call
    ``waitpid(pid)`` again. Before the fix this raised ``ChildProcessError``
    (ECHILD, errno 10) and surfaced as ``worker error: [Errno 10] No child
    processes``; the function must instead return the real exit code 7 with
    the child's stdout intact.
    """
    ns = _load_worker_ns()
    code = (
        "import os, sys, time\n"
        "sys.stdout.write('hello\\n')\n"
        "sys.stdout.flush()\n"
        "gc = os.fork()\n"
        "if gc == 0:\n"
        "    # Grandchild holds stdout/stderr pipes open after the child is\n"
        "    # reaped, forcing the parent into the 'child_done but pipes not\n"
        "    # EOF' branch for many loop iterations.\n"
        "    time.sleep(0.5)\n"
        "    os._exit(0)\n"
        "# Child exits 7 immediately; grandchild keeps pipes open.\n"
        "os._exit(7)\n"
    )
    exit_code, out, err = _run_child(ns, code, timeout=10.0)
    assert exit_code == 7, f"expected exit 7, got {exit_code} (out={out!r}, err={err!r})"
    assert out == b"hello\n"
    # No ECHILD leak: stderr must not carry the worker error string.
    assert b"No child processes" not in err
    assert b"ECHILD" not in err


@_SKIP_NON_POSIX
def test_signal_death_reported_as_negative_signum():
    import signal as _sig
    ns = _load_worker_ns()
    # Child kills itself with SIGTERM; FastPath reports signal deaths as
    # -signum (matching subprocess semantics).
    code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    exit_code, out, err = _run_child(ns, code, timeout=10.0)
    assert exit_code == -int(_sig.SIGTERM)


@_SKIP_NON_POSIX
def test_timeout_returns_124_and_kills_child():
    ns = _load_worker_ns()
    code = "import time; time.sleep(30)"
    exit_code, out, err = _run_child(ns, code, timeout=1.0)
    assert exit_code == 124


@_SKIP_NON_POSIX
def test_timeout_stderr_carries_the_same_marker_as_the_normal_path():
    """A deadline kill must be distinguishable from a script exiting 124.

    The normal ``subprocess`` path appends ``Command timed out`` to stderr on
    ``TimeoutExpired``. box-server forwards only exit_code/stdout/stderr, so
    without this marker a fast-path timeout is indistinguishable from a script
    that called ``sys.exit(124)`` -- an observable behaviour difference between
    the two paths for the same command.
    """
    ns = _load_worker_ns()
    exit_code, out, err = _run_child(ns, "import time; time.sleep(30)",
                                     timeout=1.0)
    assert exit_code == 124
    assert err == b"Command timed out", f"got {err!r}"


@_SKIP_NON_POSIX
def test_timeout_marker_is_appended_after_partial_stderr():
    """Output written before the deadline must survive, marker appended last.

    The blank line is not incidental: the normal path builds
    ``f"{stderr_text}\\nCommand timed out"``, and a script's stderr usually
    already ends in a newline, so it too emits ``partial\\n\\nCommand timed
    out``. Byte-for-byte sameness with that path is the property under test.
    """
    ns = _load_worker_ns()
    code = ("import sys, time\n"
            "sys.stderr.write('partial\\n'); sys.stderr.flush()\n"
            "time.sleep(30)\n")
    exit_code, out, err = _run_child(ns, code, timeout=1.5)
    assert exit_code == 124
    assert err == b"partial\n\nCommand timed out", f"got {err!r}"


@_SKIP_NON_POSIX
def test_script_exiting_124_gets_no_timeout_marker():
    """The marker must mean "deadline", not merely "exit code 124"."""
    ns = _load_worker_ns()
    exit_code, out, err = _run_child(ns, "import sys; sys.exit(124)",
                                     timeout=10.0)
    assert exit_code == 124
    assert b"Command timed out" not in err, f"got {err!r}"
