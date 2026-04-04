# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Orchestrate AgentServer + Gateway in two processes (split layout, one command).

Runs ``jiuwenclaw.app_agentserver`` then ``jiuwenclaw.app_gateway`` with the same
environment as a normal CLI launch. Web RPC handlers live in ``app_web_handlers``.
"""

from __future__ import annotations

import subprocess
import sys
import time

from dotenv import load_dotenv

from jiuwenclaw.utils import get_user_workspace_dir, get_env_file, prepare_workspace


_config_file = get_user_workspace_dir() / "config" / "config.yaml"
if not _config_file.exists():
    prepare_workspace(overwrite=False)

load_dotenv(dotenv_path=get_env_file())


def main() -> None:
    python = sys.executable

    agent = subprocess.Popen([python, "-m", "jiuwenclaw.app_agentserver"])
    gateway = None
    try:
        time.sleep(0.4)
        gateway = subprocess.Popen([python, "-m", "jiuwenclaw.app_gateway"])
    except Exception:
        if agent.poll() is None:
            agent.terminate()
        raise

    procs: list[subprocess.Popen] = [agent] + ([gateway] if gateway else [])

    def _terminate_all() -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 12
        while time.time() < deadline:
            if all(p.poll() is not None for p in procs):
                break
            time.sleep(0.1)
        for p in procs:
            if p.poll() is None:
                p.kill()

    exit_code = 0
    try:
        while True:
            if agent.poll() is not None:
                exit_code = agent.returncode or 0
                break
            if gateway is not None and gateway.poll() is not None:
                exit_code = gateway.returncode or 0
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        _terminate_all()

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
