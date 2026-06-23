"""Launch Claw Manager backend and Manager Web with one command."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[2]
UI_DIST = PKG_ROOT / "web" / "dist"
LOG_TAG = "claw-manager-launcher"


class LaunchProfile(str, Enum):
    ALL = "all"
    API = "manager"
    UI = "web"


@dataclass(frozen=True)
class ServiceLaunchPlan:
    key: str
    argv: list[str]


def resolve_api_relay_url() -> str:
    host = os.getenv("MANAGER_REST_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = os.getenv("MANAGER_REST_PORT", "8765")
    return f"http://{host}:{port}"


def compose_launch_plan(profile: LaunchProfile) -> list[ServiceLaunchPlan]:
    py = sys.executable
    plan: list[ServiceLaunchPlan] = []

    if profile in {LaunchProfile.ALL, LaunchProfile.API}:
        plan.append(ServiceLaunchPlan("manager-api", [py, "-m", "jiuwenclaw_manager.main"]))

    if profile in {LaunchProfile.ALL, LaunchProfile.UI}:
        if not UI_DIST.is_dir():
            raise RuntimeError(
                f"manager web dist not found: {UI_DIST}; "
                "run `npm run build` under packages/jiuwenclaw-ee/claw_manager/web first."
            )
        relay = os.getenv("MANAGER_WEB_PROXY_TARGET", resolve_api_relay_url())
        ui_argv = [
            py,
            "-m",
            "jiuwenclaw_manager.manager_web",
            "--dist",
            str(UI_DIST),
            "--proxy-target",
            relay,
        ]
        web_host = os.getenv("MANAGER_WEB_HOST")
        if web_host:
            ui_argv.extend(["--host", web_host])
        web_port = os.getenv("MANAGER_WEB_PORT")
        if web_port:
            ui_argv.extend(["--port", web_port])
        plan.append(ServiceLaunchPlan("manager-ui", ui_argv))

    return plan


class ProcessGroupRunner:
    def __init__(self, plan: list[ServiceLaunchPlan]) -> None:
        self._plan = plan
        self._children: dict[str, subprocess.Popen[bytes]] = {}

    def run_until_exit(self) -> int:
        if not self._plan:
            print(f"[{LOG_TAG}] nothing to launch")
            return 2

        exit_code = 0
        try:
            for item in self._plan:
                print(f"[{LOG_TAG}] exec {item.key}: {' '.join(item.argv)}")
                self._children[item.key] = subprocess.Popen(item.argv)

            while True:
                time.sleep(0.5)
                for key, proc in self._children.items():
                    rc = proc.poll()
                    if rc is not None:
                        print(f"[{LOG_TAG}] {key} stopped (rc={rc})")
                        exit_code = rc
                        return exit_code
        except KeyboardInterrupt:
            print(f"\n[{LOG_TAG}] interrupted")
            return 130
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        for key, proc in self._children.items():
            if proc.poll() is not None:
                continue
            print(f"[{LOG_TAG}] SIGTERM -> {key} (pid={proc.pid})")
            proc.terminate()

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if all(proc.poll() is not None for proc in self._children.values()):
                return
            time.sleep(0.2)

        for key, proc in self._children.items():
            if proc.poll() is None:
                print(f"[{LOG_TAG}] SIGKILL -> {key} (pid={proc.pid})")
                proc.kill()


def main() -> None:
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    parser = argparse.ArgumentParser(description="Launch Claw Manager API and Web UI.")
    parser.add_argument(
        "profile",
        nargs="?",
        default=LaunchProfile.ALL.value,
        choices=[item.value for item in LaunchProfile],
        help="all: API+UI (default); manager: API only; web: UI only.",
    )
    args = parser.parse_args()

    plan = compose_launch_plan(LaunchProfile(args.profile))
    raise SystemExit(ProcessGroupRunner(plan).run_until_exit())


if __name__ == "__main__":
    main()
