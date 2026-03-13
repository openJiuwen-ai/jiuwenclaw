# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Launch JiuwenClaw frontend/backend services with one command."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

from jiuwenclaw.utils import get_root_dir, is_package_installation

# Runtime data root:
# - source mode: repository root
# - package mode: ~/.jiuwenclaw
DATA_ROOT = get_root_dir()

# Package source root:
# - source mode: <repo>/jiuwenclaw
# - package mode: <site-packages>/jiuwenclaw
PACKAGE_DIR = Path(__file__).resolve().parent

# Frontend dev project root (contains package.json)
WEB_DEV_DIR = PACKAGE_DIR / "web"


def _build_commands(mode: str) -> list[tuple[str, list[str], Path]]:
    python_cmd = sys.executable
    commands: list[tuple[str, list[str], Path]] = []

    # Always launch package modules so source/package layouts behave the same.
    if mode in ("all", "app", "dev"):
        commands.append(("app", [python_cmd, "-m", "jiuwenclaw.app"], DATA_ROOT))
    if mode == "all":
        commands.append(("web", [python_cmd, "-m", "jiuwenclaw.app_web"], DATA_ROOT))
    elif mode == "web":
        commands.append(("web", [python_cmd, "-m", "jiuwenclaw.app_web"], DATA_ROOT))
    elif mode == "dev":
        package_json = WEB_DEV_DIR / "package.json"
        if is_package_installation() and not package_json.exists():
            raise RuntimeError(
                "dev mode is unavailable in package installation; "
                "please run app/web mode, or use source checkout for frontend dev."
            )
        commands.append(("web-dev", ["npm", "run", "dev"], WEB_DEV_DIR))
    return commands


def _start_process(name: str, cmd: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    print(f"[start_services] starting {name}: {' '.join(cmd)} (cwd={cwd})")
    return subprocess.Popen(cmd, cwd=str(cwd))


def _terminate_processes(processes: dict[str, subprocess.Popen[bytes]]) -> None:
    for name, proc in processes.items():
        if proc.poll() is None:
            print(f"[start_services] terminating {name} (pid={proc.pid})")
            proc.terminate()

    deadline = time.time() + 8
    while time.time() < deadline:
        if all(proc.poll() is not None for proc in processes.values()):
            return
        time.sleep(0.2)

    for name, proc in processes.items():
        if proc.poll() is None:
            print(f"[start_services] killing {name} (pid={proc.pid})")
            proc.kill()


def _run(mode: str) -> int:
    commands = _build_commands(mode)
    if not commands:
        print(f"[start_services] no commands to run for mode: {mode}")
        return 2

    processes: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for name, cmd, cwd in commands:
            processes[name] = _start_process(name, cmd, cwd)

        while True:
            for name, proc in processes.items():
                code = proc.poll()
                if code is not None:
                    print(f"[start_services] {name} exited with code {code}")
                    return code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[start_services] keyboard interrupt received, shutting down...")
        return 130
    finally:
        _terminate_processes(processes)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch JiuwenClaw services (frontend/backend).",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=["all", "web", "app", "dev"],
        help="Start mode: all (default), web, app, or dev.",
    )
    return parser.parse_args()


def main() -> None:
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    args = _parse_args()
    exit_code = _run(args.mode)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
