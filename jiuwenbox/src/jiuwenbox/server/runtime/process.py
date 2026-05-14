# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Process-based runtime adapter (bare-metal mode).

Spawns bubblewrap directly for each sandbox lifecycle process and for each
``exec`` request. The previous design wrapped every spawn with a Python
``box-supervisor`` middleman (``python3 -m jiuwenbox.supervisor.main``); for
hot-path workloads (uploads, exec, listings, downloads) that added ~150 ms of
Python interpreter cold start plus YAML/Pydantic policy parsing per call.

This adapter performs the equivalent setup in-process and reuses the
expensive artifacts (seccomp BPF program, encoded Landlock payload, copies of
the in-sandbox launcher scripts) for the lifetime of the sandbox while
preserving the same security guarantees: bubblewrap still applies all
namespace/mount/seccomp/Landlock isolation, the sandbox still runs through
the dedicated launcher script, and the seccomp memfd still flows through
``pass_fds`` so it cannot be observed by sandboxed code.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import errno
import grp
import json
import logging
import os
import pwd
import shutil
import signal
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.models.policy import NetworkMode, SecurityPolicy
from jiuwenbox.models.sandbox import BackgroundExecResult, ExecResult
from jiuwenbox.server.runtime.base import (
    RuntimeAdapter,
    RuntimeExecRequest,
    RuntimeFileOpResult,
)
from jiuwenbox.server.workspace import SANDBOX_WORKSPACE, JIUWENBOX_HOME
from jiuwenbox.supervisor import network as network_module
from jiuwenbox.supervisor.bwrap import BwrapConfig
from jiuwenbox.supervisor.daemon_ipc import (
    LISTENER_FD_ENV,
    MAX_FILE_BYTES,
    MAX_HEADER_BYTES,
    REQUEST_TYPE_EXEC,
    REQUEST_TYPE_LIST_DIR,
    REQUEST_TYPE_READ_FILE,
    REQUEST_TYPE_SHUTDOWN,
    REQUEST_TYPE_WRITE_FILE,
    SANDBOX_CONTROL_SOCKET_NAME,
    SANDBOX_DAEMON_COMMAND,
    SANDBOX_DAEMON_SANDBOX_PATH,
    SANDBOX_LAUNCHER_PATH,
    SANDBOX_RESERVED_DIR,
    encode_request,
    recv_frame,
    send_frame,
)
from jiuwenbox.supervisor.landlock import encode_landlock_payload
from jiuwenbox.supervisor.seccomp import build_seccomp_filter

configure_logging()
logger = logging.getLogger(__name__)
RUNTIME_SANDBOX_ENV = "JIUWENBOX_SANDBOX_ENV"
RUNTIME_SANDBOX_WORKDIR = "JIUWENBOX_SANDBOX_WORKDIR"
RUNTIME_POLICY_BINDS = "JIUWENBOX_POLICY_BINDS"
SERVER_PROTECT_PORTS_ENV = "JIUWENBOX_SERVER_PROTECT_PORTS"
DEFAULT_SERVER_PROTECT_PORTS = (8321,)

_SUPERVISOR_DIR = Path(__file__).resolve().parents[2] / "supervisor"
LANDLOCK_LAUNCHER_SOURCE = _SUPERVISOR_DIR / "landlock_launcher.py"
SANDBOX_DAEMON_SOURCE = _SUPERVISOR_DIR / "sandbox_daemon.py"
# Read the launcher and daemon source once at module load so we do not pay
# the I/O cost on every sandbox creation; bytes are immutable so sharing is
# safe across sandboxes.
_LANDLOCK_LAUNCHER_BYTES = LANDLOCK_LAUNCHER_SOURCE.read_bytes()
_SANDBOX_DAEMON_BYTES = SANDBOX_DAEMON_SOURCE.read_bytes()
PYTHON_EXECUTABLE = "python3"

# Per-sandbox control socket: box-server ``bind()``s a Unix socket on its
# own host filesystem inside a per-sandbox control directory, then passes
# the listener fd into bubblewrap via ``subprocess.Popen(pass_fds=...)``.
# Bubblewrap forks twice (monitor → intermediate → user command) but the
# user command path never closes arbitrary inherited fds, and Python's
# ``pass_fds`` clears CLOEXEC, so the listener fd survives all the way to
# the daemon. The daemon recovers the fd number from ``LISTENER_FD_ENV``
# and ``accept()``s on it. Because the socket file never appears under any
# sandbox-visible path, user code spawned by the daemon cannot reach the
# listener (and the daemon's ``subprocess.Popen`` calls run with
# ``close_fds=True`` so the inherited listener fd is not exposed to
# children either).
DAEMON_CONNECT_TIMEOUT_SECONDS = 2.0
DAEMON_SHUTDOWN_TIMEOUT_SECONDS = 3.0
DAEMON_STARTUP_GRACE_SECONDS = 0.3
DAEMON_MAX_RESPONSE_BYTES = 256 * 1024 * 1024
# File ops (upload/download/list) are CPU-cheap on the daemon side - it
# is just an open/read/write or scandir call. Cap the IPC roundtrip at a
# short upper bound so a wedged daemon does not stall HTTP requests.
DAEMON_FILE_OP_TIMEOUT_SECONDS = 30.0

# OS-level errors that mean the daemon is actually gone or its control
# socket is permanently broken. When we see one of these, ``_daemon_socket_ready``
# is flipped to ``False`` so subsequent calls take the slow legacy
# ``bash`` / ``python3`` fallback path instead of repeatedly re-trying a
# dead socket.
FATAL_DAEMON_ERRNOS: frozenset[int] = frozenset(
    (
        errno.ECONNREFUSED,    # nothing listening → daemon crashed
        errno.ECONNRESET,      # peer (daemon) closed mid-stream
        errno.ENOENT,          # listener path vanished
        errno.EPIPE,           # writing to a closed pipe/socket
        errno.ETIMEDOUT,       # daemon never responded → hung
        errno.EBADF,           # our fd was already closed → dead session
    ),
)

# OS-level errors that are *recoverable* - they describe a transient
# resource shortage on this specific call (host out of fds, fork queue
# full, signal interrupt, ...) but say nothing about whether the daemon
# itself is healthy. Reporting them to the caller as ``transport_failure``
# without flipping ``_daemon_socket_ready`` lets the next request try the
# fast path again. The earlier version of this fix lumped them into the
# fatal set, which caused a single ``EAGAIN`` / ``EBUSY`` / ``EINTR`` to
# permanently demote a sandbox to the bash+base64 path - sandbox-count=8
# regressed from ~217 ms back to ~919 ms because every later call paid the
# python cold-start tax.
RECOVERABLE_DAEMON_ERRNOS: frozenset[int] = frozenset(
    (
        errno.EAGAIN,
        errno.EMFILE,
        errno.ENFILE,
        errno.ENOMEM,
        errno.ENOBUFS,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EBUSY,
        errno.EINTR,
        # Some platforms expose ``EWOULDBLOCK`` as a distinct value from
        # ``EAGAIN``; on Linux they are equal so the frozenset deduplicates.
        errno.EWOULDBLOCK,
    ),
)

# Convenience union for any errno we should *not* let bubble out of an
# IPC roundtrip as an unhandled OSError (which would otherwise crash the
# route handler and surface as ``Server disconnected``).
TRANSIENT_DAEMON_ERRNOS: frozenset[int] = FATAL_DAEMON_ERRNOS | RECOVERABLE_DAEMON_ERRNOS


@dataclasses.dataclass(frozen=True)
class _DaemonExecCall:
    """Inputs to one IPC ``exec`` request.

    Bundled into a single value object so the per-call worker thread
    only takes one positional argument (G.FNM.03 keeps the signature at
    or below five arguments).
    """

    socket_path: Path
    command: list[str]
    env: dict[str, str] | None
    workdir: str | None
    stdin_bytes: bytes | None
    timeout: float | None


@dataclasses.dataclass(frozen=True)
class _DaemonListDirCall:
    """Inputs to one IPC ``list_dir`` request (see ``_DaemonExecCall``)."""

    socket_path: Path
    sandbox_path: str
    recursive: bool
    max_depth: int | None
    include_files: bool
    include_dirs: bool


# Admission control for ``exec``. ``exec`` is the one operation that
# spawns a fresh ``python3`` (or other) child inside the sandbox, which
# is overwhelmingly CPU-bound (interpreter cold start + imports + user
# script). Allowing more concurrent ``exec`` calls than the box has
# usable CPUs causes classic throughput collapse: TLB churn, L3-cache
# eviction across competing python interpreters, and per-fork mmap_sem
# contention in the kernel. Empirically each unit of oversubscription
# beyond 1.0 multiplies per-call latency by roughly 2-3x because of
# this collapse, *not* by the linear factor a fully scheduler-friendly
# workload would predict.
#
# The ``JIUWENBOX_EXEC_CONCURRENCY`` env var lets operators tune the
# limit (e.g. set it lower than CPU count to leave headroom for the
# server, or set it to a very large number to disable throttling). When
# unset we use the cgroup-aware ``os.process_cpu_count()`` if available
# (Python 3.13+) and fall back to ``os.cpu_count()`` so containers with
# CPU quotas get the right value automatically.
EXEC_CONCURRENCY_ENV = "JIUWENBOX_EXEC_CONCURRENCY"


def _read_cgroup_cpu_quota() -> int | None:
    """Return the cgroup-imposed CPU count, or ``None`` if uncapped.

    On Python 3.11 (which the box-server image ships with) ``os.cpu_count()``
    on Linux returns the *host* CPU count, NOT the container's
    ``--cpus``/cgroup quota. That means a 4-CPU container running on a
    16-core host saw ``os.cpu_count() == 16``, which silently undid the
    exec admission semaphore: the limit became 16, all 8 sandboxes ran
    concurrently, throughput collapsed under 4x oversubscription, and
    sandbox-count=8 latency stayed pinned at ~900 ms instead of ~200 ms.
    Reading cgroup directly gives the actual scheduling budget.
    """
    # cgroup v2: ``/sys/fs/cgroup/cpu.max`` -> ``"<quota> <period>"`` or
    # ``"max <period>"`` if uncapped.
    try:
        with open("/sys/fs/cgroup/cpu.max", "r") as fh:
            raw = fh.read().strip()
    except OSError:
        raw = ""
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota = int(parts[0])
                period = int(parts[1])
            except ValueError:
                quota = period = 0
            if quota > 0 and period > 0:
                cpus = max(1, round(quota / period))
                return cpus
        if parts and parts[0] == "max":
            return None
    # cgroup v1 fallback.
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r") as fh:
            quota = int(fh.read().strip())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r") as fh:
            period = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, round(quota / period))


def _detect_default_exec_concurrency() -> int:
    """Detect how many parallel CPU-bound execs the host can actually run.

    Preference order (most to least authoritative for our purposes):
      1. cgroup CPU quota - exactly matches Docker's ``--cpus``.
      2. ``os.process_cpu_count()`` - Python 3.13+ cgroup-aware helper.
      3. ``os.sched_getaffinity(0)`` - Linux affinity mask, useful when
         the user pinned us via ``taskset``.
      4. ``os.cpu_count()`` - finally, the host's reported CPU count.
    """
    cgroup_cpus = _read_cgroup_cpu_quota()
    if cgroup_cpus is not None and cgroup_cpus >= 1:
        return cgroup_cpus

    process_cpu_count = getattr(os, "process_cpu_count", None)
    if callable(process_cpu_count):
        try:
            value = process_cpu_count()
        except (OSError, ValueError):
            value = None
        if value and value >= 1:
            return value

    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if callable(sched_getaffinity):
        try:
            mask = sched_getaffinity(0)
        except OSError:
            mask = None
        if mask:
            return len(mask)

    cpu_count = os.cpu_count()
    if cpu_count and cpu_count >= 1:
        return cpu_count
    return 1


def _resolve_exec_concurrency() -> int:
    raw = os.environ.get(EXEC_CONCURRENCY_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            logger.warning(
                "Ignoring non-integer %s=%r; falling back to detected CPU count",
                EXEC_CONCURRENCY_ENV,
                raw,
            )
        else:
            if value >= 1:
                return value
            logger.warning(
                "Ignoring %s=%r (must be >= 1); falling back to detected CPU count",
                EXEC_CONCURRENCY_ENV,
                raw,
            )
    return _detect_default_exec_concurrency()


def _safe_close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        logger.debug("Failed to close fd %d", fd, exc_info=True)


def _summarize_command(command: list[str], max_length: int = 180) -> str:
    text = json.dumps(command, ensure_ascii=False)
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... ({len(text)} chars)"


class ProcessRuntime(RuntimeAdapter):
    """Runtime that spawns supervisor as a local process."""

    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen] = {}
        self._policy_paths: dict[str, Path] = {}
        self._runtime_policies: dict[str, SecurityPolicy] = {}
        self._policy_binds: dict[str, list[dict[str, str]]] = {}
        self._network_modes: dict[str, NetworkMode] = {}
        self._netns_names: dict[str, str] = {}
        self._directory_roots: dict[str, Path] = {}
        self._file_roots: dict[str, Path] = {}
        self._launcher_dirs: dict[str, Path] = {}
        self._control_dirs: dict[str, Path] = {}
        self._daemon_socket_ready: dict[str, bool] = {}
        self._seccomp_bpf: dict[str, bytes] = {}
        self._landlock_payloads: dict[str, str] = {}
        self._background_processes: dict[str, list[subprocess.Popen]] = {}
        self._host_firewall_refcounts: dict[tuple[int, int], int] = {}
        self._sandbox_host_firewall_rules: dict[str, list[tuple[int, int]]] = {}
        self._log_dir = JIUWENBOX_HOME / "sandbox_logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        # Admission-control semaphore for ``exec``. Lazy-initialized in
        # ``_ensure_exec_semaphore`` because ``asyncio.Semaphore`` binds to
        # the running loop on first use, and ``ProcessRuntime`` is built
        # outside the asyncio loop in some startup paths (CLI, tests).
        self._exec_concurrency_limit: int = _resolve_exec_concurrency()
        self._exec_semaphore: asyncio.Semaphore | None = None
        logger.info(
            "ProcessRuntime exec concurrency limit = %d "
            "(override via %s; cgroup_cpus=%s, os.cpu_count=%s, "
            "sched_affinity=%s)",
            self._exec_concurrency_limit,
            EXEC_CONCURRENCY_ENV,
            _read_cgroup_cpu_quota(),
            os.cpu_count(),
            len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        )

    @staticmethod
    def _load_policy(policy_path: Path) -> SecurityPolicy:
        with open(policy_path) as f:
            data = yaml.safe_load(f)
        return SecurityPolicy.model_validate(data)

    def _ensure_launcher_dir(self, sandbox_id: str) -> Path:
        """Create a per-sandbox host directory holding the launcher scripts.

        The directory is reused across all ``exec`` calls for the sandbox so we
        do not pay the ``tempfile.TemporaryDirectory`` + file-write cost on the
        hot path. The directory is removed in :meth:`cleanup`.
        """
        existing = self._launcher_dirs.get(sandbox_id)
        if existing is not None and existing.exists():
            return existing

        sandbox_root = self._sandbox_root()
        sandbox_root.mkdir(parents=True, exist_ok=True)
        launcher_dir = Path(tempfile.mkdtemp(
            prefix=f"{sandbox_id}-launcher-",
            dir=sandbox_root,
        ))
        launcher_dst = launcher_dir / "landlock-launcher.py"
        daemon_dst = launcher_dir / "sandbox-daemon.py"
        launcher_dst.write_bytes(_LANDLOCK_LAUNCHER_BYTES)
        daemon_dst.write_bytes(_SANDBOX_DAEMON_BYTES)
        os.chmod(launcher_dst, 0o644)
        os.chmod(daemon_dst, 0o644)
        self._launcher_dirs[sandbox_id] = launcher_dir
        return launcher_dir

    def _ensure_control_dir(self, sandbox_id: str) -> Path:
        """Return the per-sandbox host directory holding the control socket.

        The directory is restricted to mode 0700 owned by the box-server
        process; the listener socket is created here, but the directory is
        **never** bind-mounted into the sandbox - box-server keeps exclusive
        filesystem access to the IPC endpoint.
        """
        existing = self._control_dirs.get(sandbox_id)
        if existing is not None and existing.exists():
            return existing

        sandbox_root = self._sandbox_root()
        sandbox_root.mkdir(parents=True, exist_ok=True)
        control_dir = Path(tempfile.mkdtemp(
            prefix=f"{sandbox_id}-control-",
            dir=sandbox_root,
        ))
        try:
            os.chmod(control_dir, 0o700)
        except OSError:
            logger.debug(
                "Failed to chmod control dir %s; relying on default mode",
                control_dir,
                exc_info=True,
            )
        self._control_dirs[sandbox_id] = control_dir
        return control_dir

    def _control_socket_host_path(self, sandbox_id: str) -> Path | None:
        control_dir = self._control_dirs.get(sandbox_id)
        if control_dir is None:
            return None
        return control_dir / SANDBOX_CONTROL_SOCKET_NAME

    def _create_daemon_listener(
        self,
        sandbox_id: str,
        policy: SecurityPolicy,
    ) -> socket.socket:
        """Create the per-sandbox host listener socket the daemon will adopt.

        The fd is later handed to bubblewrap via
        ``subprocess.Popen(pass_fds=[fd])``. Bubblewrap's user command
        path (PID 2 inside the new pid namespace) never closes arbitrary
        inherited fds, so the listener flows naturally through bwrap →
        launcher → daemon. We mark the fd non-CLOEXEC so it survives every
        ``execve`` along that chain.
        """
        control_dir = self._ensure_control_dir(sandbox_id)
        socket_path = control_dir / SANDBOX_CONTROL_SOCKET_NAME
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(socket_path))
            try:
                # ``0o600`` keeps the listener reachable only by the
                # box-server uid (and root); daemon connects come from
                # outside the sandbox so the sandbox uid does not need
                # access to this path.
                os.chmod(socket_path, 0o600)
            except OSError:
                logger.debug(
                    "Failed to chmod listener socket %s; relying on umask",
                    socket_path,
                    exc_info=True,
                )
            # Restrict ownership tightly: the listener fd is what the daemon
            # uses, so file ownership only protects against in-host meddling.
            try:
                os.chown(socket_path, os.geteuid(), os.getegid())
            except OSError:
                pass
            listener.listen(64)
            # ``pass_fds`` already clears CLOEXEC on the listed fd before
            # exec'ing bwrap; calling ``set_inheritable`` makes the intent
            # explicit and survives even if the caller forgets to thread
            # the fd through ``pass_fds``.
            os.set_inheritable(listener.fileno(), True)
        except Exception:
            try:
                listener.close()
            except OSError:
                pass
            raise
        # Track per-sandbox so the policy-aware uid (used for chown of
        # other policy-managed paths) is consistent with what created the
        # socket; we do not actually need ``policy`` further here.
        _ = policy
        return listener

    def _ensure_seccomp_bpf(self, sandbox_id: str, policy: SecurityPolicy) -> bytes:
        bpf = self._seccomp_bpf.get(sandbox_id)
        if bpf is not None:
            return bpf
        bpf = build_seccomp_filter(policy.syscall)
        self._seccomp_bpf[sandbox_id] = bpf
        return bpf

    def _ensure_landlock_payload(self, sandbox_id: str, policy: SecurityPolicy) -> str:
        payload = self._landlock_payloads.get(sandbox_id)
        if payload is not None:
            return payload
        payload = encode_landlock_payload(policy)
        self._landlock_payloads[sandbox_id] = payload
        return payload

    @staticmethod
    def _open_seccomp_fd_from_bytes(bpf: bytes) -> int:
        """Create an anonymous memfd preloaded with ``bpf`` for bwrap.

        Mirrors :func:`jiuwenbox.supervisor.seccomp.open_seccomp_filter` but
        works from cached BPF bytes so the BPF program does not have to be
        re-assembled for every exec.
        """
        if not hasattr(os, "memfd_create"):
            raise RuntimeError("os.memfd_create is required for seccomp filters")
        fd = os.memfd_create("jiuwenbox-seccomp", getattr(os, "MFD_CLOEXEC", 0x0001))
        try:
            offset = 0
            while offset < len(bpf):
                offset += os.write(fd, bpf[offset:])
            os.lseek(fd, 0, os.SEEK_SET)
        except Exception:
            os.close(fd)
            raise
        return fd

    def _build_sandbox_bwrap_args(
        self,
        sandbox_id: str,
        policy: SecurityPolicy,
        command: list[str],
        *,
        is_daemon: bool,
        workdir: str | None,
        sandbox_env: dict[str, str] | None,
        netns_attached: bool,
        seccomp_fd: int | None,
        listener_fd: int | None = None,
    ) -> list[str]:
        """Build a ready-to-spawn bwrap argv vector for ``command``.

        This collapses the work that used to happen inside the long-lived
        ``box-supervisor`` Python process into the box-server itself. Caches
        populated during :meth:`create` (policy binds, launcher scripts,
        seccomp BPF, landlock payload) keep the per-call cost low.
        """
        config = BwrapConfig.from_policy(policy, list(command))
        if sandbox_env:
            config.env.update(sandbox_env)
        if workdir:
            config.workdir = workdir

        # When the runtime has joined the bwrap process to a pre-configured
        # named netns via ``ip netns exec``, bwrap must not unshare it again
        # otherwise the carefully prepared firewall rules become invisible.
        if policy.network.mode == NetworkMode.ISOLATED and netns_attached:
            config.unshare_net = False

        for entry in self._policy_binds.get(sandbox_id, []):
            config.rw_binds.append((entry["host_path"], entry["sandbox_path"]))

        launcher_dir = self._launcher_dirs.get(sandbox_id)
        landlock_enabled = policy.landlock.compatibility != "disabled"

        if is_daemon and launcher_dir is not None:
            daemon_path = launcher_dir / "sandbox-daemon.py"
            config.ro_binds.append(
                (str(daemon_path), SANDBOX_DAEMON_SANDBOX_PATH),
            )

        if listener_fd is not None:
            # The listener fd is delivered into the sandbox purely through
            # natural fd inheritance: box-server creates the listener with
            # CLOEXEC cleared, hands the fd to bubblewrap via
            # ``subprocess.Popen(pass_fds=[fd])``, and bubblewrap's user
            # command path (PID 2 of the new pid namespace) never calls
            # ``fdwalk`` to close arbitrary inherited descriptors, so the fd
            # survives the bwrap → launcher → daemon ``execve`` chain. The
            # daemon adopts it via ``LISTENER_FD_ENV`` and never reaches
            # into the filesystem for the IPC endpoint, which means
            # Landlock can stay locked down.
            config.env[LISTENER_FD_ENV] = str(listener_fd)

        if launcher_dir is not None and landlock_enabled:
            launcher_path = launcher_dir / "landlock-launcher.py"
            # ``SANDBOX_RESERVED_DIR`` is created as a fresh tmpfs by bwrap
            # so we can ``--ro-bind`` the trusted scripts on top of it.
            # ``PolicyEngine`` rejects any user policy that references this
            # subtree, which prevents the launcher / daemon mount from
            # colliding with a user-supplied ``bind_mount`` or being
            # accidentally exposed to user code via the Landlock allowlist.
            config.add_dir_mount(SANDBOX_RESERVED_DIR)
            config.ro_binds.append((str(launcher_path), SANDBOX_LAUNCHER_PATH))
            payload = self._ensure_landlock_payload(sandbox_id, policy)
            # ``-S`` skips ``import site`` so the launcher does not pay for
            # building the global ``sys.path`` table; the launcher is
            # stdlib-only.
            #
            # For the per-sandbox daemon we use the launcher's
            # ``--daemon`` mode: the launcher pre-reads the daemon
            # script, applies Landlock, and then runs the daemon code in
            # the same Python process via ``compile``/``exec``. There is
            # no second ``execve`` after Landlock is locked in, which
            # means the daemon (and every ``fork+exec`` it does for IPC
            # requests) inherits Landlock without the daemon script's
            # on-disk path needing to remain reachable. That lets us
            # keep ``/run`` outside the Landlock allowlist so user code
            # cannot read the launcher / daemon scripts at runtime.
            #
            # For the generic (legacy) path used by ``exec_background``,
            # the launcher still runs in ``--`` mode: apply Landlock and
            # then ``execvp`` the requested user command.
            if is_daemon:
                config.command = [
                    PYTHON_EXECUTABLE,
                    "-S",
                    SANDBOX_LAUNCHER_PATH,
                    payload,
                    "--daemon",
                    SANDBOX_DAEMON_SANDBOX_PATH,
                ]
            else:
                config.command = [
                    PYTHON_EXECUTABLE,
                    "-S",
                    SANDBOX_LAUNCHER_PATH,
                    payload,
                    "--",
                    *config.command,
                ]

        config.seccomp_fd = seccomp_fd
        return config.to_args()

    def _get_netns_name(self, sandbox_id: str) -> str:
        return self._netns_names.setdefault(
            sandbox_id,
            network_module.netns_name_for_sandbox(sandbox_id),
        )

    def _ensure_named_netns(self, sandbox_id: str, policy: SecurityPolicy) -> str | None:
        if policy.network.mode != NetworkMode.ISOLATED:
            return None

        namespace = self._get_netns_name(sandbox_id)
        if network_module.namespace_exists(namespace):
            return namespace

        network_module.create_named_namespace(namespace)
        try:
            network_module.setup_network_isolation(policy.network, namespace=namespace)
        except Exception:
            try:
                network_module.delete_named_namespace(namespace)
            except Exception:
                logger.warning(
                    "Failed to rollback network namespace %s after setup error",
                    namespace,
                    exc_info=True,
                )
            raise

        return namespace

    @staticmethod
    def _directory_spec(directory: object) -> tuple[str, str | None]:
        if isinstance(directory, str):
            return directory, None
        return getattr(directory, "path"), getattr(directory, "permissions", None)

    @staticmethod
    def _file_spec(file: object) -> tuple[str, str | None]:
        if isinstance(file, str):
            return file, None
        return getattr(file, "path"), getattr(file, "permissions", None)

    @staticmethod
    def _host_entry_name(sandbox_path: str) -> str:
        encoded = base64.urlsafe_b64encode(sandbox_path.encode()).decode()
        return encoded.rstrip("=")

    @staticmethod
    def _sandbox_root() -> Path:
        return SANDBOX_WORKSPACE

    @staticmethod
    def _resolve_backing_identity(policy: SecurityPolicy) -> tuple[int, int]:
        if policy.namespace.user:
            # For unprivileged user namespaces, the server uid is mapped to the
            # sandbox uid, so keeping the backing directory owned by the current
            # process is the writable choice. When the server runs as root, bwrap
            # can drop to the requested sandbox uid; root-owned 0755 directories
            # would then reject writes such as uploads under /home.
            if os.geteuid() != 0:
                return os.getuid(), os.getgid()

        try:
            uid = pwd.getpwnam(policy.process.run_as_user).pw_uid
        except KeyError:
            uid = 65534
        try:
            gid = grp.getgrnam(policy.process.run_as_group).gr_gid
        except KeyError:
            gid = 65534
        return uid, gid

    @staticmethod
    def _resolve_process_uid(policy: SecurityPolicy) -> int:
        try:
            return pwd.getpwnam(policy.process.run_as_user).pw_uid
        except KeyError:
            return 65534

    @classmethod
    def _host_firewall_uids(cls, policy: SecurityPolicy) -> list[int]:
        uids = [cls._resolve_process_uid(policy)]
        current_uid = os.geteuid()
        if policy.namespace.user and current_uid == 0 and current_uid not in uids:
            uids.append(current_uid)
        return uids

    @staticmethod
    def _server_protect_ports() -> list[int]:
        raw_ports = os.environ.get(SERVER_PROTECT_PORTS_ENV)
        if raw_ports is None:
            return list(DEFAULT_SERVER_PROTECT_PORTS)
        if not raw_ports.strip():
            return []

        ports: list[int] = []
        for raw_port in raw_ports.split(","):
            raw_port = raw_port.strip()
            if not raw_port:
                continue
            try:
                port = int(raw_port)
            except ValueError:
                logger.warning(
                    "Ignoring invalid %s entry: %s",
                    SERVER_PROTECT_PORTS_ENV,
                    raw_port,
                )
                continue
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
            else:
                logger.warning(
                    "Ignoring out-of-range %s entry: %s",
                    SERVER_PROTECT_PORTS_ENV,
                    raw_port,
                )
        return ports

    @staticmethod
    def _host_firewall_insert_args(uid: int, port: int) -> list[str]:
        return [
            "-I", "OUTPUT", "1",
            "-p", "tcp",
            "-m", "owner", "--uid-owner", str(uid),
            "--dport", str(port),
            "-j", "REJECT",
        ]

    @staticmethod
    def _host_firewall_delete_args(uid: int, port: int) -> list[str]:
        return [
            "-D", "OUTPUT",
            "-p", "tcp",
            "-m", "owner", "--uid-owner", str(uid),
            "--dport", str(port),
            "-j", "REJECT",
        ]

    def _install_host_firewall_rule(self, uid: int, port: int) -> tuple[int, int]:
        key = (uid, port)
        current_count = self._host_firewall_refcounts.get(key, 0)
        if current_count == 0:
            network_module.run_iptables(
                self._host_firewall_insert_args(uid, port),
                ip_version=4,
            )
            logger.info(
                "Blocked sandbox uid %d from connecting to box-server port %d",
                uid,
                port,
            )
        self._host_firewall_refcounts[key] = current_count + 1
        return key

    def _remove_host_firewall_rule(self, uid: int, port: int) -> None:
        key = (uid, port)
        current_count = self._host_firewall_refcounts.get(key, 0)
        if current_count <= 1:
            self._host_firewall_refcounts.pop(key, None)
            try:
                network_module.run_iptables(
                    self._host_firewall_delete_args(uid, port),
                    ip_version=4,
                )
            except Exception:
                logger.warning(
                    "Failed to remove sandbox uid %d box-server port %d block rule",
                    uid,
                    port,
                    exc_info=True,
                )
            return
        self._host_firewall_refcounts[key] = current_count - 1

    def _install_sandbox_host_firewall_rules(
        self,
        sandbox_id: str,
        policy: SecurityPolicy,
    ) -> None:
        if policy.network.mode != NetworkMode.HOST:
            return

        ports = self._server_protect_ports()
        if not ports:
            return

        installed: list[tuple[int, int]] = []
        try:
            for uid in self._host_firewall_uids(policy):
                for port in ports:
                    installed.append(self._install_host_firewall_rule(uid, port))
        except Exception:
            for installed_uid, installed_port in reversed(installed):
                self._remove_host_firewall_rule(installed_uid, installed_port)
            raise
        self._sandbox_host_firewall_rules[sandbox_id] = installed

    def _remove_sandbox_host_firewall_rules(self, sandbox_id: str) -> None:
        for uid, port in reversed(self._sandbox_host_firewall_rules.pop(sandbox_id, [])):
            self._remove_host_firewall_rule(uid, port)

    @staticmethod
    def _apply_path_ownership(path: Path, uid: int, gid: int) -> bool:
        try:
            os.chown(path, uid, gid)
            return True
        except PermissionError:
            logger.warning(
                "Failed to chown policy path %s to %d:%d; keeping current owner",
                path,
                uid,
                gid,
            )
            return False

    @staticmethod
    def _apply_path_permissions(path: Path, permissions: str | None) -> None:
        if permissions is None:
            return
        os.chmod(path, int(permissions, 8))

    @staticmethod
    def _needs_userns_write_fallback(
        policy: SecurityPolicy,
        uid: int,
        permissions: str | None,
    ) -> bool:
        if os.geteuid() != 0 or not policy.namespace.user or uid == 0:
            return False
        if permissions is None:
            return True
        mode = int(permissions, 8)
        return (
            bool(mode & 0o200)
            and (mode & 0o005) == 0o005
            and not bool(mode & 0o002)
        )

    @staticmethod
    def _needs_userns_owner_access_fallback(
        policy: SecurityPolicy,
        uid: int,
    ) -> bool:
        return os.geteuid() == 0 and policy.namespace.user and uid != 0

    @staticmethod
    def _apply_userns_write_fallback(path: Path) -> None:
        mode = path.stat().st_mode & 0o777
        fallback_mode = mode | 0o003
        if fallback_mode == mode:
            return
        logger.warning(
            "Relaxing policy path %s permissions from %s to %s because "
            "root-run user namespaces cannot map the sandbox uid onto the "
            "bind-mounted backing path owner",
            path,
            oct(mode),
            oct(fallback_mode),
        )
        os.chmod(path, fallback_mode)

    @staticmethod
    def _apply_userns_file_access_fallback(path: Path) -> None:
        mode = path.stat().st_mode & 0o777
        owner_bits = (mode & 0o700) >> 6
        fallback_mode = mode | owner_bits
        if fallback_mode == mode:
            return
        logger.warning(
            "Relaxing policy file %s permissions from %s to %s because "
            "root-run user namespaces cannot preserve owner-only file access",
            path,
            oct(mode),
            oct(fallback_mode),
        )
        os.chmod(path, fallback_mode)

    @staticmethod
    def _ensure_writable_when_chown_unavailable(path: Path, owner_applied: bool) -> None:
        if owner_applied:
            return

        if path.stat().st_uid == os.getuid():
            return

        mode = path.stat().st_mode & 0o777
        if mode & 0o005 and not mode & 0o002:
            fallback_mode = mode | 0o002
            logger.warning(
                "Relaxing policy path %s permissions from %s to %s because chown failed",
                path,
                oct(mode),
                oct(fallback_mode),
            )
            os.chmod(path, fallback_mode)

    def _ensure_policy_directories(
        self,
        sandbox_id: str,
        policy: SecurityPolicy,
    ) -> list[dict[str, str]]:
        directories = policy.filesystem_policy.directories
        if not directories:
            return []

        sandbox_root = self._sandbox_root()
        sandbox_root.mkdir(parents=True, exist_ok=True)
        directory_root = self._directory_roots.get(sandbox_id)
        if directory_root is None:
            directory_root = Path(tempfile.mkdtemp(
                prefix=f"{sandbox_id}-dirs-",
                dir=sandbox_root,
            ))
            self._directory_roots[sandbox_id] = directory_root
        else:
            directory_root.mkdir(parents=True, exist_ok=True)

        uid, gid = self._resolve_backing_identity(policy)
        binds: list[dict[str, str]] = []
        for directory in directories:
            sandbox_path, permissions = self._directory_spec(directory)
            host_path = directory_root / self._host_entry_name(sandbox_path)
            host_path.mkdir(parents=True, exist_ok=True)
            owner_applied = self._apply_path_ownership(host_path, uid, gid)
            self._apply_path_permissions(host_path, permissions)
            if self._needs_userns_write_fallback(policy, uid, permissions):
                self._apply_userns_write_fallback(host_path)
            self._ensure_writable_when_chown_unavailable(host_path, owner_applied)
            binds.append({
                "host_path": str(host_path),
                "sandbox_path": sandbox_path,
            })
        return binds

    def _ensure_policy_files(
        self,
        sandbox_id: str,
        policy: SecurityPolicy,
    ) -> list[dict[str, str]]:
        files = policy.filesystem_policy.files
        if not files:
            return []

        sandbox_root = self._sandbox_root()
        sandbox_root.mkdir(parents=True, exist_ok=True)
        file_root = self._file_roots.get(sandbox_id)
        if file_root is None:
            file_root = Path(tempfile.mkdtemp(
                prefix=f"{sandbox_id}-files-",
                dir=sandbox_root,
            ))
            self._file_roots[sandbox_id] = file_root
        else:
            file_root.mkdir(parents=True, exist_ok=True)

        uid, gid = self._resolve_backing_identity(policy)
        binds: list[dict[str, str]] = []
        for file in files:
            sandbox_path, permissions = self._file_spec(file)
            host_path = file_root / self._host_entry_name(sandbox_path)
            host_path.parent.mkdir(parents=True, exist_ok=True)
            host_path.touch(exist_ok=True)
            owner_applied = self._apply_path_ownership(host_path, uid, gid)
            self._apply_path_permissions(host_path, permissions)
            if self._needs_userns_owner_access_fallback(policy, uid):
                self._apply_userns_file_access_fallback(host_path)
            if self._needs_userns_write_fallback(policy, uid, permissions):
                self._apply_userns_write_fallback(host_path)
            self._ensure_writable_when_chown_unavailable(host_path, owner_applied)
            binds.append({
                "host_path": str(host_path),
                "sandbox_path": sandbox_path,
            })
        return binds

    @staticmethod
    def _wrap_command_in_namespace(command: list[str], namespace: str | None) -> list[str]:
        if not namespace:
            return command
        return [network_module.IP_BINARY, "netns", "exec", namespace, *command]

    @staticmethod
    def _apply_runtime_env(
        process_env: dict[str, str],
        *,
        netns_name: str | None,
        policy_binds: list[dict[str, str]],
        sandbox_env: dict[str, str] | None = None,
        sandbox_workdir: str | None = None,
    ) -> None:
        if netns_name:
            process_env["JIUWENBOX_NETNS_READY"] = "1"
        if policy_binds:
            process_env[RUNTIME_POLICY_BINDS] = json.dumps(
                policy_binds,
                separators=(",", ":"),
            )
        else:
            process_env.pop(RUNTIME_POLICY_BINDS, None)
        if sandbox_env is not None:
            process_env[RUNTIME_SANDBOX_ENV] = json.dumps(
                sandbox_env,
                separators=(",", ":"),
            )
        if sandbox_workdir:
            process_env[RUNTIME_SANDBOX_WORKDIR] = sandbox_workdir
        else:
            process_env.pop(RUNTIME_SANDBOX_WORKDIR, None)

    def _network_mode_for_cleanup(self, sandbox_id: str) -> NetworkMode | None:
        mode = self._network_modes.get(sandbox_id)
        if mode is not None:
            return mode

        policy_path = self._policy_paths.get(sandbox_id)
        if policy_path is None or not policy_path.exists():
            return None

        try:
            mode = self._load_policy(policy_path).network.mode
        except Exception:
            logger.warning("Failed to reload policy for sandbox %s during cleanup", sandbox_id, exc_info=True)
            return None

        self._network_modes[sandbox_id] = mode
        return mode

    def _policy_for_sandbox(self, sandbox_id: str, policy_path: Path) -> SecurityPolicy:
        policy = self._runtime_policies.get(sandbox_id)
        if policy is None:
            policy = self._load_policy(policy_path)
            self._runtime_policies[sandbox_id] = policy
            self._network_modes[sandbox_id] = policy.network.mode
        return policy

    def _policy_binds_for_sandbox(
        self,
        sandbox_id: str,
        policy: SecurityPolicy,
    ) -> list[dict[str, str]]:
        policy_binds = self._policy_binds.get(sandbox_id)
        if policy_binds is None:
            directory_binds = self._ensure_policy_directories(sandbox_id, policy)
            file_binds = self._ensure_policy_files(sandbox_id, policy)
            policy_binds = [*directory_binds, *file_binds]
            self._policy_binds[sandbox_id] = policy_binds
        return policy_binds

    def _reap_background_processes(self, sandbox_id: str) -> None:
        processes = self._background_processes.get(sandbox_id)
        if not processes:
            return
        running = [proc for proc in processes if proc.poll() is None]
        if running:
            self._background_processes[sandbox_id] = running
        else:
            self._background_processes.pop(sandbox_id, None)

    async def _stop_background_processes(self, sandbox_id: str, timeout: float = 5.0) -> None:
        processes = self._background_processes.pop(sandbox_id, [])
        if not processes:
            return

        running = [proc for proc in processes if proc.poll() is None]
        for proc in running:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue

        loop = asyncio.get_running_loop()
        for proc in running:
            try:
                await loop.run_in_executor(None, proc.wait, timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                await loop.run_in_executor(None, proc.wait, 5.0)

    async def create(
        self,
        sandbox_id: str,
        policy_path: Path,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        existing = self._processes.get(sandbox_id)
        if existing is not None:
            if existing.poll() is None:
                raise RuntimeError(f"Sandbox {sandbox_id} already has a running process")
            self._processes.pop(sandbox_id, None)

        policy = self._load_policy(policy_path)
        self._runtime_policies[sandbox_id] = policy
        self._network_modes[sandbox_id] = policy.network.mode
        self._policy_paths[sandbox_id] = Path(policy_path)

        netns_name = self._ensure_named_netns(sandbox_id, policy)
        self._policy_binds_for_sandbox(sandbox_id, policy)
        self._install_sandbox_host_firewall_rules(sandbox_id, policy)

        # Pre-build expensive per-sandbox artifacts so subsequent ``exec``
        # calls only have to allocate a fresh seccomp memfd and assemble the
        # bwrap argv list.
        self._ensure_launcher_dir(sandbox_id)
        self._ensure_landlock_payload(sandbox_id, policy)
        self._daemon_socket_ready[sandbox_id] = False

        try:
            listener = self._create_daemon_listener(sandbox_id, policy)
        except OSError as exc:
            self._cleanup_sandbox_artifacts(sandbox_id, netns_name)
            raise RuntimeError(
                f"Failed to bind daemon control listener for sandbox "
                f"{sandbox_id}: {exc}",
            ) from exc
        listener_fd = listener.fileno()

        seccomp_fd: int | None = None
        try:
            seccomp_fd = self._open_seccomp_fd_from_bytes(
                self._ensure_seccomp_bpf(sandbox_id, policy),
            )
        except Exception:
            logger.warning(
                "Failed to build seccomp filter for sandbox %s; continuing without seccomp",
                sandbox_id,
                exc_info=True,
            )

        bwrap_args = self._build_sandbox_bwrap_args(
            sandbox_id,
            policy,
            list(SANDBOX_DAEMON_COMMAND),
            is_daemon=True,
            workdir=workdir,
            sandbox_env=env,
            netns_attached=netns_name is not None,
            seccomp_fd=seccomp_fd,
            listener_fd=listener_fd,
        )
        daemon_cmd = self._wrap_command_in_namespace(bwrap_args, netns_name)

        process_env = {**os.environ, **(env or {})}
        # Forward the listener fd number to bwrap (and onward to the
        # daemon) via env so the daemon can recover it without having to
        # parse ``argv``.
        process_env[LISTENER_FD_ENV] = str(listener_fd)
        log_file = self._log_dir / f"{sandbox_id}.log"

        logger.info("Spawning sandbox daemon for %s", sandbox_id)
        logger.debug("Sandbox daemon bwrap command for %s: %s", sandbox_id, daemon_cmd)

        pass_fd_list: list[int] = [listener_fd]
        if seccomp_fd is not None:
            pass_fd_list.append(seccomp_fd)
        pass_fds = tuple(pass_fd_list)
        try:
            log_fd = open(log_file, "w", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    daemon_cmd,
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    env=process_env,
                    pass_fds=pass_fds,
                    start_new_session=True,
                )
            finally:
                log_fd.close()
        except Exception:
            if seccomp_fd is not None:
                _safe_close_fd(seccomp_fd)
            try:
                listener.close()
            except OSError:
                pass
            self._cleanup_sandbox_artifacts(sandbox_id, netns_name)
            raise

        if seccomp_fd is not None:
            _safe_close_fd(seccomp_fd)
        # The daemon now owns the listener fd in its process; box-server's
        # copy is no longer useful and must not block accept-loop teardown.
        try:
            listener.close()
        except OSError:
            pass

        self._processes[sandbox_id] = proc
        await self._wait_daemon_ready(
            sandbox_id,
            proc,
            log_file,
            netns_name,
        )
        logger.info("Sandbox daemon started for %s (pid=%d)", sandbox_id, proc.pid)
        return proc.pid

    async def _wait_daemon_ready(
        self,
        sandbox_id: str,
        proc: subprocess.Popen,
        log_file: Path,
        netns_name: str | None,
    ) -> None:
        """Verify the daemon is alive and mark its IPC channel ready.

        Box-server already created the listener and ``listen()``ed before
        spawning bubblewrap, so the kernel will queue connection attempts
        immediately - there is no socket file we still have to wait for. We
        sleep briefly to ensure the daemon process has had a chance to
        ``accept()`` (so the very first request does not block waiting for
        a worker thread to spin up) and confirm the bwrap parent has not
        already exited with an error.
        """
        await asyncio.sleep(DAEMON_STARTUP_GRACE_SECONDS)
        self._verify_daemon_alive(sandbox_id, proc, log_file, netns_name)
        self._daemon_socket_ready[sandbox_id] = True

    def _verify_daemon_alive(
        self,
        sandbox_id: str,
        proc: subprocess.Popen,
        log_file: Path,
        netns_name: str | None,
    ) -> None:
        if proc.poll() is None:
            return
        self._processes.pop(sandbox_id, None)
        self._cleanup_sandbox_artifacts(sandbox_id, netns_name)
        log_tail = ""
        if log_file.exists():
            log_tail = log_file.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(
            f"Sandbox daemon exited during startup with code {proc.returncode}; "
            f"see log {log_file}. Log tail: {log_tail}"
        )

    def _cleanup_sandbox_artifacts(
        self,
        sandbox_id: str,
        netns_name: str | None,
    ) -> None:
        """Drop every cache that was populated during a failed ``create``."""
        directory_root = self._directory_roots.pop(sandbox_id, None)
        if directory_root is not None:
            shutil.rmtree(directory_root, ignore_errors=True)
        file_root = self._file_roots.pop(sandbox_id, None)
        if file_root is not None:
            shutil.rmtree(file_root, ignore_errors=True)
        launcher_dir = self._launcher_dirs.pop(sandbox_id, None)
        if launcher_dir is not None:
            shutil.rmtree(launcher_dir, ignore_errors=True)
        control_dir = self._control_dirs.pop(sandbox_id, None)
        if control_dir is not None:
            shutil.rmtree(control_dir, ignore_errors=True)
        self._daemon_socket_ready.pop(sandbox_id, None)
        if netns_name and network_module.namespace_exists(netns_name):
            network_module.delete_named_namespace(netns_name)
        self._network_modes.pop(sandbox_id, None)
        self._runtime_policies.pop(sandbox_id, None)
        self._policy_binds.pop(sandbox_id, None)
        self._seccomp_bpf.pop(sandbox_id, None)
        self._landlock_payloads.pop(sandbox_id, None)
        self._policy_paths.pop(sandbox_id, None)
        self._remove_sandbox_host_firewall_rules(sandbox_id)

    async def stop(self, sandbox_id: str, timeout: float = 10.0) -> None:
        await self._stop_background_processes(sandbox_id)
        proc = self._processes.get(sandbox_id)
        if proc is None:
            self._remove_sandbox_host_firewall_rules(sandbox_id)
            return
        if proc.poll() is not None:
            self._processes.pop(sandbox_id, None)
            self._remove_sandbox_host_firewall_rules(sandbox_id)
            return

        logger.info("Stopping sandbox %s (pid=%d)", sandbox_id, proc.pid)
        # Politely ask the daemon to drain in-flight IPC requests first;
        # ``SIGTERM`` from the host then trips the kernel's normal default
        # action (the daemon installs no signal handlers, so PID-1 namespace
        # init protection prevents inside-sandbox processes from hijacking
        # this path).
        await self._send_daemon_shutdown(sandbox_id)
        # Briefly give the daemon a chance to exit on its own; if it does we
        # avoid the SIGTERM/SIGKILL escalation entirely.
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, proc.wait, DAEMON_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                self._processes.pop(sandbox_id, None)
                self._daemon_socket_ready.pop(sandbox_id, None)
                self._remove_sandbox_host_firewall_rules(sandbox_id)
                return

            try:
                await loop.run_in_executor(None, proc.wait, timeout)
            except subprocess.TimeoutExpired:
                logger.warning("SIGTERM timeout for %s, killing", sandbox_id)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    self._processes.pop(sandbox_id, None)
                    self._daemon_socket_ready.pop(sandbox_id, None)
                    self._remove_sandbox_host_firewall_rules(sandbox_id)
                    return
                await loop.run_in_executor(None, proc.wait, 5.0)

        self._processes.pop(sandbox_id, None)
        self._daemon_socket_ready.pop(sandbox_id, None)
        self._remove_sandbox_host_firewall_rules(sandbox_id)

    async def is_running(self, sandbox_id: str) -> bool:
        proc = self._processes.get(sandbox_id)
        if proc is None:
            return False
        return proc.poll() is None

    def get_exit_diagnostics(self, sandbox_id: str) -> str:
        """Return diagnostics for a sandbox whose lifecycle process is not running."""
        proc = self._processes.get(sandbox_id)
        returncode = None if proc is None else proc.poll()
        log_file = self._log_dir / f"{sandbox_id}.log"
        log_tail = ""
        if log_file.exists():
            log_tail = log_file.read_text(encoding="utf-8", errors="replace")[-4000:]
        return (
            f"Sandbox lifecycle process is not running; "
            f"returncode={returncode}; log={log_file}; log_tail={log_tail}"
        )

    def _prepare_exec_invocation(
        self,
        sandbox_id: str,
        request: RuntimeExecRequest,
    ) -> tuple[list[str], int | None] | None:
        """Build the bwrap argv + a fresh seccomp memfd for one ``exec``.

        Returns ``None`` when the sandbox has no recorded policy (the caller
        decides what failure to surface).
        """
        policy_path = self._policy_paths.get(sandbox_id)
        if policy_path is None:
            return None

        policy = self._policy_for_sandbox(sandbox_id, policy_path)
        if policy.network.mode == NetworkMode.ISOLATED:
            netns_name = self._ensure_named_netns(sandbox_id, policy)
        else:
            netns_name = None
        self._policy_binds_for_sandbox(sandbox_id, policy)
        self._ensure_launcher_dir(sandbox_id)

        seccomp_fd: int | None = None
        try:
            seccomp_fd = self._open_seccomp_fd_from_bytes(
                self._ensure_seccomp_bpf(sandbox_id, policy),
            )
        except Exception:
            logger.warning(
                "Failed to build seccomp filter for sandbox %s exec; continuing without seccomp",
                sandbox_id,
                exc_info=True,
            )

        bwrap_args = self._build_sandbox_bwrap_args(
            sandbox_id,
            policy,
            list(request.command),
            is_daemon=False,
            workdir=request.workdir,
            sandbox_env=request.env,
            netns_attached=netns_name is not None,
            seccomp_fd=seccomp_fd,
        )
        return self._wrap_command_in_namespace(bwrap_args, netns_name), seccomp_fd

    def _daemon_ipc_available(self, sandbox_id: str) -> bool:
        if not self._daemon_socket_ready.get(sandbox_id, False):
            return False
        proc = self._processes.get(sandbox_id)
        if proc is None or proc.poll() is not None:
            return False
        socket_path = self._control_socket_host_path(sandbox_id)
        if socket_path is None:
            return False
        try:
            return socket_path.exists()
        except OSError:
            return False

    @staticmethod
    def _connect_daemon_socket(socket_path: Path) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DAEMON_CONNECT_TIMEOUT_SECONDS)
        try:
            sock.connect(str(socket_path))
        except OSError:
            sock.close()
            raise
        return sock

    @staticmethod
    def _send_request_blob(
        sock: socket.socket,
        header_blob: bytes,
        stdin_bytes: bytes | None,
    ) -> None:
        send_frame(sock, header_blob)
        if stdin_bytes:
            sock.sendall(stdin_bytes)

    @staticmethod
    def _read_response_blob(sock: socket.socket) -> dict[str, Any]:
        blob = recv_frame(sock, DAEMON_MAX_RESPONSE_BYTES)
        return json.loads(blob.decode("utf-8"))

    def _exec_via_daemon_blocking(self, call: _DaemonExecCall) -> ExecResult:
        """Run one ``exec`` over the IPC channel and return an ``ExecResult``.

        Executed via a worker thread so the asyncio event loop does not block
        on the synchronous Unix-socket IO. The daemon ``communicate()``s the
        child entirely before responding, so we only have to handle the
        request/response pair here.
        """
        request_payload: dict[str, Any] = {
            "command": list(call.command),
            "stdin_size": len(call.stdin_bytes or b""),
        }
        if call.env:
            request_payload["env"] = dict(call.env)
        if call.workdir:
            request_payload["workdir"] = call.workdir
        if call.timeout is not None:
            request_payload["timeout"] = call.timeout
        header_blob = encode_request(
            request_type=REQUEST_TYPE_EXEC,
            payload=request_payload,
        )
        if len(header_blob) > MAX_HEADER_BYTES:
            return ExecResult(
                exit_code=1,
                stderr=(
                    f"daemon request header too large "
                    f"({len(header_blob)} > {MAX_HEADER_BYTES})"
                ),
            )

        sock = self._connect_daemon_socket(call.socket_path)
        try:
            # The daemon waits for the user command to finish before
            # responding, so the receive timeout has to outlive the request
            # timeout. ``None`` means "wait forever" which matches the
            # legacy bwrap path when no timeout is configured.
            if call.timeout is not None:
                sock.settimeout(call.timeout + 5.0)
            else:
                sock.settimeout(None)
            self._send_request_blob(sock, header_blob, call.stdin_bytes)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            response = self._read_response_blob(sock)
        finally:
            try:
                sock.close()
            except OSError:
                pass

        return ExecResult(
            exit_code=int(response.get("exit_code", 1)),
            stdout=str(response.get("stdout", "")),
            stderr=str(response.get("stderr", "")),
        )

    async def _exec_via_daemon(
        self,
        sandbox_id: str,
        request: RuntimeExecRequest,
    ) -> ExecResult:
        """Run an ``exec`` over the daemon IPC channel.

        Transport-level failures (connection refused, daemon crashed,
        timeout, framing/JSON corruption) surface as a synthetic
        ``ExecResult`` with a non-zero exit code so callers see the same
        value-shape regardless of whether the IPC roundtrip succeeded.
        Whenever transport fails the daemon is also flagged unhealthy so
        the next call short-circuits without attempting another connect.
        """
        socket_path = self._control_socket_host_path(sandbox_id)
        if socket_path is None or not self._daemon_ipc_available(sandbox_id):
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=(
                    f"sandbox {sandbox_id!r} daemon IPC channel unavailable; "
                    "the daemon is not running or its control socket is gone"
                ),
            )

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                self._exec_via_daemon_blocking,
                _DaemonExecCall(
                    socket_path=socket_path,
                    command=list(request.command),
                    env=dict(request.env) if request.env else None,
                    workdir=request.workdir,
                    stdin_bytes=request.stdin_data,
                    timeout=request.timeout,
                ),
            )
        except (ConnectionError, ValueError) as exc:
            # ``ValueError`` already covers ``json.JSONDecodeError`` (G.ERR.09).
            self._daemon_socket_ready[sandbox_id] = False
            logger.warning(
                "Daemon IPC transport failure for sandbox %s: %s",
                sandbox_id,
                exc,
            )
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"daemon IPC transport failure: {exc}",
            )
        except socket.timeout as exc:
            self._daemon_socket_ready[sandbox_id] = False
            logger.warning(
                "Daemon IPC timeout for sandbox %s: %s",
                sandbox_id,
                exc,
            )
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr=f"daemon IPC timeout: {exc}",
            )
        except OSError as exc:
            if exc.errno in FATAL_DAEMON_ERRNOS:
                # Daemon is genuinely gone - flip the flag so future
                # callers stop trying this socket and fall back.
                self._daemon_socket_ready[sandbox_id] = False
                logger.warning(
                    "Daemon IPC unavailable for sandbox %s (fatal errno=%s): %s",
                    sandbox_id,
                    exc.errno,
                    exc,
                )
                return ExecResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"daemon IPC unavailable: {exc}",
                )
            if exc.errno in RECOVERABLE_DAEMON_ERRNOS:
                # This is just transient host-side pressure (EMFILE,
                # EAGAIN, ENOMEM, ...). Don't poison the daemon - the
                # next request should be able to use the fast path.
                logger.warning(
                    "Daemon IPC transient failure for sandbox %s (errno=%s): %s",
                    sandbox_id,
                    exc.errno,
                    exc,
                )
                return ExecResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"daemon IPC transient failure: {exc}",
                )
            raise

    def _file_op_unavailable(self, sandbox_id: str) -> RuntimeFileOpResult:
        return RuntimeFileOpResult(
            ok=False,
            error="daemon_unavailable",
            detail=(
                f"sandbox {sandbox_id!r} daemon IPC channel unavailable; "
                "the daemon is not running or its control socket is gone"
            ),
        )

    def _file_op_transport_failure(
        self,
        sandbox_id: str,
        exc: BaseException,
        *,
        fatal: bool = True,
    ) -> RuntimeFileOpResult:
        """Build a transport-failure result.

        If ``fatal`` is true the sandbox's daemon is flagged unhealthy so
        subsequent callers fall back to the legacy ``bash`` / ``python3``
        path. Recoverable resource-pressure errors (EMFILE, EAGAIN, ...)
        should pass ``fatal=False`` so the next request can still take
        the IPC fast path - otherwise a single transient blip permanently
        demotes the sandbox to the slow exec fallback and turns subsequent
        calls into ~hundreds of ms each.
        """
        if fatal:
            self._daemon_socket_ready[sandbox_id] = False
        logger.warning(
            "Daemon IPC %s failure during file-op for sandbox %s: %s",
            "transport" if fatal else "transient",
            sandbox_id,
            exc,
        )
        return RuntimeFileOpResult(
            ok=False,
            error="transport_failure",
            detail=str(exc),
        )

    def _write_file_via_daemon_blocking(
        self,
        socket_path: Path,
        sandbox_path: str,
        content: bytes,
        mkdir_parents: bool,
        mode: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": sandbox_path,
            "content_size": len(content),
            "mkdir_parents": mkdir_parents,
        }
        if mode is not None:
            payload["mode"] = mode
        header_blob = encode_request(
            request_type=REQUEST_TYPE_WRITE_FILE,
            payload=payload,
        )
        sock = self._connect_daemon_socket(socket_path)
        try:
            sock.settimeout(DAEMON_FILE_OP_TIMEOUT_SECONDS)
            self._send_request_blob(sock, header_blob, content)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            return self._read_response_blob(sock)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _read_file_via_daemon_blocking(
        self,
        socket_path: Path,
        sandbox_path: str,
    ) -> tuple[dict[str, Any], bytes]:
        header_blob = encode_request(
            request_type=REQUEST_TYPE_READ_FILE,
            payload={"path": sandbox_path},
        )
        sock = self._connect_daemon_socket(socket_path)
        try:
            sock.settimeout(DAEMON_FILE_OP_TIMEOUT_SECONDS)
            self._send_request_blob(sock, header_blob, None)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            response = self._read_response_blob(sock)
            content = b""
            if response.get("ok"):
                size = int(response.get("content_size") or 0)
                if size > 0:
                    content = recv_frame(sock, MAX_FILE_BYTES)
                    if len(content) != size:
                        raise ConnectionError(
                            f"daemon returned {len(content)} bytes but advertised {size}",
                        )
            return response, content
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _list_dir_via_daemon_blocking(
        self, call: _DaemonListDirCall,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": call.sandbox_path,
            "recursive": call.recursive,
            "include_files": call.include_files,
            "include_dirs": call.include_dirs,
        }
        if call.max_depth is not None:
            payload["max_depth"] = call.max_depth
        header_blob = encode_request(
            request_type=REQUEST_TYPE_LIST_DIR,
            payload=payload,
        )
        sock = self._connect_daemon_socket(call.socket_path)
        try:
            sock.settimeout(DAEMON_FILE_OP_TIMEOUT_SECONDS)
            self._send_request_blob(sock, header_blob, None)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            return self._read_response_blob(sock)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _file_op_result_from_response(response: dict[str, Any]) -> RuntimeFileOpResult:
        if response.get("ok"):
            return RuntimeFileOpResult(ok=True)
        return RuntimeFileOpResult(
            ok=False,
            error=str(response.get("error") or "io_error"),
            errno=int(response["errno"]) if isinstance(response.get("errno"), int) else None,
            detail=str(response.get("stderr") or response.get("detail") or ""),
        )

    async def write_file(
        self,
        sandbox_id: str,
        sandbox_path: str,
        content: bytes,
        *,
        mkdir_parents: bool = True,
        mode: int | None = None,
    ) -> RuntimeFileOpResult:
        socket_path = self._control_socket_host_path(sandbox_id)
        if socket_path is None or not self._daemon_ipc_available(sandbox_id):
            return self._file_op_unavailable(sandbox_id)
        if len(content) > MAX_FILE_BYTES:
            return RuntimeFileOpResult(
                ok=False,
                error="too_large",
                detail=(
                    f"content size {len(content)} exceeds limit {MAX_FILE_BYTES}"
                ),
            )

        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                None,
                self._write_file_via_daemon_blocking,
                socket_path,
                sandbox_path,
                content,
                mkdir_parents,
                mode,
            )
        except (ConnectionError, ValueError) as exc:
            # ``ValueError`` already covers ``json.JSONDecodeError`` (G.ERR.09).
            return self._file_op_transport_failure(sandbox_id, exc)
        except socket.timeout as exc:
            return self._file_op_transport_failure(sandbox_id, exc)
        except OSError as exc:
            if exc.errno in FATAL_DAEMON_ERRNOS:
                return self._file_op_transport_failure(sandbox_id, exc)
            if exc.errno in RECOVERABLE_DAEMON_ERRNOS:
                return self._file_op_transport_failure(sandbox_id, exc, fatal=False)
            raise
        return self._file_op_result_from_response(response)

    async def read_file(
        self,
        sandbox_id: str,
        sandbox_path: str,
    ) -> RuntimeFileOpResult:
        socket_path = self._control_socket_host_path(sandbox_id)
        if socket_path is None or not self._daemon_ipc_available(sandbox_id):
            return self._file_op_unavailable(sandbox_id)

        loop = asyncio.get_running_loop()
        try:
            response, content = await loop.run_in_executor(
                None,
                self._read_file_via_daemon_blocking,
                socket_path,
                sandbox_path,
            )
        except (ConnectionError, ValueError) as exc:
            # ``ValueError`` already covers ``json.JSONDecodeError`` (G.ERR.09).
            return self._file_op_transport_failure(sandbox_id, exc)
        except socket.timeout as exc:
            return self._file_op_transport_failure(sandbox_id, exc)
        except OSError as exc:
            if exc.errno in FATAL_DAEMON_ERRNOS:
                return self._file_op_transport_failure(sandbox_id, exc)
            if exc.errno in RECOVERABLE_DAEMON_ERRNOS:
                return self._file_op_transport_failure(sandbox_id, exc, fatal=False)
            raise

        if response.get("ok"):
            return RuntimeFileOpResult(ok=True, content=content)
        return self._file_op_result_from_response(response)

    async def list_dir(
        self,
        sandbox_id: str,
        sandbox_path: str,
        *,
        recursive: bool = False,
        max_depth: int | None = None,
        include_files: bool = True,
        include_dirs: bool = True,
    ) -> RuntimeFileOpResult:
        socket_path = self._control_socket_host_path(sandbox_id)
        if socket_path is None or not self._daemon_ipc_available(sandbox_id):
            return self._file_op_unavailable(sandbox_id)

        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                None,
                self._list_dir_via_daemon_blocking,
                _DaemonListDirCall(
                    socket_path=socket_path,
                    sandbox_path=sandbox_path,
                    recursive=recursive,
                    max_depth=max_depth,
                    include_files=include_files,
                    include_dirs=include_dirs,
                ),
            )
        except (ConnectionError, ValueError) as exc:
            # ``ValueError`` already covers ``json.JSONDecodeError`` (G.ERR.09).
            return self._file_op_transport_failure(sandbox_id, exc)
        except socket.timeout as exc:
            return self._file_op_transport_failure(sandbox_id, exc)
        except OSError as exc:
            if exc.errno in FATAL_DAEMON_ERRNOS:
                return self._file_op_transport_failure(sandbox_id, exc)
            if exc.errno in RECOVERABLE_DAEMON_ERRNOS:
                return self._file_op_transport_failure(sandbox_id, exc, fatal=False)
            raise

        if response.get("ok"):
            items = response.get("items")
            if not isinstance(items, list):
                items = []
            return RuntimeFileOpResult(ok=True, items=items)
        return self._file_op_result_from_response(response)

    async def _send_daemon_shutdown(self, sandbox_id: str) -> None:
        """Politely ask the daemon to drain and exit before sending SIGTERM."""
        socket_path = self._control_socket_host_path(sandbox_id)
        if socket_path is None or not self._daemon_socket_ready.get(sandbox_id, False):
            return
        if not socket_path.exists():
            return

        def _ask_shutdown(path: Path) -> None:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(DAEMON_SHUTDOWN_TIMEOUT_SECONDS)
            try:
                sock.connect(str(path))
                send_frame(
                    sock,
                    encode_request(request_type=REQUEST_TYPE_SHUTDOWN),
                )
                try:
                    recv_frame(sock, MAX_HEADER_BYTES)
                except OSError:
                    # ``OSError`` already covers ``ConnectionError`` (G.ERR.09).
                    pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _ask_shutdown, socket_path),
                timeout=DAEMON_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, OSError) as exc:
            logger.debug(
                "Failed to send daemon shutdown for %s: %s",
                sandbox_id,
                exc,
            )

    def _ensure_exec_semaphore(self) -> asyncio.Semaphore:
        """Lazily build the per-runtime ``exec`` admission semaphore.

        Cannot be created in ``__init__`` because ``ProcessRuntime`` may
        be instantiated before any event loop is running (e.g. CLI tools
        or pytest fixtures), and ``asyncio.Semaphore`` will then bind to
        the wrong loop. By creating it the first time ``exec`` actually
        awaits inside the server's loop, we guarantee it is bound to the
        loop that will end up signalling it.
        """
        sem = self._exec_semaphore
        if sem is None:
            sem = asyncio.Semaphore(self._exec_concurrency_limit)
            self._exec_semaphore = sem
        return sem

    async def exec(
        self,
        sandbox_id: str,
        request: RuntimeExecRequest,
    ) -> ExecResult:
        """Execute a command in the sandbox via the daemon IPC channel.

        Each sandbox owns a long-running in-sandbox daemon (PID 1 of its
        own PID namespace) that bubblewrap set up once at sandbox creation
        with the full namespace/mount/seccomp/Landlock envelope. ``exec``
        therefore boils down to a single Unix-socket roundtrip; bubblewrap
        is **not** spawned per call. The same security envelope is
        inherited by every command the daemon ``fork+exec``s.

        Concurrency is gated by ``_exec_semaphore`` to keep the number of
        in-flight CPU-heavy commands below ``JIUWENBOX_EXEC_CONCURRENCY``
        (defaulted to the box's usable CPU count). Without this cap,
        running more concurrent ``exec`` calls than there are CPUs causes
        super-linear latency growth - the typical "throughput collapse"
        pattern of oversubscribed CPU-bound workloads. File-ops fast
        paths (``write_file``/``read_file``/``list_dir``) are *not*
        throttled because they are I/O-bound and barely consume CPU.
        """
        logger.info(
            "Executing command in sandbox %s via daemon IPC: %s",
            sandbox_id,
            _summarize_command(list(request.command)),
        )
        semaphore = self._ensure_exec_semaphore()
        async with semaphore:
            return await self._exec_via_daemon(sandbox_id, request)

    async def exec_background(
        self,
        sandbox_id: str,
        request: RuntimeExecRequest,
    ) -> BackgroundExecResult:
        """Start a command and return after the process is created."""
        prepared = self._prepare_exec_invocation(sandbox_id, request)
        if prepared is None:
            return BackgroundExecResult(
                started=False,
                command=list(request.command),
                error_message="No policy found for sandbox",
            )
        bwrap_cmd, seccomp_fd = prepared

        process_env = {**os.environ, **(request.env or {})}

        self._reap_background_processes(sandbox_id)
        log_index = len(self._background_processes.get(sandbox_id, []))
        log_file = self._log_dir / f"{sandbox_id}-background-{log_index}.log"
        stdin_target = subprocess.PIPE if request.stdin_data is not None else subprocess.DEVNULL
        pass_fds = (seccomp_fd,) if seccomp_fd is not None else ()
        try:
            log_fd = open(log_file, "ab")
            try:
                proc = subprocess.Popen(
                    bwrap_cmd,
                    stdin=stdin_target,
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    env=process_env,
                    pass_fds=pass_fds,
                    start_new_session=True,
                )
                if request.stdin_data is not None and proc.stdin is not None:
                    proc.stdin.write(request.stdin_data)
                    proc.stdin.close()
            finally:
                log_fd.close()
        except Exception as exc:
            return BackgroundExecResult(
                started=False,
                command=list(request.command),
                error_message=str(exc),
            )
        finally:
            if seccomp_fd is not None:
                _safe_close_fd(seccomp_fd)

        await asyncio.sleep(0.2)
        if proc.poll() is not None:
            log_tail = ""
            if log_file.exists():
                log_tail = log_file.read_text(encoding="utf-8", errors="replace")[-4000:]
            return BackgroundExecResult(
                started=False,
                pid=proc.pid,
                command=list(request.command),
                error_message=(
                    f"Background command exited during startup with code "
                    f"{proc.returncode}; log={log_file}; log_tail={log_tail}"
                ),
            )

        self._background_processes.setdefault(sandbox_id, []).append(proc)
        logger.info(
            "Started background command in sandbox %s (pid=%d): %s",
            sandbox_id,
            proc.pid,
            _summarize_command(list(request.command)),
        )
        logger.debug(
            "Background bwrap command for sandbox %s: %s",
            sandbox_id,
            bwrap_cmd,
        )
        return BackgroundExecResult(
            started=True,
            pid=proc.pid,
            command=list(request.command),
        )

    async def cleanup(self, sandbox_id: str) -> None:
        await self.stop(sandbox_id)
        self._processes.pop(sandbox_id, None)
        policy_path = self._policy_paths.pop(sandbox_id, None)
        network_mode = self._network_modes.pop(sandbox_id, None)
        self._runtime_policies.pop(sandbox_id, None)
        self._policy_binds.pop(sandbox_id, None)
        self._seccomp_bpf.pop(sandbox_id, None)
        self._landlock_payloads.pop(sandbox_id, None)
        if network_mode is None and policy_path is not None and policy_path.exists():
            try:
                network_mode = self._load_policy(policy_path).network.mode
            except Exception:
                logger.warning(
                    "Failed to reload policy for sandbox %s during namespace cleanup",
                    sandbox_id,
                    exc_info=True,
                )

        if network_mode == NetworkMode.ISOLATED:
            namespace = self._netns_names.pop(
                sandbox_id,
                network_module.netns_name_for_sandbox(sandbox_id),
            )
            if network_module.namespace_exists(namespace):
                network_module.delete_named_namespace(namespace)
        else:
            self._netns_names.pop(sandbox_id, None)
        self._remove_sandbox_host_firewall_rules(sandbox_id)

        directory_root = self._directory_roots.pop(sandbox_id, None)
        if directory_root is not None and directory_root.exists():
            shutil.rmtree(directory_root, ignore_errors=True)
        file_root = self._file_roots.pop(sandbox_id, None)
        if file_root is not None and file_root.exists():
            shutil.rmtree(file_root, ignore_errors=True)
        launcher_dir = self._launcher_dirs.pop(sandbox_id, None)
        if launcher_dir is not None and launcher_dir.exists():
            shutil.rmtree(launcher_dir, ignore_errors=True)
        control_dir = self._control_dirs.pop(sandbox_id, None)
        if control_dir is not None and control_dir.exists():
            shutil.rmtree(control_dir, ignore_errors=True)
        self._daemon_socket_ready.pop(sandbox_id, None)

        log_file = self._log_dir / f"{sandbox_id}.log"
        log_file.unlink(missing_ok=True)
        for background_log in self._log_dir.glob(f"{sandbox_id}-background-*.log"):
            background_log.unlink(missing_ok=True)
