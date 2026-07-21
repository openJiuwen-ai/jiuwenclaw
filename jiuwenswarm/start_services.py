# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Launch JiuWenSwarm frontend/backend services with one command.

Supports ``--dotenv <path>`` for multi-instance isolation.

Multi-instance management commands:
- ``--list``: List all instances with their status
- ``--status <name>``: Show detailed status of an instance
- ``--stop <name>``: Stop a running instance
- ``--restart <name>``: Restart an instance
- ``--name <name>``: Start a named instance with mode
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# --- Early --dotenv parsing (before jiuwenswarm imports) ---
from jiuwenswarm.dotenv_early import parse_dotenv_early
parse_dotenv_early("jiuwenswarm-start")

# --- Now safe to import jiuwenswarm modules ---
from jiuwenswarm.common.utils import get_root_dir, get_user_workspace_dir, is_package_installation
from jiuwenswarm.instance_manager import (
    InstanceConfig,
    InstanceLock,
    InstanceStatus,
    calculate_instance_ports,
    create_bootstrap_env,
    format_status_line,
    get_default_instance_status,
    get_instance_config,
    get_instance_status,
    is_port_available,
    list_all_instances,
    stop_instance_process,
    stop_process_by_pid,
    validate_instance_name,
    write_pid_file,
    PORT_TYPES,
    compute_auto_port,
)

# Runtime data root:
# - source mode: repository root
# - package mode: ~/.jiuwenswarm
DATA_ROOT = get_root_dir()

# Package source root:
# - source mode: <repo>/jiuwenswarm
# - package mode: <site-packages>/jiuwenswarm
PACKAGE_DIR = Path(__file__).resolve().parent

# Frontend dev project root (contains package.json)
WEB_DEV_DIR = PACKAGE_DIR / "channels" / "web" / "frontend"


# =============================================================================
# Instance Command Base Class - Unified handling for all instance operations
# =============================================================================

class InstanceCommand:
    """Unified base class for instance management commands.

    Handles:
    - Instance name validation
    - Config loading (default or named)
    - Status retrieval
    - Error message formatting

    Usage:
        cmd = InstanceCommand(name)
        error = cmd.validate_and_load()
        if error:
            return error  # Already printed error message
        # Now cmd.config, cmd.status are ready
    """

    def __init__(self, name: str):
        self.name = name
        self.is_default = (name == "default")
        self.config: InstanceConfig | None = None
        self.status: InstanceStatus | None = None

    def validate_and_load(self) -> int | None:
        """Validate instance name and load config/status.

        Returns:
            Error code if validation/loading failed, None if successful.
            Error message is already printed to stdout.
        """
        # Handle default instance specially
        if self.is_default:
            self.status = get_default_instance_status()
            # Build synthetic config for default instance
            workspace = get_user_workspace_dir()
            ports = {pt: compute_auto_port(pt, 0) for pt in PORT_TYPES}
            self.config = InstanceConfig(name="default", workspace=workspace, ports=ports)
            return None

        # Validate instance name
        error = validate_instance_name(self.name)
        if error:
            logging.info(f"[start_services] ERROR: {error}")
            return 1

        # Load instance config from instances.yaml
        self.config = get_instance_config(self.name)
        if self.config is None:
            logging.info(f"[start_services] ERROR: Instance '{self.name}' not found in instances.yaml")
            logging.info(f"[start_services] Run 'jiuwenswarm-init --name {self.name}' to create it.")

            return 1

        # Get current status
        self.status = get_instance_status(self.config)
        return None

    def check_workspace_exists(self) -> int | None:
        """Check if workspace directory exists.

        Returns:
            Error code if workspace missing, None if exists.
        """
        if self.config is None:
            return 1
        if not self.config.workspace.exists():
            logging.info(f"[start_services] ERROR: Workspace directory not found: {self.config.workspace}")
            logging.info(f"[start_services] Run 'jiuwenswarm-init --name {self.name}' to create it.")

            return 1
        return None

    def check_running(self) -> bool | None:
        """Check if instance is running.

        Returns:
            True if running, False if not running, None if status unavailable.
        """
        if self.status is None:
            return None
        return self.status.running

    def check_ports_available(self) -> int | None:
        """Check if all instance ports are available (for start operation).

        Returns:
            Error code if port conflicts, None if all ports available.
        """
        if self.config is None:
            return 1

        logging.info(f"[start_services] Checking ports for instance '{self.name}'...")
        conflicts = []

        for port_type, port in self.config.ports.items():
            if not is_port_available("127.0.0.1", port):
                conflicts.append((port_type, port))
                logging.info(f"  ✗ {port_type}: {port} - already in use")
            else:
                logging.info(f"  ✓ {port_type}: {port} - available")

        if conflicts:
            logging.info("[start_services] ERROR: Port conflicts detected, cannot start instance.")
            return 1

        return None


def print_instance_details(status: InstanceStatus) -> None:
    """Print detailed instance status in unified format.

    Args:
        status: InstanceStatus to display
    """
    logging.info(f"Instance:     {status.name}")
    logging.info(f"Status:       {'running' if status.running else 'stopped'}")
    logging.info(f"PID:          {status.pid or '-'}")
    logging.info(f"Workspace:    {status.workspace}")
    logging.info("Ports:")

    for port_type in PORT_TYPES:
        port = status.ports.get(port_type, 0)
        logging.info(f"  {port_type}: {port}")

    if status.started_at:
        from datetime import datetime
        started_dt = datetime.fromtimestamp(status.started_at)
        logging.info(f"Started at:   {started_dt.isoformat()}")


def do_stop_instance(cmd: InstanceCommand) -> int:
    """Execute stop operation for an instance.

    Args:
        cmd: InstanceCommand with validated config/status

    Returns:
        Exit code
    """
    if cmd.status is None or cmd.config is None:
        return 1

    pid = cmd.status.pid
    logging.info(f"[start_services] Stopping instance '{cmd.name}' (PID={pid})...")

    if cmd.is_default:
        success = stop_process_by_pid(pid, timeout=10.0)
    else:
        success = stop_instance_process(cmd.config, timeout=10.0)

    if success:
        logging.info(f"[start_services] Instance '{cmd.name}' stopped.")
        return 0
    else:
        logging.info(f"[start_services] Failed to stop instance '{cmd.name}'.")
        return 1


def _run_instance_with_pid(commands: list[tuple[str, list[str], Path]],
                           config: InstanceConfig) -> int:
    """Run processes for an instance with PID file management.

    Args:
        commands: List of (name, command, cwd) tuples
        config: InstanceConfig for PID file writing

    Returns:
        Exit code
    """
    processes: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for cmd_name, cmd_args, cwd in commands:
            processes[cmd_name] = _start_process(cmd_name, cmd_args, cwd)

        write_pid_file(config, os.getpid(), time.time())
        logging.info(f"[start_services] Instance '{config.name}' started")

        _wait_for_services_ready(config.ports, processes)

        while True:
            for cmd_name, proc in processes.items():
                code = proc.poll()
                if code is not None:
                    logging.info(f"[start_services] {cmd_name} exited with code {code}")
                    return code
            time.sleep(0.5)

    except KeyboardInterrupt:
        logging.info("\n[start_services] Keyboard interrupt received, shutting down...")
        return 130
    finally:
        _terminate_processes(processes)


def _build_commands(mode: str, dotenv_path: Path | None = None) -> list[tuple[str, list[str], Path]]:
    """Build startup commands for instance.

    Args:
        mode: Start mode (all/app/web/dev)
        dotenv_path: Optional path to bootstrap .env for named instance

    Returns:
        List of (name, command, cwd) tuples
    """
    python_cmd = sys.executable
    commands: list[tuple[str, list[str], Path]] = []

    # Build --dotenv argument if provided
    dotenv_arg = ["--dotenv", str(dotenv_path)] if dotenv_path else []

    if mode in ("all", "app", "dev"):
        cmd = [python_cmd, "-m", "jiuwenswarm.app"] + dotenv_arg
        commands.append(("app", cmd, DATA_ROOT))

    if mode in ("all", "web"):
        cmd = [python_cmd, "-m", "jiuwenswarm.channels.web.app_web"] + dotenv_arg
        commands.append(("web", cmd, DATA_ROOT))

    elif mode == "dev":
        package_json = WEB_DEV_DIR / "package.json"
        if is_package_installation() and not package_json.exists():
            raise RuntimeError(
                "dev mode is unavailable in package installation; "
                "please run app/web mode, or use source checkout for frontend dev."
            )
        # npm doesn't use --dotenv, ports passed via inherited env vars
        npm_cmd = ["npm", "run", "dev"]
        if sys.platform == "win32":
            npm_cmd = ["cmd", "/c", *npm_cmd]
        commands.append(("web-dev", npm_cmd, WEB_DEV_DIR))

    return commands


def _start_process(name: str, cmd: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    """Start a single subprocess."""
    import json
    logging.info(f"[start_services] starting {name}: {' '.join(cmd)} (cwd={cwd})")
    env = os.environ.copy()
    env["JIUWENSWARM_START_CMD"] = json.dumps(sys.argv[:])
    return subprocess.Popen(cmd, cwd=str(cwd), env=env)


def _terminate_processes(processes: dict[str, subprocess.Popen[bytes]]) -> None:
    """Terminate all running processes gracefully."""
    for name, proc in processes.items():
        if proc.poll() is None:
            logging.info(f"[start_services] terminating {name} (pid={proc.pid})")
            proc.terminate()

    deadline = time.time() + 8
    while time.time() < deadline:
        if all(proc.poll() is not None for proc in processes.values()):
            return
        time.sleep(0.2)

    for name, proc in processes.items():
        if proc.poll() is None:
            logging.info(f"[start_services] killing {name} (pid={proc.pid})")
            proc.kill()


def _resolve_runtime_ports() -> dict[str, int]:
    """Resolve default-instance ports from env overrides with BASE_PORTS fallback."""
    env_map = {
        "agent_server": "AGENT_SERVER_PORT",
        "web": "WEB_PORT",
        "gateway": "GATEWAY_PORT",
        "frontend": "FRONTEND_PORT",
    }
    ports = {pt: compute_auto_port(pt, 0) for pt in PORT_TYPES}
    for port_type, env_name in env_map.items():
        raw = os.environ.get(env_name)
        if not raw:
            continue
        try:
            ports[port_type] = int(raw)
        except ValueError:
            logging.info(
                "[start_services] ignore invalid %s=%r, keep port %s",
                env_name,
                raw,
                ports.get(port_type),
            )
    return ports


def _print_port_banner(
    targets: list[tuple[str, int, str]],
    ready: dict[str, bool],
) -> None:
    """Print a user-facing port / access-URL summary for issue #1059."""
    all_ready = all(ready[label] for label, _, _ in targets)
    title = (
        "服务已启动，端口信息如下："
        if all_ready
        else "服务启动中，端口信息如下："
    )
    label_width = max(len(label) for label, _, _ in targets)
    logging.info("")
    logging.info("=" * 64)
    logging.info(f"  {title}")
    for label, _port, url in targets:
        mark = "✓" if ready[label] else "…"
        logging.info(f"  {mark} {label:<{label_width}}  {url}")
    logging.info("=" * 64)
    logging.info("")


def _wait_for_services_ready(
    ports: dict[str, int],
    processes: dict[str, subprocess.Popen[bytes]],
    *,
    overall_timeout: float | None = None,
) -> None:
    """Wait for services to be ready and log a complete access / port summary.

    Prints the access-URL banner as soon as Web UI is ready (or at the end if
    there is no Web UI target). Continues probing remaining ports afterward.
    Aborts early if a launched subprocess has already exited.
    """
    import socket

    def _proc_alive(name: str) -> bool:
        proc = processes.get(name)
        return proc is not None and proc.poll() is None

    def _any_required_dead() -> bool:
        for name in ("app", "web", "web-dev"):
            proc = processes.get(name)
            if proc is not None and proc.poll() is not None:
                return True
        return False

    def _port_open(port: int) -> bool:
        """Return True if port accepts TCP on IPv4 or IPv6 localhost."""
        # Vite on Windows often binds ::1 only; backend services bind 127.0.0.1.
        for host, family in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.4)
                    sock.connect((host, port))
                    return True
            except OSError:
                continue
        return False

    # (label, port, access_url) — only entries for processes actually launched.
    targets: list[tuple[str, int, str]] = []

    if _proc_alive("app"):
        agent_port = ports.get("agent_server", 0)
        gateway_port = ports.get("gateway", 0)
        web_port = ports.get("web", 0)
        if agent_port:
            targets.append(("AgentServer WebSocket", agent_port, f"ws://localhost:{agent_port}"))
        if gateway_port:
            targets.append(("Gateway HTTP", gateway_port, f"http://localhost:{gateway_port}"))
        if web_port:
            targets.append(("WebChannel WebSocket", web_port, f"ws://localhost:{web_port}/ws"))

    frontend_alive = _proc_alive("web") or _proc_alive("web-dev")
    frontend_port = ports.get("frontend", 0)
    if frontend_alive and frontend_port:
        # Web UI first in the user-facing banner.
        targets.insert(0, ("Web UI", frontend_port, f"http://localhost:{frontend_port}"))

    if not targets:
        return

    if overall_timeout is None:
        overall_timeout = 45.0 if "web-dev" in processes else 30.0
    deadline = time.time() + overall_timeout
    ready: dict[str, bool] = {label: False for label, _, _ in targets}
    banner_printed = False
    banner_was_partial = False

    while time.time() < deadline and not all(ready.values()):
        if _any_required_dead():
            logging.info(
                "[start_services] a required process exited during startup wait; "
                "stop waiting for remaining ports"
            )
            break

        for label, port, _url in targets:
            if ready[label]:
                continue
            if _port_open(port):
                ready[label] = True
                logging.info(f"[start_services] ✓ {label} ready (port {port})")

        # CR-001: surface Web UI URL as soon as frontend is reachable.
        if not banner_printed and ready.get("Web UI"):
            _print_port_banner(targets, ready)
            banner_printed = True
            banner_was_partial = not all(ready.values())

        if not all(ready.values()):
            time.sleep(0.3)

    for label, port, _url in targets:
        if not ready[label]:
            logging.info(f"[start_services] ⏳ {label} starting... (port {port})")

    if not banner_printed:
        _print_port_banner(targets, ready)
    elif banner_was_partial:
        # Refresh so early "…" rows can become "✓" after backends catch up.
        _print_port_banner(targets, ready)


def _run_processes(commands: list[tuple[str, list[str], Path]]) -> int:
    """Run processes and wait for them.

    Args:
        commands: List of (name, command, cwd) tuples

    Returns:
        Exit code
    """
    processes: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for name, cmd, cwd in commands:
            processes[name] = _start_process(name, cmd, cwd)

        _wait_for_services_ready(_resolve_runtime_ports(), processes)

        while True:
            for name, proc in processes.items():
                code = proc.poll()
                if code is not None:
                    logging.info(f"[start_services] {name} exited with code {code}")
                    return code
            time.sleep(0.5)
    except KeyboardInterrupt:
        logging.info("\n[start_services] keyboard interrupt received, shutting down...")
        return 130
    finally:
        _terminate_processes(processes)


def _run(mode: str) -> int:
    """Run default instance (existing behavior)."""
    commands = _build_commands(mode)
    if not commands:
        logging.info(f"[start_services] no commands to run for mode: {mode}")
        return 2
    return _run_processes(commands)


def _action_list() -> int:
    """List all instances with their status.

    Returns:
        Exit code (0 for success)
    """
    statuses = list_all_instances(include_default=True)

    if not statuses:
        logging.info("[start_services] No instances configured.")
        logging.info("[start_services] Run 'jiuwenswarm-init' to initialize default instance.")

        return 0

    # Print table header
    logging.info("INSTANCE     STATUS     PID     WORKSPACE                               PORTS")
    logging.info("-" * 80)

    for status in statuses:
        logging.info(format_status_line(status))

    return 0


def _action_status(name: str) -> int:
    """Show detailed status of a specific instance.

    Args:
        name: Instance name

    Returns:
        Exit code
    """
    cmd = InstanceCommand(name)
    error = cmd.validate_and_load()
    if error is not None:
        return error

    print_instance_details(cmd.status)
    return 0


def _action_stop(name: str) -> int:
    """Stop a running instance.

    Args:
        name: Instance name

    Returns:
        Exit code
    """
    cmd = InstanceCommand(name)
    error = cmd.validate_and_load()
    if error is not None:
        return error

    running = cmd.check_running()
    if running is None:
        return 1
    if not running:
        logging.info(f"[start_services] Instance '{name}' is not running.")
        return 0

    # Execute stop
    return do_stop_instance(cmd)


def _action_restart(name: str, mode: str = "all") -> int:
    """Restart an instance (stop then start).

    Args:
        name: Instance name
        mode: Start mode (all/app/web/dev)

    Returns:
        Exit code
    """
    logging.info(f"[start_services] Restarting instance '{name}'...")

    # Validate and load first (common check for both stop and start)
    cmd = InstanceCommand(name)
    error = cmd.validate_and_load()
    if error is not None:
        return error

    # First stop (only if running)
    if cmd.check_running():
        stop_result = do_stop_instance(cmd)
        if stop_result != 0:
            logging.info("[start_services] Restart aborted: stop failed.")
            return stop_result
        # Wait for process to fully exit
        time.sleep(1)

    # Then start
    if cmd.is_default:
        start_result = _run(mode)
    else:
        start_result = _start_named_instance(name, mode)

    if start_result != 0:
        logging.info("[start_services] Restart failed: start failed.")
        return start_result

    logging.info(f"[start_services] Instance '{name}' restarted.")
    return 0


def _start_named_instance(name: str, mode: str) -> int:
    """Start a named instance.

    Args:
        name: Instance name
        mode: Start mode (all/app/web/dev)

    Returns:
        Exit code
    """
    cmd = InstanceCommand(name)
    if cmd.validate_and_load():
        return 1
    if cmd.check_workspace_exists():
        return 1
    if cmd.check_running():
        logging.info(f"[start_services] ERROR: Instance '{cmd.name}' is already running (PID={cmd.status.pid})")
        return 1
    if cmd.check_ports_available():
        return 1

    config = cmd.config
    if config is None:
        return 1

    # Acquire startup lock to prevent concurrent starts
    lock = InstanceLock(config)
    if not lock.acquire(timeout=5.0):
        logging.info(f"[start_services] ERROR: Instance '{name}' startup in progress by another process")
        logging.info(f"[start_services] Wait a few seconds or check if another terminal is starting this instance.")
        return 1

    try:
        dotenv_path = create_bootstrap_env(config)
        commands = _build_commands(mode, dotenv_path)
        if not commands:
            logging.info(f"[start_services] ERROR: No commands for mode: {mode}")
            return 2

        return _run_instance_with_pid(commands, config)
    finally:
        lock.release()


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Launch JiuWenSwarm services (frontend/backend).",
    )

    # Basic start parameter: mode (optional, default all)
    parser.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=["all", "web", "app", "dev"],
        help="Start mode: all (default), web, app, or dev.",
    )

    # Instance specification parameter
    parser.add_argument(
        "--name",
        metavar="<name>",
        help="Start a named instance from instances.yaml.",
    )

    # Management function parameters (mutually exclusive group)
    management_group = parser.add_mutually_exclusive_group()
    management_group.add_argument(
        "--list",
        action="store_true",
        help="List all instances with their status.",
    )
    management_group.add_argument(
        "--status",
        metavar="<name>",
        help="Show status of a specific instance.",
    )
    management_group.add_argument(
        "--stop",
        metavar="<name>",
        help="Stop a running instance.",
    )
    management_group.add_argument(
        "--restart",
        metavar="<name>",
        help="Restart an instance (stop then start).",
    )

    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> int | None:
    """Validate argument combinations, return error code or None if valid."""
    # Management params are mutually exclusive with mode (handled by argparse)
    # --name is mutually exclusive with management params (handled by argparse)

    # Additional validation: --restart needs valid mode
    if args.restart and args.mode not in ("all", "app", "web"):
        logging.info(f"[start_services] ERROR: Invalid mode '{args.mode}' for --restart")
        return 1

    return None


def _dispatch_action(args: argparse.Namespace) -> int:
    """Dispatch action based on parsed arguments.

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    # Validate arguments
    error_code = _validate_args(args)
    if error_code is not None:
        return error_code

    # --list: list all instances
    if args.list:
        return _action_list()

    # --status <name>: show specific instance status
    if args.status:
        return _action_status(args.status)

    # --stop <name>: stop specific instance
    if args.stop:
        return _action_stop(args.stop)

    # --restart <name>: restart specific instance
    if args.restart:
        return _action_restart(args.restart, args.mode)

    # --name <name>: start named instance
    if args.name:
        return _start_named_instance(args.name, args.mode)

    # Default: start default instance (existing behavior)
    return _run(args.mode)


def main() -> None:
    """CLI entry point."""
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    # Configure logging here (inside the entry point, not at module import time)
    # so that ``--list``/``--status`` output and service-startup messages are
    # actually emitted. The root logger otherwise keeps its default WARNING
    # level and every ``logging.info`` call is silently dropped — the process
    # runs but nothing is printed to the terminal. Locating the call here
    # (rather than at module scope) also avoids import-time side effects on the
    # global logger configuration.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    args = _parse_args()
    exit_code = _dispatch_action(args)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()