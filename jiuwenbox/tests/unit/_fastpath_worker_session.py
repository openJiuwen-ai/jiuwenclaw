# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""A capture-robust FastPath worker driver for the fork-based unit tests.

Why this exists
---------------
The FastPath worker source (``FORKSERVER_WORKER_SOURCE``) is a ``python3 -c``
program. Earlier tests exec'd that source *into the pytest process's
namespace* and called ``_run_child`` directly. That inherits pytest's default
fd-level capture: pytest replaces ``sys.stdout``/``sys.stderr`` with capture
objects that write to pytest's own pipes (not fd 1/2), and the worker's
``os.dup2(w_out, 1)`` only redirects the OS-level fd -- the Python ``print()``
still went to the capture object. So stdout/stderr assertions came back empty
under standard capture (only ``pytest -s`` passed).

Production never has this problem: the worker is a real subprocess
(``Popen([sys.executable, "-c", WORKER_SOURCE, fd], stdin/stdout/stderr=DEVNULL,
pass_fds=[fd])``) whose ``sys.stdout`` is a real fd-1-backed object, so
``dup2`` correctly routes ``print()`` to the worker pipe.

This helper drives that *real* subprocess over a socketpair, mirroring
``ForkServerPool._spawn`` + ``submit`` exactly. Because the worker is a
separate process with DEVNULL standard streams, pytest capture cannot touch
it -- the tests are capture-robust by construction and need no ``-s``.

This is a test-support module (imported by the two fork-based test files), not
a conftest: it defines no fixtures, adds no global hooks, and has no side
effects at import time.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

from jiuwenbox.supervisor.sandbox_daemon import (
    FORKSERVER_WORKER_SOURCE,
    _FASTPATH_MAX_FRAME,
    _recv_frame,
    _send_frame,
)


class WorkerSession:
    """One real FastPath worker subprocess, driven over a socketpair.

    Mirrors ``ForkServerPool._spawn`` + ``submit``: launch the worker as
    ``python -c <FORKSERVER_WORKER_SOURCE> <fd>`` with DEVNULL standard streams
    and the control fd passed through, then exchange length-prefixed JSON
    frames. Use :meth:`run` per request; :meth:`close` reaps the worker.
    """

    def __init__(self) -> None:
        parent, child = socket.socketpair()
        self._parent = parent
        env = dict(os.environ)
        # Match production: the worker must not see the control-fd env var.
        env.pop("JIUWENBOX_CONTROL_LISTENER_FD", None)
        self._proc = subprocess.Popen(
            [sys.executable, "-c", FORKSERVER_WORKER_SOURCE, str(child.fileno())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            close_fds=True,
            pass_fds=[child.fileno()],
            start_new_session=True,
        )
        child.close()

    def run(
        self,
        *,
        code: str | None = None,
        plan: dict | None = None,
        stdin: bytes = b"",
        workdir: str | None = None,
        env_overrides: dict | None = None,
        timeout: float | None = None,
    ) -> tuple[int, bytes, bytes]:
        """Send one request; return ``(exit_code, stdout_bytes, stderr_bytes)``.

        Raises if the worker dies or returns a malformed/``error`` frame (so a
        test failure points at the real cause rather than a silent mismatch).
        """
        header: dict = {
            "code": code,
            "stdin_size": len(stdin),
            "timeout": timeout,
        }
        if plan is not None:
            header["plan"] = plan
        if workdir:
            header["workdir"] = workdir
        if env_overrides:
            header["env"] = env_overrides
        _send_frame(self._parent, json.dumps(header).encode("utf-8"))
        # The worker always consumes a stdin frame (4-byte length prefix +
        # payload); send it even when empty so the stream stays aligned.
        self._parent.sendall(len(stdin).to_bytes(4, "big"))
        if stdin:
            self._parent.sendall(stdin)
        resp = json.loads(_recv_frame(self._parent, _FASTPATH_MAX_FRAME).decode("utf-8"))
        if "error" in resp:
            raise AssertionError(f"worker error: {resp['error']}")
        return (
            int(resp.get("exit_code", 1)),
            resp.get("stdout", "").encode("utf-8"),
            resp.get("stderr", "").encode("utf-8"),
        )

    def close(self) -> None:
        try:
            self._parent.close()
        except OSError:
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def __enter__(self) -> "WorkerSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
