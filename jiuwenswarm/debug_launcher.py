# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""One-command local debug launcher for JiuWenSwarm.

``jiuwenswarm-start debug`` chains the whole "rebuild everything and run it"
loop that developers otherwise type by hand:

1. ``npm install`` in ``jiuwenswarm/channels/web/frontend``
2. ``npm run build`` to regenerate ``frontend/dist``
3. ``uv sync`` at the repository root
4. spawn ``jiuwenswarm-start all`` detached in the background, with stdout and
   stderr redirected to a timestamped ``swarm-<YYYYmmdd-HHMMSS>.log``

Step 4 records the background PID in a small JSON state file so that
``jiuwenswarm-stop`` can terminate exactly that service later. The service is
spawned into its own process group / session, and stopping signals the whole
group - see ``_stop_process_tree`` for why the leader PID alone is not enough.

The background child is ``python -m jiuwenswarm.start_services all`` rather
than a nested ``uv run``: it keeps the service a single direct child, so no
extra ``uv`` process sits between the recorded PID and the launcher that owns
the app/web subprocesses. Inside ``uv run`` the two forms resolve to the same
interpreter anyway.

Debug mode only makes sense from a source checkout - it builds the frontend and
syncs the project dependencies, neither of which exists in a package install.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jiuwenswarm.instance_manager import is_process_alive, stop_process_by_pid

# Package source root: <repo>/jiuwenswarm in source mode.
PACKAGE_DIR = Path(__file__).resolve().parent

# Repository root, i.e. the directory holding pyproject.toml.
#
# Deliberately NOT ``get_root_dir()``: that returns the *runtime data* root,
# which is ``~/.jiuwenswarm`` as soon as the user workspace has been
# initialized - even when running from a checkout. Debug mode needs the source
# tree instead, because that is where pyproject.toml (for ``uv sync``) and the
# frontend project (for ``npm install`` / ``npm run build``) live.
REPO_ROOT = PACKAGE_DIR.parent

# Frontend project root (contains package.json).
WEB_DEV_DIR = PACKAGE_DIR / "channels" / "web" / "frontend"

# Directory holding the debug log files and the debug state file.
DEBUG_LOG_DIRNAME = "logs"

# State file recording the background service started by ``debug`` mode.
DEBUG_STATE_FILENAME = "debug_service.json"

# Prefix / suffix of the generated log file: swarm-<timestamp>.log
DEBUG_LOG_PREFIX = "swarm"
DEBUG_LOG_SUFFIX = ".log"

# Seconds to wait after spawning before declaring the background service up.
_SPAWN_SETTLE_SECONDS = 2.0

# Default grace period given to the background service on stop.
DEFAULT_STOP_TIMEOUT = 15.0


@dataclass(frozen=True)
class DebugState:
    """Bookkeeping for the background service started by ``debug`` mode.

    Attributes:
        pid: Process ID of the detached ``jiuwenswarm-start all`` process.
        started_at: Unix timestamp of the spawn.
        log_file: Absolute path of the redirected stdout/stderr log.
        command: Argv of the spawned process, kept for PID-reuse verification.
        cwd: Working directory the process was spawned in.
    """

    pid: int
    started_at: float
    log_file: str
    command: list[str]
    cwd: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation of this state."""
        return {
            "pid": self.pid,
            "started_at": self.started_at,
            "log_file": self.log_file,
            "command": list(self.command),
            "cwd": self.cwd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> DebugState | None:
        """Build a DebugState from a parsed state file, or None if malformed.

        Args:
            data: Parsed JSON object read from the state file.

        Returns:
            The reconstructed state, or None when required fields are missing
            or have the wrong type (a hand-edited or truncated file).
        """
        raw_pid = data.get("pid")
        if not isinstance(raw_pid, int) or raw_pid <= 0:
            return None
        raw_started = data.get("started_at")
        started_at = float(raw_started) if isinstance(raw_started, (int, float)) else 0.0
        raw_command = data.get("command")
        command = [str(item) for item in raw_command] if isinstance(raw_command, list) else []
        return cls(
            pid=raw_pid,
            started_at=started_at,
            log_file=str(data.get("log_file") or ""),
            command=command,
            cwd=str(data.get("cwd") or ""),
        )


def get_debug_log_dir() -> Path:
    """Return the directory that holds debug logs and the debug state file."""
    return REPO_ROOT / DEBUG_LOG_DIRNAME


def get_debug_state_path() -> Path:
    """Return the path of the debug service state file."""
    return get_debug_log_dir() / DEBUG_STATE_FILENAME


def build_debug_log_path(now: datetime | None = None) -> Path:
    """Build a timestamped log path, avoiding collisions within one second.

    Args:
        now: Timestamp used to build the suffix, defaults to the current time.

    Returns:
        Path like ``<root>/logs/swarm-20250805-143000.log``. If that name is
        already taken (two launches in the same second), a ``-2``, ``-3`` ...
        discriminator is appended.
    """
    if now is None:
        now = datetime.now()
    log_dir = get_debug_log_dir()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    candidate = log_dir / f"{DEBUG_LOG_PREFIX}-{stamp}{DEBUG_LOG_SUFFIX}"
    counter = 2
    while candidate.exists():
        candidate = log_dir / f"{DEBUG_LOG_PREFIX}-{stamp}-{counter}{DEBUG_LOG_SUFFIX}"
        counter += 1
    return candidate


def read_debug_state() -> DebugState | None:
    """Read the debug service state file.

    Returns:
        The recorded state, or None when the file is absent or unreadable.
    """
    state_path = get_debug_state_path()
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return DebugState.from_dict(data)


def write_debug_state(state: DebugState) -> None:
    """Write the debug service state file atomically.

    Args:
        state: State to persist.
    """
    state_path = get_debug_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    # Windows rename() fails when the destination exists.
    if state_path.exists():
        state_path.unlink()
    temp_path.rename(state_path)


def clear_debug_state() -> bool:
    """Delete the debug service state file.

    Returns:
        True if a file was removed, False if there was nothing to remove.
    """
    state_path = get_debug_state_path()
    if not state_path.exists():
        return False
    try:
        state_path.unlink()
    except OSError as exc:
        logging.info(f"[debug] WARNING: failed to remove {state_path}: {exc}")
        return False
    return True


def is_debug_service_alive(state: DebugState) -> bool:
    """Check whether the recorded PID is still the process we started.

    A bare liveness check is not enough: the OS recycles PIDs, so a stale state
    file could point at an unrelated process that ``jiuwenswarm-stop`` would
    then happily kill. When psutil is available we additionally require the
    process creation time to match the recorded ``started_at``.

    Args:
        state: Recorded debug service state.

    Returns:
        True when the process is alive and plausibly the one we spawned.
    """
    if not is_process_alive(state.pid):
        return False
    if state.started_at <= 0:
        return True

    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a hard dependency
        return True

    try:
        proc = psutil.Process(state.pid)
        create_time = proc.create_time()
    except Exception:
        # Access denied / vanished: fall back to the liveness result.
        return True

    # Spawn timestamp is taken right after Popen returns, so it trails the
    # kernel's create_time by milliseconds; allow a generous window.
    return abs(create_time - state.started_at) <= 10.0


def _resolve_npm_command(args: list[str]) -> list[str] | None:
    """Build a platform-appropriate npm command line.

    Args:
        args: npm arguments, e.g. ``["install"]`` or ``["run", "build"]``.

    Returns:
        The full argv to execute, or None when npm is not on PATH.
    """
    if platform.system().lower() == "windows":
        # npm ships as npm.cmd on Windows; go through cmd /c so the shim runs.
        if shutil.which("npm") is None and shutil.which("npm.cmd") is None:
            return None
        return ["cmd", "/c", "npm", *args]

    npm_path = shutil.which("npm")
    if npm_path is None:
        return None
    return [npm_path, *args]


def _run_step(label: str, cmd: list[str], cwd: Path) -> int:
    """Run one preparation step in the foreground, streaming its output.

    Args:
        label: Human-readable step name used in log lines.
        cmd: Argv to execute.
        cwd: Working directory for the command.

    Returns:
        The command's exit code, or 1 when the executable could not be run.
    """
    logging.info(f"[debug] ==> {label}: {' '.join(cmd)} (cwd={cwd})")
    try:
        completed = subprocess.run(cmd, cwd=str(cwd), check=False)
    except OSError as exc:
        logging.info(f"[debug] ERROR: {label} failed to start: {exc}")
        return 1

    if completed.returncode != 0:
        logging.info(f"[debug] ERROR: {label} exited with code {completed.returncode}")
    else:
        logging.info(f"[debug] ✓ {label} done")
    return completed.returncode


def _check_source_checkout() -> int | None:
    """Verify the layout debug mode needs (frontend project + pyproject.toml).

    Returns:
        None when the checkout looks usable, 1 otherwise (message printed).
    """
    package_json = WEB_DEV_DIR / "package.json"
    if not package_json.exists():
        logging.info(f"[debug] ERROR: frontend project not found: {package_json}")
        logging.info(
            "[debug] debug mode rebuilds the web frontend and therefore requires a "
            "source checkout; use 'jiuwenswarm-start all' for a package installation."
        )
        return 1

    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        logging.info(f"[debug] ERROR: pyproject.toml not found: {pyproject}")
        logging.info(
            "[debug] debug mode runs 'uv sync' at the repository root; run it from a "
            "source checkout."
        )
        return 1

    return None


def _check_no_running_debug_service() -> int | None:
    """Refuse to start a second debug service on top of a live one.

    Returns:
        None when it is safe to start, 1 when one is already running.
    """
    state = read_debug_state()
    if state is None:
        return None

    if is_debug_service_alive(state):
        logging.info(
            f"[debug] ERROR: a debug service is already running (PID={state.pid})."
        )
        if state.log_file:
            logging.info(f"[debug] Log: {state.log_file}")
        logging.info("[debug] Run 'jiuwenswarm-stop' first, then start again.")
        return 1

    # Stale record from a crashed or externally killed run: drop it and move on.
    logging.info(
        f"[debug] Clearing stale debug state (PID={state.pid} is no longer running)."
    )
    clear_debug_state()
    return None


def _spawn_background_service(log_path: Path) -> DebugState | None:
    """Spawn ``jiuwenswarm-start all`` detached with output going to log_path.

    Args:
        log_path: File that receives the child's stdout and stderr.

    Returns:
        The recorded state on success, None when the spawn failed.
    """
    cmd = [sys.executable, "-m", "jiuwenswarm.start_services", "all"]

    # Detach so the service outlives this launcher process. On POSIX
    # start_new_session also makes the child a process-group leader, which is
    # what lets ``jiuwenswarm-stop`` signal the whole tree at once - see
    # _stop_process_tree.
    popen_kwargs: dict[str, object] = {}
    if platform.system().lower() == "windows":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.info(f"[debug] ==> starting background service: {' '.join(cmd)}")
    logging.info(f"[debug] Log file: {log_path}")

    env = os.environ.copy()
    # Child services are headless here; nothing may prompt on the shared stdin.
    try:
        with log_path.open("ab") as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                **popen_kwargs,
            )
    except OSError as exc:
        logging.info(f"[debug] ERROR: failed to spawn background service: {exc}")
        return None

    return DebugState(
        pid=proc.pid,
        started_at=time.time(),
        log_file=str(log_path),
        command=cmd,
        cwd=str(REPO_ROOT),
    )


def run_debug() -> int:
    """Run the full debug pipeline: npm install, npm build, uv sync, background start.

    Returns:
        Exit code: 0 when the background service is up, non-zero otherwise.
    """
    error = _check_source_checkout()
    if error is not None:
        return error

    error = _check_no_running_debug_service()
    if error is not None:
        return error

    npm_install = _resolve_npm_command(["install"])
    if npm_install is None:
        logging.info("[debug] ERROR: 'npm' not found on PATH.")
        logging.info("[debug] Install Node.js (which ships npm), then retry.")
        return 1

    code = _run_step("npm install", npm_install, WEB_DEV_DIR)
    if code != 0:
        return code

    # _resolve_npm_command already proved npm resolves; reuse it for the build.
    npm_build = _resolve_npm_command(["run", "build"])
    if npm_build is None:  # pragma: no cover - PATH changed mid-run
        logging.info("[debug] ERROR: 'npm' disappeared from PATH.")
        return 1

    code = _run_step("npm run build", npm_build, WEB_DEV_DIR)
    if code != 0:
        return code

    uv_path = shutil.which("uv")
    if uv_path is None:
        logging.info("[debug] ERROR: 'uv' not found on PATH.")
        logging.info("[debug] Install uv (https://docs.astral.sh/uv/), then retry.")
        return 1

    code = _run_step("uv sync", [uv_path, "sync"], REPO_ROOT)
    if code != 0:
        return code

    log_path = build_debug_log_path()
    state = _spawn_background_service(log_path)
    if state is None:
        return 1

    # Give the child a moment so an immediate crash (bad env, port bind) is
    # reported here instead of silently leaving a dead PID in the state file.
    time.sleep(_SPAWN_SETTLE_SECONDS)
    if not is_process_alive(state.pid):
        logging.info(
            f"[debug] ERROR: background service exited immediately (PID={state.pid})."
        )
        logging.info(f"[debug] Check the log for details: {log_path}")
        # No state file is written, so jiuwenswarm-stop could never reach any
        # sub-service the dying launcher left behind. Sweep its group now - the
        # leader is gone, so its group ID has to be taken on faith from the PID
        # we just spawned.
        _stop_process_tree(state.pid, timeout=5.0, assume_group_leader=True)
        return 1

    write_debug_state(state)

    logging.info("")
    logging.info("=" * 64)
    logging.info("  调试服务已在后台启动")
    logging.info(f"  PID:  {state.pid}")
    logging.info(f"  日志: {log_path}")
    logging.info(f"  跟踪: tail -f {log_path}")
    logging.info("  停止: jiuwenswarm-stop")
    logging.info("=" * 64)
    logging.info("")
    return 0


def _process_group_alive(pgid: int) -> bool:
    """Return True while at least one process remains in the given group."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Group exists but is owned by someone else; treat it as alive.
        return True
    except OSError:
        return False
    return True


def _stop_process_tree(
    pid: int,
    timeout: float = DEFAULT_STOP_TIMEOUT,
    *,
    assume_group_leader: bool = False,
) -> bool:
    """Stop the background service together with every process it spawned.

    Signalling only ``pid`` is not enough. ``jiuwenswarm.app`` (a child of the
    launcher) does not map SIGTERM to a graceful shutdown, so it dies without
    terminating the agent server and gateway it started - those would be left
    holding their ports. ``debug`` mode therefore spawns the service into its
    own process group (``start_new_session``) and this function signals the
    whole group, which reaches every descendant directly.

    Args:
        pid: PID of the detached service, also its process-group ID on POSIX.
        timeout: Seconds to wait for a graceful group shutdown before SIGKILL.
        assume_group_leader: Treat ``pid`` as the group ID without asking the
            kernel. Needed when the leader has already exited but its children
            may still be running - ``getpgid`` on a dead PID tells us nothing,
            yet the group ID equals the PID by construction of the spawn. Only
            pass this for a PID this process spawned itself.

    Returns:
        True when nothing from the tree is left running.
    """
    if platform.system().lower() == "windows":
        # taskkill /T /F (used by stop_process_by_pid) already walks the tree
        # the OS records for this PID, so no separate group handling is needed.
        return stop_process_by_pid(pid, timeout=timeout)

    import signal

    if assume_group_leader:
        pgid = pid
    else:
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return True
        except OSError:
            return stop_process_by_pid(pid, timeout=timeout)

        if pgid != pid:
            # Not the session leader we spawned (state file predates group-based
            # stopping, or the process re-parented). Signalling this group could
            # hit unrelated processes, so fall back to the single-PID path.
            return stop_process_by_pid(pid, timeout=timeout)

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError as exc:
        logging.info(f"[debug] WARNING: SIGTERM to process group {pgid} failed: {exc}")
        return stop_process_by_pid(pid, timeout=timeout)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _process_group_alive(pgid):
            return True
        time.sleep(0.3)

    logging.info(
        f"[debug] Process group {pgid} did not exit within {timeout}s; sending SIGKILL."
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass

    # Give the kernel a moment to reap before reporting the result.
    kill_deadline = time.time() + 3.0
    while time.time() < kill_deadline:
        if not _process_group_alive(pgid):
            return True
        time.sleep(0.2)

    return not _process_group_alive(pgid)


def stop_debug_service(timeout: float = DEFAULT_STOP_TIMEOUT) -> int:
    """Stop the background service recorded by ``jiuwenswarm-start debug``.

    Args:
        timeout: Seconds to wait for a graceful shutdown before giving up.

    Returns:
        Exit code: 0 when nothing is left running, 1 on failure.
    """
    state = read_debug_state()
    if state is None:
        logging.info("[debug] No debug service recorded; nothing to stop.")
        logging.info(
            "[debug] To stop a regular instance use "
            "'jiuwenswarm-start --stop <name>'."
        )
        return 0

    if not is_debug_service_alive(state):
        logging.info(
            f"[debug] Debug service (PID={state.pid}) is no longer running; "
            "clearing state."
        )
        clear_debug_state()
        return 0

    logging.info(f"[debug] Stopping debug service (PID={state.pid})...")
    stopped = _stop_process_tree(state.pid, timeout=timeout)
    if not stopped or is_process_alive(state.pid):
        logging.info(f"[debug] ERROR: failed to stop debug service (PID={state.pid}).")
        logging.info("[debug] Kill it manually, then rerun 'jiuwenswarm-stop'.")
        return 1

    clear_debug_state()
    logging.info("[debug] Debug service stopped.")
    if state.log_file:
        logging.info(f"[debug] Log kept at: {state.log_file}")
    return 0


def _parse_stop_args(argv: list[str] | None = None) -> float:
    """Parse ``jiuwenswarm-stop`` arguments.

    Args:
        argv: Argument list to parse, defaults to ``sys.argv[1:]``.

    Returns:
        The shutdown timeout in seconds.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="jiuwenswarm-stop",
        description=(
            "Stop the background debug service started by "
            "'jiuwenswarm-start debug'."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_STOP_TIMEOUT,
        metavar="<seconds>",
        help=f"Seconds to wait for graceful shutdown (default: {DEFAULT_STOP_TIMEOUT}).",
    )
    args = parser.parse_args(argv)
    return args.timeout


def main() -> None:
    """CLI entry point for ``jiuwenswarm-stop``."""
    # Same rationale as start_services.main(): configure logging inside the
    # entry point so logging.info output actually reaches the terminal, without
    # imposing import-time side effects on the root logger.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    timeout = _parse_stop_args()
    raise SystemExit(stop_debug_service(timeout))


if __name__ == "__main__":
    main()
