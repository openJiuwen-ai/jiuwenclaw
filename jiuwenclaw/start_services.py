# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Launch JiuwenClaw frontend/backend services with one command."""

from __future__ import annotations

import argparse
import os
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
ENTERPRISE_WEB_DEV_DIR = PACKAGE_DIR / "web_enterprise"
ENTERPRISE_WEB_DIST_DIR = ENTERPRISE_WEB_DEV_DIR / "dist"


def _enterprise_web_cmd(*, relay_only: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "jiuwenclaw.app_enterprise_web", "--dist", str(ENTERPRISE_WEB_DIST_DIR)]
    if relay_only:
        cmd.append("--relay-only")
    return cmd


def _build_commands(mode: str) -> list[tuple[str, list[str], Path]]:
    python_cmd = sys.executable
    commands: list[tuple[str, list[str], Path]] = []

    if mode == "dev-enterprise":
        package_json = ENTERPRISE_WEB_DEV_DIR / "package.json"
        if is_package_installation() and not package_json.exists():
            raise RuntimeError(
                "dev-enterprise mode is unavailable in package installation; "
                "please use source checkout with jiuwenclaw/web_enterprise."
            )
        commands.append(("enterprise-web-relay", _enterprise_web_cmd(relay_only=True), DATA_ROOT))

    elif mode in ("enterprise", "web-enterprise"):
        if is_package_installation() and not ENTERPRISE_WEB_DIST_DIR.exists():
            raise RuntimeError(
                "enterprise/web-enterprise mode is unavailable in package installation; "
                "web_enterprise/dist is missing."
            )
        commands.append(("web-enterprise", _enterprise_web_cmd(), DATA_ROOT))

    if mode in ("all", "app", "dev", "dev-enterprise", "enterprise"):
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
    elif mode == "dev-enterprise":
        upload_port = os.getenv("JIUWENCLAW_WEB_UPLOAD_PORT", "5174")
        commands.append((
            "upload-api",
            [python_cmd, "-m", "jiuwenclaw.app_web", "--upload-api-only", "--port", upload_port],
            DATA_ROOT,
        ))
        commands.append(("web-dev-enterprise", ["npm", "run", "dev"], ENTERPRISE_WEB_DEV_DIR))

    return commands


def _start_process(
    name: str,
    cmd: list[str],
    cwd: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    print(f"[start_services] starting {name}: {' '.join(cmd)} (cwd={cwd})")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    if sys.platform == "win32" and cmd:
        first = cmd[0].lower()
        if first in ("npm", "npx"):
            cmd = ["cmd", "/c", *cmd]
    return subprocess.Popen(cmd, cwd=str(cwd), env=env)


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

    enterprise_env: dict[str, str] | None = None
    if mode in ("enterprise", "dev-enterprise"):
        relay_port = os.getenv("ENTERPRISE_WEB_WS_PORT", os.getenv("WEB_PORT", "19000"))
        enterprise_env = {
            "ENTERPRISE_WEB_ENABLED": "true",
            "ENTERPRISE_WEB_GATEWAY_URL": os.getenv(
                "ENTERPRISE_WEB_GATEWAY_URL",
                f"ws://127.0.0.1:{relay_port}/gateway",
            ),
        }

    processes: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for name, cmd, cwd in commands:
            proc_env = enterprise_env if name == "app" and enterprise_env else None
            processes[name] = _start_process(name, cmd, cwd, extra_env=proc_env)
            if name in ("web-enterprise", "enterprise-web-relay"):
                time.sleep(0.8)
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
        choices=["all", "dev", "web", "app", "enterprise", "dev-enterprise", "web-enterprise"],
        help=(
            "Start mode: all (default, app + web static), dev (app + web Vite), web, app, "
            "enterprise (app + web_enterprise static), dev-enterprise (app + web_enterprise Vite) "
            "or web-enterprise (web_enterprise static)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    args = _parse_args()
    exit_code = _run(args.mode)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
