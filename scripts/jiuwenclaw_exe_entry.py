# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""PyInstaller 打包入口：根据参数分发到主应用或子命令。"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# frozen（PyInstaller 打包）模式下，macOS 双击 .app 启动时 cwd 为 "/"，
# 导致 openjiuwen 的默认日志路径 "./logs/" 解析为 "/logs/"（只读）。
# 在任何业务 import 之前，将 cwd 切换到用户可写目录。
if getattr(sys, "frozen", False):
    _safe_cwd = os.path.expanduser("~")
    try:
        os.chdir(_safe_cwd)
    except OSError:
        pass

_DESKTOP_RUN_AGENT = "--desktop-run-agent"
_DESKTOP_RUN_GATEWAY = "--desktop-run-gateway"

# 子进程 flag 集合，这些模式下需要将错误写入日志文件，
# 因为 console=False 的 PyInstaller exe 在 Windows 上无法通过 stderr 捕获错误。
_CHILD_FLAGS = {"--desktop-run-app", "--desktop-run-web", _DESKTOP_RUN_AGENT, _DESKTOP_RUN_GATEWAY}

# ── 单实例锁（在重量级 import 之前执行） ──────────────────────────
_SINGLE_INSTANCE_LOCK_FD: int | None = None


def _acquire_single_instance_lock() -> bool:
    """Try to acquire a single-instance lock.  Runs *before* any heavy imports."""
    global _SINGLE_INSTANCE_LOCK_FD
    lock_path = Path.home() / ".jiuwenclaw" / ".desktop.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, mode=0o644)
        os.set_inheritable(fd, False)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                os.close(fd)
                return False
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        _SINGLE_INSTANCE_LOCK_FD = fd
        return True
    except OSError:
        return False


def _release_single_instance_lock() -> None:
    global _SINGLE_INSTANCE_LOCK_FD
    if _SINGLE_INSTANCE_LOCK_FD is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_SINGLE_INSTANCE_LOCK_FD, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(_SINGLE_INSTANCE_LOCK_FD, fcntl.LOCK_UN)
        os.close(_SINGLE_INSTANCE_LOCK_FD)
    except OSError:
        pass
    _SINGLE_INSTANCE_LOCK_FD = None


def _show_already_running_message() -> None:
    msg = "JiuwenClaw is already running. Please use the existing window."
    title = "JiuwenClaw"
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x30)
        elif sys.platform == "darwin":
            import subprocess as _sp
            _sp.Popen(
                ["/usr/bin/osascript", "-e", f'display alert "{title}" message "{msg}" as informational'],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
            )
    except Exception:  # noqa: BLE001
        pass


def _is_child_mode() -> bool:
    return any(flag in sys.argv for flag in _CHILD_FLAGS)


def _write_child_error(exc: BaseException) -> None:
    """将子进程的未捕获异常写入日志文件。"""
    try:
        log_dir = Path(os.environ.get("JIUWENCLAW_DATA_DIR", Path.home() / ".jiuwenclaw")) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "child_error.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{'=' * 60}\n")
            f.write(f"argv: {sys.argv}\n")
            f.write(f"error: {type(exc).__name__}: {exc}\n")
            f.write(traceback.format_exc())
            f.write(f"{'=' * 60}\n\n")
    except Exception:
        pass


def _pop_flag(flag: str) -> bool:
    if flag not in sys.argv:
        return False
    sys.argv.remove(flag)
    return True


def main() -> None:
    is_child = _is_child_mode()

    try:
        _dispatch()
    except BaseException as exc:
        if is_child:
            _write_child_error(exc)
        raise


def _dispatch() -> None:
    # 子命令：初始化工作区（首次使用需运行 jiuwenclaw.exe init）
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "init":
        sys.argv.pop(1)
        from jiuwenclaw.init_workspace import main as init_main
        init_main()
        return
    # 子命令：CLI 命令分发
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "acp":
        from jiuwenclaw.app_cli import main as cli_main
        cli_main()
        return
    if _pop_flag("--desktop-run-app"):
        from jiuwenclaw.app import main as app_main
        app_main()
        return
    if _pop_flag("--desktop-run-web"):
        from jiuwenclaw.app_web import main as web_main
        web_main()
        return
    if _pop_flag(_DESKTOP_RUN_AGENT):
        from jiuwenclaw.app_agentserver import main as agent_main
        agent_main()
        return
    if _pop_flag(_DESKTOP_RUN_GATEWAY):
        from jiuwenclaw.app_gateway import main as gateway_main
        gateway_main()
        return
    # 子命令：浏览器启动（供主进程 subprocess 调用）
    if "--browser-start-client" in sys.argv:
        idx = sys.argv.index("--browser-start-client")
        sys.argv.pop(idx)
        from jiuwenclaw.agentserver.tools.browser_start_client import main as browser_main
        raise SystemExit(browser_main())
    # 默认运行桌面应用。
    # 在 import desktop_app 之前检查单实例锁，避免加载 webview 等重量级依赖。
    if not _acquire_single_instance_lock():
        _show_already_running_message()
        raise SystemExit(0)

    from jiuwenclaw.desktop_app import main as desktop_main
    try:
        desktop_main()
    finally:
        _release_single_instance_lock()


if __name__ == "__main__":
    main()
