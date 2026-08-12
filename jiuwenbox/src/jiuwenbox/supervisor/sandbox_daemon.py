# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Long-running in-sandbox daemon that handles ``exec`` requests.

The previous implementation was a placeholder that just blocked on
``signal.pause`` so the sandbox lifecycle stayed alive. To make exec
calls cheap, the daemon now hosts a Unix-socket IPC server inside the
sandbox: box-server connects to the socket and sends ``exec`` requests
that the daemon services by ``fork+exec``-ing the user command. Because
the daemon already has bubblewrap's namespaces, mounts, seccomp, and
Landlock applied, every spawned child inherits the same isolation - the
expensive ``bwrap`` setup happens once per sandbox lifecycle instead of
once per exec.

How the daemon is started
-------------------------
Inside the sandbox the Landlock launcher (see ``landlock_launcher.py``)
reads this script's source from ``/jiuwenbox`` *before* applying
Landlock and then runs the daemon code with ``compile``/``exec`` in the
launcher's own Python process. There is **no** second ``execve`` after
Landlock is locked in, which is what allows the ``/jiuwenbox`` directory
to be omitted from the Landlock allowlist: nothing in the sandbox needs
to read the daemon script from disk after Landlock applies. From the
kernel's point of view the daemon and the launcher are the same Linux
process (PID 1 of the sandbox PID namespace). The directory is reserved
by ``PolicyEngine`` so user policies cannot reference it; see
``_RESERVED_SANDBOX_PATHS`` in ``jiuwenbox/server/policy_engine.py``.

The control socket itself lives on the **host** filesystem; box-server
``bind()``s and ``listen()``s before spawning bubblewrap, then passes
the listener file descriptor into the sandbox via
``subprocess.Popen(pass_fds=...)``. Bubblewrap's user command path
never closes arbitrary inherited fds (only its own monitor/PID-1 paths
do), so the listener fd flows naturally through the bwrap → launcher
chain. The daemon recovers the fd from ``JIUWENBOX_CONTROL_LISTENER_FD``
and ``accept()``s against it. This keeps the IPC channel entirely
outside any sandbox-visible path, so user code spawned by the daemon
cannot reach (or delete) the listener.

Important security notes:

* Sandboxed payloads share the daemon's UID (typically ``nobody``).  The
  kernel's PID-1 signal protections are not sufficient on every platform,
  so seccomp additionally blocks ``kill``/``tkill``/``tgkill`` targeting
  infrastructure PIDs (1/2, process group, or broadcast).  Each user exec
  also runs in its own session so ``kill(0)`` cannot reach the daemon.

* Box-server shuts the daemon down via the ``shutdown`` IPC command
  (graceful) or ``SIGKILL`` from outside the namespace (forced).

* Children are spawned via :func:`subprocess.Popen` so the kernel does
  the standard ``fork``/``execve`` pair. The seccomp BPF filter and
  Landlock ruleset that bwrap and the launcher installed before running
  the daemon are inherited by every child - they cannot be relaxed or
  stripped from inside the sandbox. ``close_fds=True`` (the Python
  default) ensures the listener fd is **not** inherited by user code.

The module deliberately stays standard-library-only so it can be
launched with ``python3 -S`` (no ``import site``) for fastest cold
start, and so the launcher's ``compile``/``exec`` step does not need to
reach outside the standard library to load the daemon.
"""

from __future__ import annotations

import contextlib
import datetime
import errno
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

# These constants are duplicated from ``daemon_ipc`` so the in-sandbox
# daemon does not need to import the package; the script is mounted at
# ``/jiuwenbox/sandbox-daemon.py`` and executed directly. Any change must
# be mirrored in ``daemon_ipc.py``.
SANDBOX_RESERVED_DIR = "/jiuwenbox"
SANDBOX_DAEMON_SANDBOX_PATH = f"{SANDBOX_RESERVED_DIR}/sandbox-daemon.py"
SANDBOX_LAUNCHER_PATH = f"{SANDBOX_RESERVED_DIR}/landlock-launcher.py"
SANDBOX_DAEMON_COMMAND = ["python3", "-S", SANDBOX_DAEMON_SANDBOX_PATH]
LISTENER_FD_ENV = "JIUWENBOX_CONTROL_LISTENER_FD"

REQUEST_TYPE_EXEC = "exec"
REQUEST_TYPE_SHUTDOWN = "shutdown"
REQUEST_TYPE_WRITE_FILE = "write_file"
REQUEST_TYPE_READ_FILE = "read_file"
REQUEST_TYPE_LIST_DIR = "list_dir"
REQUEST_TYPE_EXEC_BACKGROUND = "exec_background"
REQUEST_TYPE_BG_STATUS = "bg_status"
REQUEST_TYPE_BG_KILL = "bg_kill"

PROTOCOL_VERSION = 1
MAX_HEADER_BYTES = 1 * 1024 * 1024
MAX_STDIN_BYTES = 64 * 1024 * 1024
MAX_STDOUT_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024

ACCEPT_TIMEOUT_SECONDS = 1.0
SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 30.0

logger = logging.getLogger("jiuwenbox.sandbox_daemon")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    if n == 0:
        return b""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(
                f"socket closed after {len(buf)}/{n} bytes",
            )
        buf.extend(chunk)
    return bytes(buf)


def _send_frame(sock: socket.socket, payload: bytes) -> None:
    if len(payload) > 0xFFFFFFFF:
        raise ValueError(f"frame size {len(payload)} exceeds 4 GiB")
    sock.sendall(struct.pack(">I", len(payload)))
    if payload:
        sock.sendall(payload)


def _recv_frame(sock: socket.socket, max_size: int) -> bytes:
    header = _recv_exact(sock, 4)
    (size,) = struct.unpack(">I", header)
    if size > max_size:
        raise ValueError(
            f"incoming frame size {size} exceeds limit {max_size}",
        )
    return _recv_exact(sock, size)


def _send_response(sock: socket.socket, response: dict[str, Any]) -> None:
    body = json.dumps(response, ensure_ascii=False).encode("utf-8")
    _send_frame(sock, body)


def _exec_response(
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    started: bool = True,
    error: str | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "ok": started,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }
    if error:
        response["error"] = error
    return response


def _stringify_command(command: list[Any]) -> list[str]:
    return [str(item) for item in command]


def _normalize_env(env: Any) -> dict[str, str] | None:
    if env is None:
        return None
    if not isinstance(env, dict):
        raise ValueError("env must be a JSON object")
    return {str(key): str(value) for key, value in env.items()}


class DaemonState:
    """Shared mutable state guarded by ``lock``."""

    def __init__(self) -> None:
        self.shutdown_event = threading.Event()
        self.in_flight = 0
        self.lock = threading.Lock()
        self.completion = threading.Condition(self.lock)

    def begin_request(self) -> None:
        with self.lock:
            self.in_flight += 1

    def end_request(self) -> None:
        with self.lock:
            self.in_flight -= 1
            if self.in_flight <= 0:
                self.completion.notify_all()

    def wait_drain(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self.lock:
            while self.in_flight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.completion.wait(timeout=remaining)
            return True


def _handle_exec(conn: socket.socket, header: dict[str, Any], state: DaemonState) -> None:
    """Run a single user command and stream the result back to ``conn``."""
    try:
        command = header.get("command")
        if not isinstance(command, list) or not command:
            _send_response(
                conn,
                _exec_response(
                    exit_code=2,
                    stderr="exec request missing 'command'",
                    started=False,
                    error="bad_request",
                ),
            )
            return
        command = _stringify_command(command)

        env_override = _normalize_env(header.get("env"))
        workdir = header.get("workdir")
        if workdir is not None and not isinstance(workdir, str):
            raise ValueError("workdir must be a string")
        timeout = header.get("timeout")
        if timeout is not None and not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a number")
        stdin_size = int(header.get("stdin_size") or 0)
        if stdin_size < 0 or stdin_size > MAX_STDIN_BYTES:
            raise ValueError(f"invalid stdin_size {stdin_size}")

        stdin_bytes = _recv_exact(conn, stdin_size) if stdin_size else b""

        # Experimental Python ForkServer fast path. Only activated when BOTH
        # the server marked the request (``python_fastpath``) and the daemon
        # environment enables the feature (``JIUWENBOX_PYTHON_FASTPATH=1``).
        # Falls back to the normal ``subprocess.Popen`` path below otherwise.
        if header.get("python_fastpath") and _fastpath_enabled():
            if _try_fastpath_exec(conn, command, header, stdin_bytes):
                return

        merged_env = dict(os.environ)
        # Children must not see the listener fd or the env var pointing at
        # it; ``close_fds=True`` (Python default) closes the fd, but we also
        # strip the env var so user code cannot trivially fingerprint the
        # daemon.
        merged_env.pop(LISTENER_FD_ENV, None)
        if env_override is not None:
            merged_env.update(env_override)

        proc_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE if stdin_size else subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": merged_env,
            "close_fds": True,
            # Isolate each exec in its own session so kill(0) from user
            # code cannot reach the long-running daemon process group.
            "start_new_session": True,
        }
        if workdir:
            proc_kwargs["cwd"] = workdir

        try:
            proc = subprocess.Popen(command, **proc_kwargs)
        except OSError as exc:
            # ``OSError`` already covers ``FileNotFoundError`` and
            # ``PermissionError``; keep one branch (G.ERR.09).
            _send_response(
                conn,
                _exec_response(
                    exit_code=127,
                    stderr=f"failed to spawn command: {exc}",
                    started=False,
                    error="spawn_failed",
                ),
            )
            return

        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                input=stdin_bytes if stdin_size else None,
                timeout=timeout,
            )
            response = _exec_response(
                exit_code=proc.returncode if proc.returncode is not None else 0,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
            )
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                stdout_bytes, stderr_bytes = b"", b""
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            stderr_text = (
                f"{stderr_text}\nCommand timed out"
                if stderr_text
                else "Command timed out"
            )
            response = _exec_response(
                exit_code=124,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_text,
                error="timeout",
            )

        _send_response(conn, response)
    except (ValueError, ConnectionError) as exc:
        try:
            _send_response(
                conn,
                _exec_response(
                    exit_code=2,
                    stderr=str(exc),
                    started=False,
                    error="bad_request",
                ),
            )
        except OSError:
            pass
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unhandled error while handling exec request")
        try:
            _send_response(
                conn,
                _exec_response(
                    exit_code=1,
                    stderr=f"daemon internal error: {exc}",
                    started=False,
                    error="internal",
                ),
            )
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Experimental Python ForkServer fast path (feature-flagged, default OFF).
#
# When ``JIUWENBOX_PYTHON_FASTPATH=1`` is set in the server environment (and
# therefore inherited into the sandbox daemon), ``python3 -c <code>`` exec
# requests that the server marked with ``python_fastpath: true`` are routed
# to a small persistent in-sandbox ForkServer instead of spawning a fresh
# interpreter per call. The worker source is passed to the worker via
# ``python3 -c <source>`` so nothing needs to be read from disk after
# Landlock applies (mirroring how the launcher loads the daemon itself).
# The forked children inherit the exact same bwrap namespace / userns /
# cgroup / seccomp / Landlock / mount envelope as the daemon. This is an
# experiment for perf/memory modelling only -- it is not a formal API and
# does not change the default ``/exec`` path. When the fast path is
# unavailable it falls back to the normal ``subprocess.Popen`` path.
FASTPATH_ENV = "JIUWENBOX_PYTHON_FASTPATH"
FASTPATH_WORKERS_ENV = "JIUWENBOX_PYTHON_FASTPATH_WORKERS"
FASTPATH_IDLE_TIMEOUT_ENV = "JIUWENBOX_PYTHON_FASTPATH_IDLE_TIMEOUT"
FASTPATH_DEFAULT_WORKERS = 2
FASTPATH_MARKER = "JIWENBOX_FORK_WORKER"
# Worker control fd is socketpair'd by the daemon; the fd number is passed
# as ``sys.argv[1]`` to the ``python3 -c`` worker.
_FASTPATH_MAX_FRAME = 1 * 1024 * 1024

# --- Phase 2: lifecycle / resilience / resource knobs --------------------
# Every knob is env-overridable but hard-clamped so a bad override can never
# grow the worker pool without bound or defeat the circuit breaker.
FASTPATH_MAX_WORKERS = 4            # per-sandbox hard ceiling
FASTPATH_DEFAULT_IDLE_TIMEOUT = 300.0  # recycle pool to 0 after this idle
FASTPATH_BREAKER_THRESHOLD = 3      # consecutive failures -> breaker open
FASTPATH_BREAKER_COOLDOWN = 30.0    # seconds before a half-open probe
FASTPATH_MAX_SPINS = 3              # worker-selection retries per request
FASTPATH_REAP_INTERVAL = 5.0        # idle-reaper wakeup interval
# Minimal observability: counters are mirrored (throttled) to a JSON
# snapshot inside the sandbox so host-side tooling / perf scripts can read
# them via the normal exec path without any new API surface. Purely
# diagnostic; never used for control flow.
FASTPATH_STATS_PATH = "/tmp/fastpath_stats.json"
FASTPATH_STATS_THROTTLE = 1.0       # min seconds between snapshot writes


def _fastpath_enabled() -> bool:
    return os.environ.get(FASTPATH_ENV) == "1"


def _fastpath_worker_count() -> int:
    raw = os.environ.get(FASTPATH_WORKERS_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value >= 1:
            return min(value, FASTPATH_MAX_WORKERS)
    return FASTPATH_DEFAULT_WORKERS


def _fastpath_idle_timeout() -> float:
    raw = os.environ.get(FASTPATH_IDLE_TIMEOUT_ENV)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value >= 10.0:
            return value
    return FASTPATH_DEFAULT_IDLE_TIMEOUT


# Source of the persistent in-sandbox ForkServer worker. Self-contained and
# stdlib-only. The worker is a single-threaded interpreter; each request
# ``fork()``s a child that runs the user's ``-c`` code, so the interpreter's
# already-loaded state (stdlib + site) is shared with the child via copy-on-
# write instead of paying a full interpreter cold start per exec.
FORKSERVER_WORKER_SOURCE = r'''
import json, os, select, signal, socket, struct, sys, time, traceback

MARKER = "JIWENBOX_FORK_WORKER"


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def _recv_frame(sock, max_size):
    (size,) = struct.unpack(">I", _recv_exact(sock, 4))
    if size > max_size:
        raise ValueError("frame too large")
    return _recv_exact(sock, size)


def _send_frame(sock, payload):
    sock.sendall(struct.pack(">I", len(payload)))
    if payload:
        sock.sendall(payload)


def _send_response(sock, resp):
    _send_frame(sock, json.dumps(resp).encode())


def _flush_std():
    # ``os._exit`` skips Python's stdio flush, so a plain ``print`` in user
    # code would otherwise lose buffered output. Flush explicitly on every
    # child exit path to match ``subprocess`` semantics.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass


def _run_child(code, stdin_bytes, workdir, env_overrides, timeout, control_fd):
    """Fork a child that runs ``code``; return (exit_code, stdout, stderr).

    Matches the daemon's sync-exec semantics: child runs in its own session
    (setsid), output is captured separately, and timeout yields exit 124.
    Signal deaths are reported as ``-signum`` (same as ``subprocess``).
    """
    r_out, w_out = os.pipe()
    r_err, w_err = os.pipe()
    r_in, w_in = os.pipe()
    pid = os.fork()
    if pid == 0:
        # --- child: run user code ---
        try:
            for fd in (r_out, r_err, w_in):
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.close(control_fd)
            except OSError:
                pass
            os.setsid()
            try:
                os.dup2(w_out, 1)
                os.dup2(w_err, 2)
                os.dup2(r_in, 0)
            finally:
                for fd in (w_out, w_err, r_in):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if workdir:
                try:
                    os.chdir(workdir)
                except OSError:
                    pass
            if env_overrides:
                os.environ.update(env_overrides)
            coded = compile(code, "<fastpath>", "exec")
            exec(coded, {"__name__": "__main__"})
            _flush_std()
            os._exit(0)
        except SystemExit as esc:
            code_val = getattr(esc, "code", None)
            if isinstance(code_val, str):
                # Match ``python3 -c``: a string exit code is printed to
                # stderr and the process exits 1.
                try:
                    sys.stderr.write(code_val + "\n")
                except Exception:
                    pass
                code_val = 1
            _flush_std()
            os._exit(code_val if isinstance(code_val, int) else (0 if code_val is None else 1))
        except BaseException:
            traceback.print_exc()
            _flush_std()
            os._exit(1)
    # --- parent (worker): feed stdin, collect stdout/stderr, enforce timeout ---
    for fd in (w_out, w_err, r_in):
        try:
            os.close(fd)
        except OSError:
            pass
    if stdin_bytes:
        try:
            os.write(w_in, stdin_bytes)
        except OSError:
            pass
    try:
        os.close(w_in)
    except OSError:
        pass

    out = b""
    err = b""
    deadline = time.monotonic() + timeout if timeout is not None else None
    timed_out = False
    stdout_fd = r_out
    stderr_fd = r_err
    while True:
        fds = [fd for fd in (stdout_fd, stderr_fd) if fd != -1]
        if fds:
            readable, _, _ = select.select(fds, [], [], 0.05)
            for fd in readable:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    if fd == stdout_fd:
                        stdout_fd = -1
                    else:
                        stderr_fd = -1
                elif fd == stdout_fd:
                    out += chunk
                else:
                    err += chunk
        if deadline is not None and time.monotonic() >= deadline and not timed_out:
            timed_out = True
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        wpid, status = os.waitpid(pid, os.WNOHANG)
        child_done = wpid == pid
        pipes_done = stdout_fd == -1 and stderr_fd == -1
        if child_done and pipes_done:
            if timed_out:
                return 124, out, err
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status), out, err
            return -os.WTERMSIG(status), out, err
        if not fds and not child_done:
            # No pipe activity and child still running; let the loop re-check
            # the deadline without busy-spinning.
            time.sleep(0.005)


def _worker_main(fd_str):
    fd = int(fd_str)
    sock = socket.socket(family=socket.AF_UNIX, type=socket.SOCK_STREAM, fileno=fd)
    while True:
        try:
            header = json.loads(_recv_frame(sock, 1 << 20))
        except Exception:
            break
        code = header.get("code")
        if not isinstance(code, str):
            _send_response(sock, {"error": "bad_request"})
            continue
        # The daemon always sends a stdin frame (4-byte length prefix +
        # payload, empty frame when no stdin); consume it so the stream
        # stays aligned across requests.
        stdin_bytes = _recv_frame(sock, 64 * 1024 * 1024)
        try:
            exit_code, out, err = _run_child(
                code,
                stdin_bytes,
                header.get("workdir"),
                header.get("env") or {},
                header.get("timeout"),
                fd,
            )
            _send_response(sock, {
                "exit_code": exit_code,
                "stdout": out.decode("utf-8", errors="replace"),
                "stderr": err.decode("utf-8", errors="replace"),
            })
        except Exception as exc:  # defensive
            _send_response(sock, {"error": f"worker error: {exc}"})


if __name__ == "__main__":
    _worker_main(sys.argv[1])
'''


class FastPathStats:
    """Minimal thread-safe fast-path observability counters.

    Counters are kept in memory and mirrored (throttled) to a JSON snapshot
    at ``/tmp/fastpath_stats.json`` inside the sandbox so host-side tooling
    and the perf scripts can read them through the normal exec/files path -
    no new API surface. Purely diagnostic; never used for control flow.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "requests": 0,
            "hits": 0,
            "fallbacks": 0,
            "fallback_reasons": {},
            "cold_starts": 0,
            "worker_restarts": 0,
            "spawn_failures": 0,
            "active_workers": 0,
            "breaker_state": "closed",
            "breaker_failures": 0,
        }
        self._last_write = 0.0

    def record_request(self) -> None:
        with self._lock:
            self._data["requests"] += 1

    def record_hit(self) -> None:
        with self._lock:
            self._data["hits"] += 1

    def record_fallback(self, reason: str) -> None:
        with self._lock:
            self._data["fallbacks"] += 1
            reasons = self._data["fallback_reasons"]
            reasons[reason] = reasons.get(reason, 0) + 1

    def record_cold_start(self) -> None:
        with self._lock:
            self._data["cold_starts"] += 1

    def record_worker_restart(self, count: int = 1) -> None:
        with self._lock:
            self._data["worker_restarts"] += count

    def record_spawn_failure(self) -> None:
        with self._lock:
            self._data["spawn_failures"] += 1

    def set_active(self, n: int) -> None:
        with self._lock:
            self._data["active_workers"] = n

    def set_breaker(self, state: str, failures: int) -> None:
        with self._lock:
            self._data["breaker_state"] = state
            self._data["breaker_failures"] = failures

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def write_snapshot(self, force: bool = False) -> None:
        """Write the throttled JSON snapshot; never raises on failure."""
        now = time.monotonic()
        if not force and (now - self._last_write) < FASTPATH_STATS_THROTTLE:
            return
        try:
            with open(FASTPATH_STATS_PATH, "wb") as fh:
                fh.write(json.dumps(self.snapshot()).encode("utf-8"))
            self._last_write = now
        except Exception:  # pragma: no cover - diagnostics must not break exec
            pass


class FastPathUnavailable(RuntimeError):
    """Raised by ``ForkServerPool.submit`` when the fast path is unusable.

    Carries a ``reason`` recorded for observability. Subclasses
    ``RuntimeError`` so existing callers catching ``RuntimeError`` keep
    working unchanged.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"fork fastpath unavailable: {reason}")
        self.reason = reason


class ForkServerPool:
    """Daemon-side pool of persistent in-sandbox ForkServer workers.

    Workers are spawned lazily on the first fast-path request (so an idle
    sandbox holds zero workers) and are direct children of the daemon, i.e.
    they live inside the same bwrap namespace / userns / pidns and inherit
    the same cgroup, seccomp, Landlock and mount envelope. Simple round-robin
    dispatch; no dynamic scaling in this experiment.

    Concurrency: each worker has its own lock so two workers can service two
    requests concurrently. The pool lock only guards pool state (spawn/prune/
    selection), never a blocking worker round-trip.

    Phase 2 resilience (all daemon-side, no new privileges):

    * **Auto-recovery**: killed/crashed workers are reaped and respawned on
      the next request; the daemon (PID 1) is never affected. Killing a
      worker only degrades *this sandbox's* fast path - the worker has no
      more privileges than any user child - so this is a same-sandbox
      availability concern, not a security-boundary breach.
    * **Circuit breaker**: after ``FASTPATH_BREAKER_THRESHOLD`` consecutive
      failures (worker death, spawn failure, protocol error) the breaker
      opens and fast-path requests fall straight back to ``/exec`` with no
      worker churn for ``FASTPATH_BREAKER_COOLDOWN`` seconds, then one
      half-open probe decides whether to re-close. This prevents rebuild
      storms.
    * **Idle recycle**: after ``FASTPATH_IDLE_TIMEOUT`` seconds of no fast
      path activity the pool recycles to zero workers (background reaper).
    * **Hard caps**: worker count is clamped to ``FASTPATH_MAX_WORKERS`` so
      a misconfigured override cannot grow the pool without bound.
    """

    def __init__(self) -> None:
        self._workers: list[tuple[subprocess.Popen, socket.socket, threading.Lock]] = []
        self._next = 0
        self._lock = threading.Lock()
        self._breaker_state = "closed"      # closed | open | half_open
        self._breaker_failures = 0
        self._cooldown_until = 0.0
        self._last_activity = time.monotonic()
        self._reaper_started = False
        self._stop_event = threading.Event()
        self.stats = FastPathStats()

    # -- lifecycle ---------------------------------------------------------
    def start_reaper(self) -> None:
        """Start the idle-recycle background thread (called once from main)."""
        if self._reaper_started:
            return
        self._reaper_started = True
        threading.Thread(
            target=self._reaper_loop,
            name="fastpath-idle-reaper",
            daemon=True,
        ).start()

    def _reaper_loop(self) -> None:
        while not self._stop_event.wait(FASTPATH_REAP_INTERVAL):
            idle_for = time.monotonic() - self._last_activity
            if idle_for < _fastpath_idle_timeout():
                continue
            self._recycle_idle(idle_for)

    def _recycle_idle(self, idle_for: float) -> None:
        with self._lock:
            if not self._workers:
                return
            # Only recycle when every live worker is idle: no request is
            # in flight and no request is blocked waiting on a worker lock.
            acquired: list[tuple[subprocess.Popen, socket.socket, threading.Lock]] = []
            all_idle = True
            for proc, sock, wlock in self._workers:
                if proc.poll() is not None:
                    continue
                if not wlock.acquire(blocking=False):
                    all_idle = False
                    break
                acquired.append((proc, sock, wlock))
            if not all_idle:
                for _p, _s, wl in acquired:
                    wl.release()
                return
            for proc, sock, _wl in self._workers:
                try:
                    sock.close()
                except OSError:
                    pass
                try:
                    proc.kill()
                except OSError:
                    pass
            for _p, _s, wl in acquired:
                wl.release()
            self._workers = []
            self.stats.set_active(0)
        self.stats.write_snapshot()
        logger.info(
            "fastpath pool idle-recycled to 0 workers after %.0fs idle",
            idle_for,
        )

    # -- breaker helpers (callers hold ``self._lock``) ---------------------
    def _bump_failure_locked(self) -> None:
        self._breaker_failures += 1
        if self._breaker_failures >= FASTPATH_BREAKER_THRESHOLD:
            self._breaker_state = "open"
            self._cooldown_until = time.monotonic() + FASTPATH_BREAKER_COOLDOWN
            self.stats.set_breaker("open", self._breaker_failures)
            logger.warning(
                "fastpath circuit breaker OPEN after %d consecutive failures; "
                "falling back to /exec for %.0fs",
                self._breaker_failures,
                FASTPATH_BREAKER_COOLDOWN,
            )

    def _record_success_locked(self) -> None:
        if self._breaker_failures:
            self._breaker_failures = 0
            self.stats.set_breaker(self._breaker_state, 0)
        if self._breaker_state == "half_open":
            self._breaker_state = "closed"
            self.stats.set_breaker("closed", 0)
            logger.info("fastpath circuit breaker CLOSED (probe succeeded)")

    def _allow_attempt(self) -> bool:
        with self._lock:
            if self._breaker_state != "open":
                return True
            if time.monotonic() >= self._cooldown_until:
                self._breaker_state = "half_open"
                self.stats.set_breaker("half_open", self._breaker_failures)
                logger.info("fastpath circuit breaker HALF-OPEN (allowing probe)")
                return True
            return False

    # -- worker management -------------------------------------------------
    def _spawn(self) -> tuple[subprocess.Popen, socket.socket]:
        parent, child = socket.socketpair()
        worker_env = dict(os.environ)
        worker_env.pop(LISTENER_FD_ENV, None)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                FORKSERVER_WORKER_SOURCE,
                str(child.fileno()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=worker_env,
            close_fds=True,
            pass_fds=[child.fileno()],
            start_new_session=True,
        )
        child.close()
        return proc, parent

    def _prune(self) -> None:
        alive: list[tuple[subprocess.Popen, socket.socket, threading.Lock]] = []
        for proc, sock, lock in self._workers:
            if proc.poll() is None:
                alive.append((proc, sock, lock))
            else:
                try:
                    sock.close()
                except OSError:
                    pass
        self._workers = alive

    def _ensure(self) -> bool:
        """Bring the pool up to target; ``True`` when live workers exist.

        Reaps dead/killed workers (counting restarts) and marks a cold start
        when a request turns an empty pool into a populated one. Must be
        called with ``self._lock`` held.
        """
        was_empty = not bool(self._workers)
        before = len(self._workers)
        self._prune()
        pruned = before - len(self._workers)
        if pruned:
            self.stats.record_worker_restart(pruned)
        target = min(_fastpath_worker_count(), FASTPATH_MAX_WORKERS)
        spawned = 0
        while len(self._workers) < target:
            try:
                proc, sock = self._spawn()
                self._workers.append((proc, sock, threading.Lock()))
                spawned += 1
            except OSError:
                logger.warning("fork fastpath worker spawn failed", exc_info=True)
                self.stats.record_spawn_failure()
                break
        if was_empty and spawned:
            self.stats.record_cold_start()
            logger.info("fastpath cold start: spawned %d worker(s)", spawned)
        self.stats.set_active(len(self._workers))
        return bool(self._workers)

    def _drop(self, proc: subprocess.Popen) -> None:
        self._workers = [
            (p, s, l) for (p, s, l) in self._workers if p is not proc
        ]

    def submit(
        self,
        code: str,
        stdin_bytes: bytes | None,
        workdir: str | None,
        env_overrides: dict[str, str] | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        """Send one code payload to a worker and return its JSON response.

        Raises ``FastPathUnavailable`` (a ``RuntimeError``) when the fast
        path cannot be used, so the caller can fall back to the normal
        ``subprocess.Popen`` path.
        """
        self._last_activity = time.monotonic()
        self.stats.record_request()
        if not self._allow_attempt():
            raise FastPathUnavailable("breaker_open")

        header: dict[str, Any] = {
            "code": code,
            "stdin_size": len(stdin_bytes or b""),
            "timeout": timeout,
        }
        if workdir:
            header["workdir"] = workdir
        if env_overrides:
            header["env"] = env_overrides

        # Pick a free worker (round-robin, non-blocking acquire); when all
        # are busy, block on the next round-robin worker's lock - but never
        # while holding the pool lock, so independent workers stay
        # concurrent. Bounded spins + the breaker stop rebuild storms when
        # workers keep dying.
        spins = 0
        while True:
            spins += 1
            if spins > FASTPATH_MAX_SPINS:
                with self._lock:
                    self._bump_failure_locked()
                raise FastPathUnavailable("worker_unavailable")
            with self._lock:
                if not self._ensure():
                    self._bump_failure_locked()
                    raise FastPathUnavailable("spawn_failed")
                chosen: tuple[subprocess.Popen, socket.socket, threading.Lock] | None = None
                for _ in range(len(self._workers)):
                    proc, sock, wlock = self._workers[self._next % len(self._workers)]
                    self._next += 1
                    if proc.poll() is not None:
                        self._prune()
                        break
                    if wlock.acquire(blocking=False):
                        chosen = (proc, sock, wlock)
                        break
                if chosen is None and not self._workers:
                    continue  # everything got pruned; re-ensure (respawn)
                if chosen is None:
                    proc, sock, wlock = self._workers[self._next % len(self._workers)]
                    self._next += 1
            if chosen is None:
                wlock.acquire()
                chosen = (proc, sock, wlock)
            if chosen[0].poll() is not None:
                with self._lock:
                    self._prune()
                chosen[2].release()
                continue
            break

        proc, sock, wlock = chosen
        try:
            sock.settimeout((timeout or 305.0) + 5.0)
            _send_frame(sock, json.dumps(header).encode("utf-8"))
            if stdin_bytes:
                sock.sendall(struct.pack(">I", len(stdin_bytes)))
                sock.sendall(stdin_bytes)
            else:
                sock.sendall(struct.pack(">I", 0))
            resp_bytes = _recv_frame(sock, _FASTPATH_MAX_FRAME)
            response = json.loads(resp_bytes.decode("utf-8"))
        except Exception as exc:  # worker became unusable
            logger.warning("fork fastpath worker failed: %s", exc)
            try:
                sock.close()
            except OSError:
                pass
            try:
                proc.kill()
            except OSError:
                pass
            with self._lock:
                self._drop(proc)
                # A worker that dies mid-request is replaced on the next
                # request; count it as a restart so observability matches the
                # resurrected-worker-prune path below.
                self.stats.record_worker_restart(1)
                self._bump_failure_locked()
            raise FastPathUnavailable("worker_unavailable") from exc
        else:
            with self._lock:
                self._record_success_locked()
            self.stats.record_hit()
        finally:
            wlock.release()
        return response

    def shutdown(self) -> None:
        self._stop_event.set()
        for proc, sock, _lock in self._workers:
            try:
                sock.close()
            except OSError:
                pass
            try:
                proc.kill()
            except OSError:
                pass
        self._workers = []
        self.stats.set_active(0)
        self.stats.write_snapshot(force=True)


_FORK_POOL = ForkServerPool()


def _try_fastpath_exec(
    conn: socket.socket,
    command: list[str],
    header: dict[str, Any],
    stdin_bytes: bytes,
) -> bool:
    """Route a ``python3 -c <code>`` exec to the ForkServer.

    Returns ``True`` when a response was sent (fastpath success or timeout),
    ``False`` when the caller should fall back to ``subprocess.Popen``.
    Records fast-path hits / fallback reasons for observability.
    """
    if not command or command[0] != "python3":
        _FORK_POOL.stats.record_fallback("not_eligible")
        return False
    # Only the exact ``python3 [-S] [-I] [-E] -c CODE`` shape is eligible;
    # anything else uses the normal path.
    exec_index = None
    for i, tok in enumerate(command[:-1]):
        if tok == "-c":
            exec_index = i
            break
    if exec_index is None or exec_index + 1 >= len(command):
        _FORK_POOL.stats.record_fallback("not_eligible")
        return False
    source = command[exec_index + 1]

    try:
        response = _FORK_POOL.submit(
            source,
            stdin_bytes,
            header.get("workdir"),
            header.get("env"),
            header.get("timeout"),
        )
    except FastPathUnavailable as exc:
        _FORK_POOL.stats.record_fallback(exc.reason)
        _FORK_POOL.stats.write_snapshot()
        return False
    _FORK_POOL.stats.write_snapshot()
    if "error" in response:
        _FORK_POOL.stats.record_fallback("worker_error")
        _send_response(
            conn,
            _exec_response(
                exit_code=1,
                stderr=response.get("stderr") or response["error"],
                started=False,
                error="fastpath_internal",
            ),
        )
        return True
    _send_response(
        conn,
        _exec_response(
            exit_code=int(response.get("exit_code", 1)),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
        ),
    )
    return True


def _fastpath_shutdown() -> None:
    _FORK_POOL.shutdown()


def _os_error_response(exc: OSError, fallback: str = "io_error") -> dict[str, Any]:
    """Build a JSON-friendly description of an ``OSError`` from a file op."""
    return {
        "v": PROTOCOL_VERSION,
        "ok": False,
        "error": fallback,
        "errno": exc.errno or 0,
        "stderr": exc.strerror or str(exc),
    }


def _handle_write_file(conn: socket.socket, header: dict[str, Any]) -> None:
    """Write a file on behalf of box-server, no child process involved.

    The daemon already runs inside the sandbox PID/mount/user namespaces
    with the policy uid/gid, Landlock ruleset, and seccomp filter applied,
    so doing this in-process is exactly equivalent to a sandboxed
    ``cat > path``: the same paths are reachable, the same uid owns the
    resulting file, and the same syscall filter is enforced. The win is
    that we avoid forking ``bash`` for every upload.
    """
    try:
        path = header.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("write_file request missing 'path'")
        content_size = int(header.get("content_size") or 0)
        if content_size < 0 or content_size > MAX_FILE_BYTES:
            raise ValueError(f"invalid content_size {content_size}")
        mkdir_parents = bool(header.get("mkdir_parents", True))
        mode = header.get("mode")
        if mode is not None and not isinstance(mode, int):
            raise ValueError("mode must be an integer")
    except ValueError as exc:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "bad_request",
                "stderr": str(exc),
            },
        )
        return

    try:
        content = _recv_exact(conn, content_size) if content_size else b""
    except ConnectionError as exc:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "bad_request",
                "stderr": f"truncated write_file payload: {exc}",
            },
        )
        return

    parent = os.path.dirname(path) or "/"
    if mkdir_parents and parent not in ("", "/"):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            _send_response(conn, _os_error_response(exc, "mkdir_failed"))
            return

    # Open with O_NOFOLLOW so a pre-existing symlink at ``path`` (which a
    # malicious user payload could have planted before the upload arrived)
    # cannot redirect the write to an attacker-chosen location. The link
    # would still be subject to Landlock, but refusing it outright keeps
    # the IPC fast path's behaviour aligned with what the previous
    # ``cat > $target`` pipeline produced.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, mode if mode is not None else 0o644)
    except OSError as exc:
        _send_response(conn, _os_error_response(exc, "open_failed"))
        return
    try:
        try:
            if content:
                view = memoryview(content)
                offset = 0
                while offset < len(view):
                    written = os.write(fd, view[offset:])
                    if written <= 0:
                        raise OSError(errno.EIO, "short write")
                    offset += written
        except OSError as exc:
            _send_response(conn, _os_error_response(exc, "write_failed"))
            return
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    _send_response(conn, {"v": PROTOCOL_VERSION, "ok": True})


def _handle_read_file(conn: socket.socket, header: dict[str, Any]) -> None:
    """Read a file in-process and stream it back as a single frame.

    Matches the safety properties of the previous ``base64 -w 0 -- $path``
    helper but without a ``bash`` cold start. The header is sent first
    with ``content_size``; the body frame follows so binary content
    survives intact (no base64 round-trip, no ``replace`` decoding).
    """
    path = header.get("path")
    if not isinstance(path, str) or not path:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "bad_request",
                "stderr": "read_file request missing 'path'",
            },
        )
        return

    try:
        if os.path.islink(path):
            _send_response(
                conn,
                {
                    "v": PROTOCOL_VERSION,
                    "ok": False,
                    "error": "is_symlink",
                    "errno": errno.ELOOP,
                    "stderr": f"refusing to read symlink {path!r}",
                },
            )
            return
        try:
            stat = os.stat(path, follow_symlinks=False)
        except FileNotFoundError as exc:
            _send_response(conn, _os_error_response(exc, "not_found"))
            return
        except IsADirectoryError as exc:
            _send_response(conn, _os_error_response(exc, "is_directory"))
            return
        if os.path.isdir(path):
            _send_response(
                conn,
                {
                    "v": PROTOCOL_VERSION,
                    "ok": False,
                    "error": "is_directory",
                    "errno": errno.EISDIR,
                    "stderr": f"{path!r} is a directory",
                },
            )
            return
        if stat.st_size > MAX_FILE_BYTES:
            _send_response(
                conn,
                {
                    "v": PROTOCOL_VERSION,
                    "ok": False,
                    "error": "too_large",
                    "stderr": (
                        f"file size {stat.st_size} exceeds limit "
                        f"{MAX_FILE_BYTES}"
                    ),
                },
            )
            return
        try:
            # ``mode`` is required by G.FIO.01 even though the kernel
            # ignores it without ``O_CREAT``; a placeholder of ``0o600``
            # documents the (would-be) least-privilege permission.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            _send_response(conn, _os_error_response(exc, "open_failed"))
            return
        try:
            chunks: list[bytes] = []
            remaining = stat.st_size
            while remaining > 0:
                chunk = os.read(fd, min(remaining, 1 << 20))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
    except OSError as exc:
        _send_response(conn, _os_error_response(exc, "read_failed"))
        return

    _send_response(
        conn,
        {
            "v": PROTOCOL_VERSION,
            "ok": True,
            "content_size": len(content),
        },
    )
    _send_frame(conn, content)


def _list_dir_entries(
    root: str,
    *,
    recursive: bool,
    max_depth: int | None,
    include_files: bool,
    include_dirs: bool,
) -> list[dict[str, Any]]:
    """Build the ``items`` payload for a list_dir response."""
    items: list[dict[str, Any]] = []
    pending: list[tuple[str, int]] = [(root, 0)]
    while pending:
        current, depth = pending.pop()
        try:
            iterator = os.scandir(current)
        except OSError:
            continue
        with iterator:
            for entry in iterator:
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel_depth = depth + 1
                if max_depth is not None and rel_depth > max_depth:
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if is_dir and recursive:
                    pending.append((entry.path, rel_depth))
                if is_dir and not include_dirs:
                    continue
                if not is_dir and not include_files:
                    continue
                items.append({
                    "name": entry.name,
                    "path": entry.path,
                    "size": 0 if is_dir else stat.st_size,
                    "is_directory": is_dir,
                    "modified_time": datetime.datetime.fromtimestamp(
                        stat.st_mtime,
                    ).isoformat(),
                    "type": (
                        None
                        if is_dir
                        else (os.path.splitext(entry.name)[1] or None)
                    ),
                })
    items.sort(key=lambda item: item["path"])
    return items


def _handle_list_dir(conn: socket.socket, header: dict[str, Any]) -> None:
    """List a directory tree from the daemon process."""
    path = header.get("path")
    if not isinstance(path, str) or not path:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "bad_request",
                "stderr": "list_dir request missing 'path'",
            },
        )
        return
    recursive = bool(header.get("recursive", False))
    raw_max_depth = header.get("max_depth")
    if raw_max_depth is None:
        max_depth: int | None = None
    elif isinstance(raw_max_depth, int) and raw_max_depth >= 0:
        max_depth = raw_max_depth
    else:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "bad_request",
                "stderr": "max_depth must be a non-negative integer",
            },
        )
        return
    include_files = bool(header.get("include_files", True))
    include_dirs = bool(header.get("include_dirs", True))

    if not os.path.exists(path):
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "not_found",
                "errno": errno.ENOENT,
                "stderr": f"path {path!r} does not exist",
            },
        )
        return
    if not os.path.isdir(path):
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "not_a_directory",
                "errno": errno.ENOTDIR,
                "stderr": f"path {path!r} is not a directory",
            },
        )
        return

    try:
        items = _list_dir_entries(
            path,
            recursive=recursive,
            max_depth=max_depth,
            include_files=include_files,
            include_dirs=include_dirs,
        )
    except OSError as exc:
        _send_response(conn, _os_error_response(exc, "list_failed"))
        return

    _send_response(
        conn,
        {
            "v": PROTOCOL_VERSION,
            "ok": True,
            "items": items,
        },
    )


@dataclass
class _BgJob:
    job_id: str
    command: list[str]
    proc: subprocess.Popen


_bg_jobs: dict[str, _BgJob | object] = {}
_bg_jobs_lock = threading.Lock()
_BG_JOB_RESERVED = object()


def _try_reserve_bg_job(job_id: str) -> bool:
    with _bg_jobs_lock:
        if job_id in _bg_jobs:
            return False
        _bg_jobs[job_id] = _BG_JOB_RESERVED
        return True


def _release_bg_job_reservation(job_id: str) -> None:
    with _bg_jobs_lock:
        if _bg_jobs.get(job_id) is _BG_JOB_RESERVED:
            del _bg_jobs[job_id]


def _commit_bg_job(job_id: str, job: _BgJob) -> None:
    with _bg_jobs_lock:
        _bg_jobs[job_id] = job


def _sync_bg_job(job: _BgJob) -> None:
    if job.proc.returncode is None:
        job.proc.poll()


def _bg_job_response(
    *,
    ok: bool,
    job_id: str,
    job: _BgJob | None = None,
    started: bool = True,
    error: str | None = None,
    stderr: str = "",
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "ok": ok,
        "job_id": job_id,
        "started": started,
    }
    if error:
        response["error"] = error
    if stderr:
        response["stderr"] = stderr
    if job is not None:
        _sync_bg_job(job)
        response["pid"] = job.proc.pid
        response["running"] = job.proc.returncode is None
        response["exit_code"] = job.proc.returncode
    return response


def _handle_exec_background(conn: socket.socket, header: dict[str, Any]) -> None:
    try:
        job_id = header.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("exec_background request missing 'job_id'")
        job_id = job_id.strip()

        command = header.get("command")
        if not isinstance(command, list) or not command:
            raise ValueError("exec_background request missing 'command'")
        command = _stringify_command(command)

        env_override = _normalize_env(header.get("env"))
        workdir = header.get("workdir")
        if workdir is not None and not isinstance(workdir, str):
            raise ValueError("workdir must be a string")
        stdin_size = int(header.get("stdin_size") or 0)
        if stdin_size < 0 or stdin_size > MAX_STDIN_BYTES:
            raise ValueError(f"invalid stdin_size {stdin_size}")

        stdin_bytes = _recv_exact(conn, stdin_size) if stdin_size else b""

        if not _try_reserve_bg_job(job_id):
            _send_response(
                conn,
                _bg_job_response(
                    ok=False,
                    job_id=job_id,
                    started=False,
                    error="job_exists",
                    stderr=f"background job {job_id!r} already exists",
                ),
            )
            return

        reserved = True
        try:
            merged_env = dict(os.environ)
            merged_env.pop(LISTENER_FD_ENV, None)
            if env_override is not None:
                merged_env.update(env_override)

            # Background jobs discard stdout/stderr; use ``exec`` when output
            # must be captured.
            proc_kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE if stdin_size else subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "env": merged_env,
                "close_fds": True,
                "start_new_session": True,
            }
            if workdir:
                proc_kwargs["cwd"] = workdir

            try:
                proc = subprocess.Popen(command, **proc_kwargs)
                if stdin_size and proc.stdin is not None:
                    proc.stdin.write(stdin_bytes)
                    proc.stdin.close()
            except OSError as exc:
                _send_response(
                    conn,
                    _bg_job_response(
                        ok=False,
                        job_id=job_id,
                        started=False,
                        error="spawn_failed",
                        stderr=f"failed to spawn background command: {exc}",
                    ),
                )
                return

            job = _BgJob(
                job_id=job_id,
                command=command,
                proc=proc,
            )
            _commit_bg_job(job_id, job)
            reserved = False

            _send_response(conn, _bg_job_response(ok=True, job_id=job_id, job=job))
        finally:
            if reserved:
                _release_bg_job_reservation(job_id)
    except (ValueError, ConnectionError) as exc:
        _send_response(
            conn,
            _bg_job_response(
                ok=False,
                job_id=str(header.get("job_id") or ""),
                started=False,
                error="bad_request",
                stderr=str(exc),
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unhandled error while handling exec_background request")
        _send_response(
            conn,
            _bg_job_response(
                ok=False,
                job_id=str(header.get("job_id") or ""),
                started=False,
                error="internal",
                stderr=f"daemon internal error: {exc}",
            ),
        )


def _handle_bg_status(conn: socket.socket, header: dict[str, Any]) -> None:
    job_id = header.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        _send_response(
            conn,
            _bg_job_response(
                ok=False,
                job_id="",
                started=False,
                error="bad_request",
                stderr="bg_status request missing 'job_id'",
            ),
        )
        return
    job_id = job_id.strip()
    with _bg_jobs_lock:
        job = _bg_jobs.get(job_id)
    if job is None or job is _BG_JOB_RESERVED:
        _send_response(
            conn,
            _bg_job_response(
                ok=False,
                job_id=job_id,
                started=False,
                error="not_found",
                stderr=f"background job {job_id!r} not found",
            ),
        )
        return
    _send_response(conn, _bg_job_response(ok=True, job_id=job_id, job=job))


def _handle_bg_kill(conn: socket.socket, header: dict[str, Any]) -> None:
    job_id = header.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "bad_request",
                "stderr": "bg_kill request missing 'job_id'",
            },
        )
        return
    job_id = job_id.strip()
    signum = header.get("signum", 15)
    if not isinstance(signum, int):
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "error": "bad_request",
                "stderr": "signum must be an integer",
            },
        )
        return

    with _bg_jobs_lock:
        job = _bg_jobs.get(job_id)
    if job is None or job is _BG_JOB_RESERVED:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": False,
                "job_id": job_id,
                "killed": False,
                "reason": "not_found",
            },
        )
        return

    _sync_bg_job(job)
    if job.proc.returncode is not None:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": True,
                "job_id": job_id,
                "killed": False,
                "reason": "already_exited",
                "exit_code": job.proc.returncode,
            },
        )
        return

    try:
        job.proc.send_signal(signum)
    except ProcessLookupError:
        _sync_bg_job(job)
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": True,
                "job_id": job_id,
                "killed": False,
                "reason": "already_exited",
                "exit_code": job.proc.returncode,
            },
        )
        return
    except PermissionError:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": True,
                "job_id": job_id,
                "killed": False,
                "reason": "permission_denied",
                "exit_code": job.proc.returncode,
            },
        )
        return
    except OSError:
        _send_response(
            conn,
            {
                "v": PROTOCOL_VERSION,
                "ok": True,
                "job_id": job_id,
                "killed": False,
                "reason": "permission_denied",
                "exit_code": job.proc.returncode,
            },
        )
        return

    _sync_bg_job(job)
    _send_response(
        conn,
        {
            "v": PROTOCOL_VERSION,
            "ok": True,
            "job_id": job_id,
            "killed": True,
            "reason": "ok",
            "exit_code": job.proc.returncode,
        },
    )


def _handle_connection(conn: socket.socket, state: DaemonState) -> None:
    state.begin_request()
    try:
        try:
            header_bytes = _recv_frame(conn, MAX_HEADER_BYTES)
        except OSError:
            # ``OSError`` already covers ``ConnectionError`` (G.ERR.09).
            return
        try:
            header = json.loads(header_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _send_response(
                conn,
                {
                    "v": PROTOCOL_VERSION,
                    "ok": False,
                    "error": "bad_request",
                    "stderr": f"invalid request header: {exc}",
                },
            )
            return

        request_type = header.get("type")
        if request_type == REQUEST_TYPE_EXEC:
            _handle_exec(conn, header, state)
        elif request_type == REQUEST_TYPE_SHUTDOWN:
            _send_response(
                conn,
                {"v": PROTOCOL_VERSION, "ok": True, "type": "shutdown_ack"},
            )
            state.shutdown_event.set()
        elif request_type == REQUEST_TYPE_WRITE_FILE:
            _handle_write_file(conn, header)
        elif request_type == REQUEST_TYPE_READ_FILE:
            _handle_read_file(conn, header)
        elif request_type == REQUEST_TYPE_LIST_DIR:
            _handle_list_dir(conn, header)
        elif request_type == REQUEST_TYPE_EXEC_BACKGROUND:
            _handle_exec_background(conn, header)
        elif request_type == REQUEST_TYPE_BG_STATUS:
            _handle_bg_status(conn, header)
        elif request_type == REQUEST_TYPE_BG_KILL:
            _handle_bg_kill(conn, header)
        else:
            _send_response(
                conn,
                {
                    "v": PROTOCOL_VERSION,
                    "ok": False,
                    "error": "unknown_request_type",
                    "stderr": f"unknown request type: {request_type!r}",
                },
            )
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass
        state.end_request()


@contextlib.contextmanager
def _adopted_listener() -> Iterator[socket.socket]:
    """Recover the host-bound listener fd that box-server passed in.

    Box-server creates the Unix listening socket on its own filesystem and
    passes the resulting fd to bubblewrap via ``subprocess.Popen(pass_fds=...)``.
    Bubblewrap's user command path never closes arbitrary inherited fds, so
    by the time the daemon runs, the fd is already in our process and
    ready to ``accept()``. We wrap it in a Python socket object so the
    standard library accept loop works without re-binding (which Landlock
    would forbid, since the launcher applies the policy filesystem ruleset
    before exec'ing the daemon).

    Exposed as a context manager so resource acquisition (wrapping the
    pre-bound fd in a Python socket object) and release (closing that
    socket) live in the same lexical scope, satisfying the resource-pair
    requirement (G.PRM.03).
    """
    raw = os.environ.get(LISTENER_FD_ENV)
    if raw is None:
        raise RuntimeError(
            f"{LISTENER_FD_ENV} is not set; box-server must hand the daemon "
            "a pre-bound control listener fd",
        )
    try:
        fd = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"{LISTENER_FD_ENV}={raw!r} is not an integer",
        ) from exc

    listener = socket.socket(
        family=socket.AF_UNIX,
        type=socket.SOCK_STREAM,
        fileno=fd,
    )
    try:
        listener.settimeout(ACCEPT_TIMEOUT_SECONDS)
        yield listener
    finally:
        try:
            listener.close()
        except OSError:
            pass


def _accept_loop(listener: socket.socket, state: DaemonState) -> None:
    while not state.shutdown_event.is_set():
        try:
            conn, _ = listener.accept()
        except socket.timeout:
            continue
        except OSError as exc:
            if exc.errno == errno.EBADF:
                return
            logger.warning("accept() failed: %s", exc)
            continue
        thread = threading.Thread(
            target=_handle_connection,
            args=(conn, state),
            name="jiuwenbox-daemon-worker",
            daemon=True,
        )
        thread.start()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    state = DaemonState()
    if _fastpath_enabled():
        # Idle-recycle background thread; runs only when the feature is on.
        _FORK_POOL.start_reaper()
    try:
        with _adopted_listener() as listener:
            logger.info(
                "sandbox daemon adopted listener fd; entering accept loop",
            )
            try:
                _accept_loop(listener, state)
            finally:
                # Give in-flight requests a brief window to complete cleanly
                # before the context manager closes the listener.
                state.wait_drain(SHUTDOWN_DRAIN_TIMEOUT_SECONDS)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    _fastpath_shutdown()
    logger.info("sandbox daemon exited cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
