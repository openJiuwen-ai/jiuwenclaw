# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""编码 CLI 引导安装：缺失即调用 setup 脚本安装.

Windows 使用 ``scripts/setup_coding_cli.ps1``，Unix 使用 ``scripts/setup_coding_cli.sh``。
安装策略（npm / 国内镜像等）封装在脚本中，便于用户单独手动运行。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_INSTALL_TIMEOUT_S = float(os.getenv("CODING_CLI_INSTALL_TIMEOUT", "600"))


@dataclass
class CliInstallStatus:
    engine_kind: str
    running: bool = False
    last_detail: str = ""


_INSTALL_LOCK = threading.Lock()
_INSTALL_STATUS: dict[str, CliInstallStatus] = {}


def _setup_script_name() -> str:
    return "setup_coding_cli.ps1" if sys.platform == "win32" else "setup_coding_cli.sh"


def _setup_script_path() -> Path | None:
    """定位 setup_coding_cli 脚本（仓库根 / 包内 resources / PyInstaller 捆绑）."""
    script_name = _setup_script_name()
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "jiuwenavatar" / "resources" / "scripts" / script_name)
        candidates.append(Path(sys.executable).resolve().parent / "scripts" / script_name)

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "scripts" / script_name)
        if (parent / ".git").exists():
            break
    try:
        from jiuwenavatar.common.utils import _find_package_root

        pkg_root = _find_package_root()
        if pkg_root is not None:
            candidates.append(pkg_root / "resources" / "scripts" / script_name)
    except Exception:
        pass

    for c in candidates:
        if c.is_file():
            return c
    return None


def _run_setup_script(script: Path, engine_kind: str) -> subprocess.CompletedProcess[str]:
    if sys.platform == "win32":
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            engine_kind,
        ]
    else:
        cmd = ["bash", str(script), engine_kind]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_INSTALL_TIMEOUT_S,
        check=False,
    )


def _auto_install_enabled() -> bool:
    return os.getenv("JIUWEN_AUTO_INSTALL_CODING_CLI", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _install_status(engine_kind: str) -> CliInstallStatus:
    return _INSTALL_STATUS.setdefault(engine_kind, CliInstallStatus(engine_kind=engine_kind))


def ensure_cli_installed(engine_kind: str) -> str:
    """缺失即安装指定引擎的 CLI；返回一行诊断信息（不抛异常）.

    通过环境变量 ``JIUWEN_AUTO_INSTALL_CODING_CLI=0`` 可禁用自动安装。
    """
    if not _auto_install_enabled():
        return "auto-install disabled (JIUWEN_AUTO_INSTALL_CODING_CLI=0)"

    script = _setup_script_path()
    if script is None:
        return f"{_setup_script_name()} not found; install the CLI manually"

    logger.info("[coding.bootstrap] installing CLI for engine=%s via %s", engine_kind, script)
    try:
        proc = _run_setup_script(script, engine_kind)
    except subprocess.TimeoutError:
        return f"install timed out after {_INSTALL_TIMEOUT_S:.0f}s"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[coding.bootstrap] install failed: %s", exc)
        return f"install error: {exc}"

    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    if proc.returncode == 0:
        return f"installed via {script.name}: {tail[0]}"
    err = (proc.stderr or "").strip().splitlines()[-1:] or [""]
    return f"install script exited {proc.returncode}: {err[0] or tail[0]}"


def start_cli_install_background(engine_kind: str) -> str:
    """后台安装指定 CLI；若已有安装任务则复用状态，不阻塞调用方。"""
    if not _auto_install_enabled():
        return "auto-install disabled (JIUWEN_AUTO_INSTALL_CODING_CLI=0)"

    with _INSTALL_LOCK:
        status = _install_status(engine_kind)
        if status.running:
            return "install already running"
        status.running = True
        status.last_detail = "install queued"

    def worker() -> None:
        detail = ensure_cli_installed(engine_kind)
        with _INSTALL_LOCK:
            status = _install_status(engine_kind)
            status.running = False
            status.last_detail = detail
        logger.info("[coding.bootstrap] background install done: engine=%s detail=%s", engine_kind, detail)

    thread = threading.Thread(
        target=worker,
        name=f"coding-cli-install-{engine_kind}",
        daemon=True,
    )
    thread.start()
    return "install started in background"


def get_cli_install_status(engine_kind: str) -> CliInstallStatus:
    with _INSTALL_LOCK:
        status = _install_status(engine_kind)
        return CliInstallStatus(
            engine_kind=status.engine_kind,
            running=status.running,
            last_detail=status.last_detail,
        )
