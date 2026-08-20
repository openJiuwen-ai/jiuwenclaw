# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the FastPath worker ``_run_child`` reap race.

These tests exercise the real worker ``_run_child`` (fork/waitpid/pipe-drain)
by driving a *real* worker subprocess over a socketpair via
``_fastpath_worker_session.WorkerSession`` -- the same launch path production
uses (``Popen([python, -c, WORKER_SOURCE, fd], stdin/stdout/stderr=DEVNULL,
pass_fds=[fd])``). Because the worker is a separate process with DEVNULL
standard streams, these tests are robust to pytest's default fd-level capture
and need no ``-s``.

The central regression (``test_child_reaped_before_pipe_eof_no_echild``)
deterministically constructs the window that previously leaked
``ChildProcessError`` (ECHILD, errno 10): a child that forks a grandchild
holding the stdout/stderr pipes open, then exits. The parent reaps the child
(``child_done``) while the pipes are still not EOF, so the loop must keep
draining *without* calling ``waitpid(pid)`` a second time. Before the fix the
second ``waitpid`` raised ``ChildProcessError`` which surfaced to clients as
``worker error: [Errno 10] No child processes`` on ~0.1% of requests.

These tests must run on POSIX (they call ``os.fork`` inside the worker).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

# ``tests/unit`` is a package (``__init__.py`` present), so the sibling helper
# is imported as a relative module -- no ``sys.path`` mutation and no global
# conftest side effects.
from ._fastpath_worker_session import WorkerSession  # noqa: E402


pytestmark = pytest.mark.unit

_SKIP_NON_POSIX = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="_run_child requires os.fork (POSIX)",
)


@_SKIP_NON_POSIX
def test_normal_exit_zero_captures_stdout():
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code="print('hello')")
    assert exit_code == 0
    assert out == b"hello\n"
    assert err == b""


@_SKIP_NON_POSIX
def test_nonzero_exit_preserves_code_and_stderr():
    code = "import sys; sys.stderr.write('boom\\n'); sys.exit(7)"
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code=code)
    assert exit_code == 7
    assert err == b"boom\n"
    assert out == b""


@_SKIP_NON_POSIX
def test_dash_c_main_module_matches_real_interpreter():
    """``import __main__`` under ``-c`` sees the code's own globals.

    Previously the FastPath ``-c`` path exec'd into a bare dict, so
    ``import __main__`` returned the worker's own module (exposing ``_run_child``
    etc.) and code-defined names were invisible. Now it installs a fresh
    ``__main__`` module whose namespace is the code's globals, matching CPython.
    The forked child mutates ``sys.modules`` copy-on-write and ``os._exit``s,
    so the parent worker is unaffected.
    """
    code = ("import __main__ as m\n"
            "X = 1\n"
            "print(m.__name__, hasattr(m, 'X'), m.X, hasattr(m, '_run_child'))\n")
    with WorkerSession() as ws:
        fp_rc, fp_out, fp_err = ws.run(code=code)
    assert fp_rc == 0, fp_err
    rl = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert fp_out == rl.stdout, f"fastpath={fp_out!r}\nreal={rl.stdout!r}"
    # Fixed behaviour: the worker's own symbols must NOT leak into __main__.
    assert b"__main__ True 1 False" in fp_out


@_SKIP_NON_POSIX
def test_dash_c_argv_matches_real_interpreter():
    """``sys.argv`` under bare ``-c`` is ``['-c']`` like CPython.

    The worker is launched as ``python -c <worker_source> <control_fd>``, so
    without the explicit ``sys.argv = ['-c']`` the child would inherit an argv
    carrying the internal control fd. Verified differentially against a real
    ``sys.executable -c``.
    """
    code = "import sys; print(repr(sys.argv))"
    with WorkerSession() as ws:
        fp_rc, fp_out, fp_err = ws.run(code=code)
    assert fp_rc == 0, fp_err
    rl = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert fp_out == rl.stdout, f"fastpath={fp_out!r}\nreal={rl.stdout!r}"
    assert fp_out == b"['-c']\n"


@_SKIP_NON_POSIX
def test_stdin_forwarded_and_json_stdout():
    code = ("import json,sys; d=json.loads(sys.stdin.read()); "
            "d['result']='ok'; print(json.dumps(d))")
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code=code, stdin=b'{"a":1,"b":2}')
    assert exit_code == 0
    # Must round-trip the JSON and tag the result.
    assert b'"result": "ok"' in out or b'"result":"ok"' in out
    assert b'"a": 1' in out or b'"a":1' in out


@_SKIP_NON_POSIX
def test_long_lived_grandchild_marker_written_once(tmp_path):
    """A long-lived grandchild must not cause double execution.

    The child writes a side-effect marker, forks a grandchild that holds the
    stdout/stderr pipe write-ends longer than the request timeout, then exits.
    ``_run_child`` reaps the child while the pipes are still open, so the drain
    loop keeps running until the grandchild releases the pipes. The marker
    must appear exactly once: ``_run_child`` forks the code a single time; the
    no-replay guarantee against a *Popen* replay lives at the ``submit`` /
    daemon layer and is covered by the pool-level marker bench.

    The grandchild sleeps longer than ``timeout`` but is bounded so the test
    completes: the deadline kill fires on the (already dead) child, the
    grandchild then exits, the pipes reach EOF, and ``_run_child`` returns 124
    (timeout) -- no hang, no ECHILD leak, marker == 1.
    """
    # ``tmp_path`` (pytest-managed) yields a unique dir cleaned up after the
    # test, so the marker file is always removed (no leftover temp files).
    marker = str(tmp_path / "p8a2_marker")
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
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code=code, timeout=1.0)
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
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code=code, timeout=10.0)
    assert exit_code == 7, f"expected exit 7, got {exit_code} (out={out!r}, err={err!r})"
    assert out == b"hello\n"
    # No ECHILD leak: stderr must not carry the worker error string.
    assert b"No child processes" not in err
    assert b"ECHILD" not in err


@_SKIP_NON_POSIX
def test_signal_death_reported_as_negative_signum():
    import signal as _sig
    # Child kills itself with SIGTERM; FastPath reports signal deaths as
    # -signum (matching subprocess semantics).
    code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code=code, timeout=10.0)
    assert exit_code == -int(_sig.SIGTERM)


@_SKIP_NON_POSIX
def test_timeout_returns_124_and_kills_child():
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code="import time; time.sleep(30)",
                                     timeout=1.0)
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
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code="import time; time.sleep(30)",
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
    code = ("import sys, time\n"
            "sys.stderr.write('partial\\n'); sys.stderr.flush()\n"
            "time.sleep(30)\n")
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code=code, timeout=1.5)
    assert exit_code == 124
    assert err == b"partial\n\nCommand timed out", f"got {err!r}"


@_SKIP_NON_POSIX
def test_script_exiting_124_gets_no_timeout_marker():
    """The marker must mean "deadline", not merely "exit code 124"."""
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code="import sys; sys.exit(124)",
                                     timeout=10.0)
    assert exit_code == 124
    assert b"Command timed out" not in err, f"got {err!r}"


@_SKIP_NON_POSIX
@pytest.mark.parametrize("payload_mib", [2, 16])
def test_large_output_within_popen_contract_is_returned_intact(payload_mib):
    """Output up to the Popen contract succeeds, no frame cap.

    The worker->daemon frame cap was raised to align with the
    daemon->box-server response contract (``DAEMON_MAX_RESPONSE_BYTES =
    256 MiB``). A 2 MiB stdout -- which previously triggered the 1 MiB
    ``frame too large`` replay path -- now returns intact, matching Popen.
    16 MiB is a representative value comfortably above the old threshold,
    confirming alignment holds as output grows (not just barely past 1 MiB).
    """
    payload_size = payload_mib * 1024 * 1024
    code = ("import sys; sys.stdout.write('x' * %d)" % payload_size)
    with WorkerSession() as ws:
        exit_code, out, err = ws.run(code=code, timeout=60.0)
    assert exit_code == 0, err
    assert len(out) == payload_size
    assert out == b"x" * payload_size
