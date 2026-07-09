# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""JiuwenAvatar 桌面应用 — Windows/macOS/Linux.

支持系统托盘图标、任务完成通知、关闭到托盘（Windows 托盘体验）。

流程：
  1. 启动合并后端进程（AgentServer + Gateway 单进程）
  2. 内嵌前端静态文件服务（主进程线程）
  3. 打开 pywebview 窗口，加载前端
  4. 创建系统托盘图标（pystray）
  5. 通过 WebSocket 监听 Gateway 的任务事件，到达时弹出托盘通知

Window 关闭行为：
  - 关闭按钮 → 隐藏到托盘（不退出）
  - 托盘右键 → 退出（彻底退出）
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import http.client
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from logging.handlers import RotatingFileHandler

import webview

from jiuwenavatar.common.service_ports import (
    DEFAULT_AGENT_SERVER_PORT,
    DEFAULT_FRONTEND_PORT,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_WEB_PORT,
)
from jiuwenavatar.common.utils import get_user_workspace_dir, get_logs_dir, wait_for_pid_exit, wait_for_tcp_port
from jiuwenavatar.channels.desktop.floating_widget_manager import FloatingWidgetManager
from jiuwenavatar.channels.desktop.webview_cache import clear_webview_http_cache


BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = DEFAULT_WEB_PORT
BACKEND_AGENT_PORT_DEFAULT = DEFAULT_AGENT_SERVER_PORT
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = DEFAULT_FRONTEND_PORT
APP_CHILD_FLAG = "--desktop-run-app"
BACKEND_CHILD_FLAG = "--desktop-run-backend"
WEB_CHILD_FLAG = "--desktop-run-web"
UPDATE_HELPER_FLAG = "--desktop-install-update"
STARTUP_TIMEOUT_SECONDS = 90.0
SHUTDOWN_PROCESS_GRACE_SECONDS = 5.0
QUIT_WINDOW_DESTROY_TIMEOUT_SECONDS = 5.0
MISSION_WS_POLL_INTERVAL = 15.0  # seconds between mission polls (idle-friendly)
MISSION_NOTIFICATION_MAX_LEN = 150


def _setup_logger() -> logging.Logger:
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)

    desktop_logger = logging.getLogger("jiuwenavatar.channels.desktop")
    desktop_logger.setLevel(logging.INFO)
    desktop_logger.propagate = False

    for handler in desktop_logger.handlers[:]:
        handler.close()
        desktop_logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        filename=logs_dir / "desktop.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    desktop_logger.addHandler(stream_handler)
    desktop_logger.addHandler(file_handler)
    return desktop_logger


logger = _setup_logger()


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _build_child_command(name: str, extra_args: list[str] | None = None) -> list[str]:
    if getattr(sys, "frozen", False):
        if name == "backend":
            flag = BACKEND_CHILD_FLAG
        elif name == "app":
            flag = APP_CHILD_FLAG
        elif name == "web":
            flag = WEB_CHILD_FLAG
        else:
            flag = UPDATE_HELPER_FLAG
        base = [sys.executable, flag]
    elif name == "backend":
        base = [sys.executable, "-m", "jiuwenavatar.channels.desktop.embedded_backend"]
    elif name == "app":
        base = [sys.executable, "-m", "jiuwenavatar.app"]
    elif name == "web":
        base = [sys.executable, "-m", "jiuwenavatar.channels.web.app_web"]
    else:
        base = [sys.executable, "-m", "jiuwenavatar.channels.desktop.desktop_app", UPDATE_HELPER_FLAG]
    if extra_args:
        base.extend(extra_args)
    return base


def _build_child_env(name: str) -> dict[str, str]:
    env = os.environ.copy()
    if name in ("app", "backend"):
        env["WEB_HOST"] = BACKEND_HOST
        env["WEB_PORT"] = str(BACKEND_PORT)
        env.setdefault("TRIGGER_STORE_WATCH_INTERVAL", "15")
    return env


def _start_process(name: str, command: list[str]) -> subprocess.Popen[bytes]:
    logger.info("[desktop] starting %s: %s", name, command)
    kwargs: dict[str, object] = {
        "env": _build_child_env(name),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    # macOS/Linux: 用 start_new_session=True 创建新进程组，
    # 以便后续用 os.killpg 杀掉整个进程树（含孙子进程）。
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = _creationflags()
    return subprocess.Popen(command, **kwargs)


def _backend_agent_port() -> int:
    return int(
        os.getenv("AGENT_SERVER_PORT") or os.getenv("AGENT_PORT", str(BACKEND_AGENT_PORT_DEFAULT))
    )


# Windows: job object with KILL_ON_JOB_CLOSE — if the shell exits (incl. os._exit),
# the OS terminates registered backend children even when explicit shutdown is skipped.
_CHILD_KILL_JOB: int | None = None


def _create_kill_on_close_job() -> int:
    """Create a Windows job that kills all assigned processes when the handle closes."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        9,  # JobObjectExtendedLimitInformation
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        raise OSError(f"SetInformationJobObject failed: {ctypes.get_last_error()}")
    return int(job)


def _assign_pid_to_kill_job(job: int, pid: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    access = 0x0001 | 0x0100  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        raise OSError(f"OpenProcess({pid}) failed: {ctypes.get_last_error()}")
    try:
        if not kernel32.AssignProcessToJobObject(job, handle):
            raise OSError(f"AssignProcessToJobObject({pid}) failed: {ctypes.get_last_error()}")
    finally:
        kernel32.CloseHandle(handle)


def _register_child_for_kill_on_parent_exit(process: subprocess.Popen[bytes]) -> None:
    """Register a child process in the kill-on-close job (Windows only)."""
    global _CHILD_KILL_JOB
    if os.name != "nt":
        return
    if process.poll() is not None:
        return
    try:
        if _CHILD_KILL_JOB is None:
            _CHILD_KILL_JOB = _create_kill_on_close_job()
            logger.info("[desktop] created kill-on-close job for backend children")
        _assign_pid_to_kill_job(_CHILD_KILL_JOB, process.pid)
        logger.info("[desktop] registered backend child pid=%s in kill-on-close job", process.pid)
    except Exception as exc:
        logger.warning("[desktop] kill-on-close job registration failed for pid=%s: %s", process.pid, exc)


def _find_pid_by_desktop_port(port: int) -> int | None:
    try:
        from jiuwenavatar.instance_manager.status import _find_pid_by_port

        return _find_pid_by_port(port)
    except Exception as exc:
        logger.debug("[desktop] port pid lookup failed for %s: %s", port, exc)
        return None


def _ensure_backend_ports_freed() -> None:
    """Last-resort cleanup: kill any process still bound to backend ports."""
    our_pid = os.getpid()
    for port in {BACKEND_PORT, _backend_agent_port()}:
        pid = _find_pid_by_desktop_port(port)
        if pid is None or pid == our_pid:
            continue
        logger.warning(
            "[desktop] port %s still held by pid %s after shutdown; force killing process tree",
            port,
            pid,
        )
        _psutil_terminate(pid, force=True)


def _wait_for_tcp(
    host: str,
    port: int,
    timeout: float,
    process: subprocess.Popen[bytes] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None

    while time.monotonic() < deadline:
        if process is not None:
            _ensure_process_running(f"service on tcp://{host}:{port}", process)
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.35)

    raise RuntimeError(f"Timed out waiting for tcp://{host}:{port}: {last_error}")


def _ensure_process_running(name: str, process: subprocess.Popen[bytes]) -> None:
    code = process.poll()
    if code is None:
        return
    raise RuntimeError(f"{name} exited early with code {code}")


def _wait_for_http(
    host: str,
    port: int,
    path: str,
    timeout: float,
    process: subprocess.Popen[bytes] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        if process is not None:
            _ensure_process_running(f"service on http://{host}:{port}{path}", process)
        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            response.read()
            if response.status < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        finally:
            conn.close()
        time.sleep(0.35)

    raise RuntimeError(
        f"Timed out waiting for http://{host}:{port}{path}: {last_error}"
    )


def _wait_for_port_release(host: str, port: int, timeout: float = 15.0) -> bool:
    return wait_for_tcp_port(host, port, timeout=timeout, target_state="disconnected")


def _launch_windows_installer_helper(installer_path: str, app_executable: str, parent_pid: int = 0) -> None:
    target = Path(installer_path).expanduser().resolve()

    logger.info("[update-helper] starting, target=%s, parent_pid=%d", target, parent_pid)

    wait_pid = parent_pid if parent_pid else os.getppid()
    logger.info("[update-helper] waiting for process %d to exit", wait_pid)
    wait_for_pid_exit(wait_pid)
    logger.info("[update-helper] parent process %d has exited, waiting for ports to release", wait_pid)

    _wait_for_port_release(BACKEND_HOST, BACKEND_PORT, timeout=15.0)
    _wait_for_port_release(FRONTEND_HOST, FRONTEND_PORT, timeout=15.0)
    logger.info("[update-helper] ports released, proceeding with install")

    try:
        subprocess.Popen(
            [str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[update-helper] installer launched successfully (interactive)")
    except Exception as exc:
        logger.error("[update-helper] installer launch failed: %s", exc)


class _WindowApi:
    def __init__(self, runtime: "DesktopRuntime") -> None:
        self._runtime = runtime

    def minimize_window(self) -> bool:
        return self._runtime.minimize_window()

    def toggle_fullscreen_window(self) -> bool:
        return self._runtime.toggle_fullscreen_window()

    def close_window(self) -> bool:
        return self._runtime.close_window()

    def install_update(self, installer_path: str) -> bool:
        return self._runtime.install_update(installer_path)

    def download_file(self, url: str, filename: str) -> bool:
        """通过 webview 下载文件，解决 exe 中无法使用 <a> 标签下载的问题。"""
        # 如果是相对路径，拼接完整的 URL（使用前端 web server 端口）
        if url.startswith("/"):
            full_url = f"http://{self._runtime.frontend_host}:{self._runtime.frontend_port}{url}"
        else:
            full_url = url
        logger.info("[desktop] download_file called: url=%s, filename=%s", full_url, filename)
        return self._runtime.download_file(full_url, filename)


class MissionWebSocketWatcher:
    """轮询本地 missions.json，发现新完成的/failed 任务时触发桌面通知。

    Gateway DEFAULT_WEB_PORT 端口仅提供 WebSocket，无 HTTP /api；因此直接读取 ReportStore
    持久化文件，与「报告」页数据源一致。
    """

    def __init__(
        self,
        notify_callback: Callable[[str, str, str, str], None] | None = None,
    ) -> None:
        self._notify = notify_callback
        self._known_missions: OrderedDict[str, bool] = OrderedDict()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._seed_known_missions()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="mission-watcher")
        self._thread.start()
        logger.info("[mission-watcher] started, polling local missions store")

    def stop(self) -> None:
        self._running = False
        logger.info("[mission-watcher] stopped")

    def _seed_known_missions(self) -> None:
        """启动时将已有终态任务标记为已知，避免重启后重复通知历史任务。"""
        try:
            from jiuwenavatar.gateway.report.models import MissionStatus
            from jiuwenavatar.gateway.report.store import ReportStore

            for mission in ReportStore().list_missions(limit=500):
                if mission.status in (MissionStatus.COMPLETED, MissionStatus.FAILED):
                    self._known_missions[mission.id] = True
        except Exception as exc:
            logger.warning("[mission-watcher] seed known missions failed: %s", exc)

    def _poll_loop(self) -> None:
        """Poll local mission store for updates."""
        while self._running:
            try:
                self._poll_once()
            except Exception as exc:
                logger.warning("[mission-watcher] poll error: %s", exc)
            time.sleep(MISSION_WS_POLL_INTERVAL)

    def _poll_once(self) -> None:
        """Fetch recent missions from local store and notify on terminal states."""
        from jiuwenavatar.gateway.report.models import MissionStatus
        from jiuwenavatar.gateway.report.store import ReportStore

        missions = ReportStore().list_missions(limit=20)
        for mission in missions:
            mid = mission.id
            if not mid or mid in self._known_missions:
                continue
            status = mission.status.value if isinstance(mission.status, MissionStatus) else str(mission.status)
            if status in ("completed", "failed"):
                self._known_missions[mid] = True
                self._notify_mission(mission)
                logger.info(
                    "[mission-watcher] new terminal mission: id=%s status=%s",
                    mid, status,
                )
                while len(self._known_missions) > 500:
                    self._known_missions.popitem(last=False)

    def _notify_mission(self, mission) -> None:
        if not self._notify:
            return
        status = mission.status.value if hasattr(mission.status, "value") else str(mission.status)
        summary = (mission.result_summary or mission.prompt or "")[:MISSION_NOTIFICATION_MAX_LEN]
        avatar_id = mission.avatar_id or "unknown"

        if status == "completed":
            title = "任务完成"
            message = f"[{avatar_id}] {summary}" if summary else f"分身 {avatar_id} 的任务已成功完成"
        elif status == "failed":
            title = "任务失败"
            message = f"[{avatar_id}] {summary}" if summary else f"分身 {avatar_id} 的任务执行失败"
        else:
            return

        try:
            self._notify(avatar_id, title, message, status)
        except Exception as exc:
            logger.warning("[mission-watcher] notify callback failed: %s", exc)


class DesktopRuntime:
    def __init__(
        self, frontend_host: str, frontend_port: int, backend_port: int
    ) -> None:
        self.frontend_host = frontend_host
        self.frontend_port = frontend_port
        self.backend_port = backend_port
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.window = None
        self._lock = threading.Lock()
        self._quit_lock = threading.Lock()
        self._is_shutting_down = False  # user requested exit (skip hide-to-tray)
        self._shutdown_done = False  # cleanup already executed (idempotent guard)
        self._tray: Any = None
        self._mission_watcher: MissionWebSocketWatcher | None = None
        self._window_visible = True
        self._window_minimized_to_tray = False
        self._floating_manager: FloatingWidgetManager | None = None
        self._floating_widget_enabled = True
        self._first_minimize_to_tray = True  # 首次最小化到托盘时提示用户
        self._show_window_lock = threading.Lock()
        self._embedded_web: Any = None
        self._webview_content_active = True

    @property
    def frontend_url(self) -> str:
        return f"http://{self.frontend_host}:{self.frontend_port}"

    def _setup_tray(self) -> None:
        """Initialize the system tray icon."""
        from jiuwenavatar.channels.desktop.tray_icon import TrayIcon

        self._tray = TrayIcon(
            on_open=self._on_tray_open,
            on_quit=self._on_tray_quit,
            on_toggle_floating=self._on_toggle_floating_widget,
        )
        self._tray.start()

    def _setup_floating_widget(self) -> None:
        """Initialize per-avatar floating buoys (one per avatar instance)."""
        logger.info("[desktop] _setup_floating_widget called, enabled=%s", self._floating_widget_enabled)
        if not self._floating_widget_enabled:
            logger.info("[desktop] floating widget disabled, skipping")
            return
        try:
            self._floating_manager = FloatingWidgetManager(
                on_open_main=lambda avatar_id: self._show_window(
                    avatar_id=avatar_id, open_reports=False,
                ),
                on_hide=self._on_floating_widget_hide,
                on_assign_task=self._on_floating_assign_task,
                unread_provider=self._get_unread_counts,
                on_open_reports=lambda avatar_id: self._show_window(
                    avatar_id=avatar_id, open_reports=True,
                ),
            )
            result = self._floating_manager.create_window()
            logger.info("[desktop] floating widget manager started: %s", result)
        except Exception as exc:
            logger.error("[desktop] floating widget setup failed: %s", exc, exc_info=True)

    def _on_floating_widget_hide(self) -> None:
        """Callback when a floating buoy is hidden via context menu."""
        if self._floating_manager:
            self._floating_manager.hide()

    def _on_floating_assign_task(self, avatar_id: str, prompt: str) -> None:
        """Assign a task to an avatar from the floating widget context menu."""
        from jiuwenavatar.channels.desktop.task_dispatch import dispatch_task_to_avatar

        avatar_name = avatar_id
        try:
            from jiuwenavatar.server.runtime.persona.manager import PersonaManager

            persona = PersonaManager.get_instance().get_avatar(avatar_id)
            if persona and persona.get("name"):
                avatar_name = str(persona["name"])
        except Exception:
            pass

        def _on_complete(_result: dict) -> None:
            new_sid = _result.get("session_id")
            if self._tray is not None:
                self._tray.show_notification(
                    "任务已下发",
                    f"已向「{avatar_name}」分配任务，可在报告页查看进度",
                    "info",
                )
            if self._floating_manager is not None:
                self._floating_manager.refresh_unread_badges()
            # 如果主窗口已打开，显式切换到刚创建的会话
            if new_sid and self.window is not None:
                self._switch_session_in_webview(str(new_sid), avatar_id)

        def _on_error(exc: Exception) -> None:
            if self._tray is not None:
                self._tray.show_notification(
                    "任务下发失败",
                    str(exc)[:200] or "未知错误",
                    "failed",
                )

        dispatch_task_to_avatar(
            host=BACKEND_HOST,
            port=self.backend_port,
            avatar_id=avatar_id,
            prompt=prompt,
            on_complete=_on_complete,
            on_error=_on_error,
        )

    @staticmethod
    def _get_unread_counts() -> tuple[dict[str, int], dict[str, int]]:
        """Provider callback for FloatingWidgetManager — returns (unread_counts, active_counts)."""
        from jiuwenavatar.gateway.report.read_state import (
            count_active_missions_by_avatar,
            count_unread_missions_by_avatar,
        )

        return count_unread_missions_by_avatar(), count_active_missions_by_avatar()

    def _on_toggle_floating_widget(self) -> None:
        """Toggle floating widget visibility from tray menu."""
        if self._floating_manager:
            self._floating_manager.toggle()

    def _start_mission_watcher(self) -> None:
        self._mission_watcher = MissionWebSocketWatcher(
            notify_callback=self._on_mission_notification,
        )
        self._mission_watcher.start()

    def _on_tray_open(self) -> None:
        """Tray 'Open' clicked — restore window."""
        threading.Thread(
            target=self._show_window,
            daemon=True,
            name="tray-open-main",
        ).start()

    def _on_tray_quit(self) -> None:
        """Tray 'Quit' clicked — schedule async quit (never block the pystray thread)."""
        with self._quit_lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True
        logger.info("[desktop] tray quit requested")
        threading.Thread(
            target=self._run_quit_sequence,
            daemon=True,
            name="desktop-quit",
        ).start()

    def _run_quit_sequence(self) -> None:
        """Graceful quit: stop services/children first, then close the webview window."""
        try:
            # Release backend + tray before tearing down WebView2 (slow on a foreign thread).
            self.shutdown()
            self._destroy_window_for_quit()
        except Exception as exc:
            logger.error("[desktop] quit sequence failed: %s", exc, exc_info=True)
            os._exit(1)

    def _destroy_window_for_quit(self) -> None:
        """Ask pywebview to destroy the window, with a timeout escape hatch."""
        if self.window is None:
            return

        def _do_destroy() -> None:
            try:
                self.window.destroy()
            except Exception as exc:
                logger.debug("[desktop] window destroy on quit failed: %s", exc)

        if self._invoke_webview_op(
            _do_destroy,
            timeout=QUIT_WINDOW_DESTROY_TIMEOUT_SECONDS,
        ):
            logger.info("[desktop] webview window destroyed")
            return

        logger.warning(
            "[desktop] window destroy timed out after %.1fs (services already stopped); forcing exit",
            QUIT_WINDOW_DESTROY_TIMEOUT_SECONDS,
        )
        _ensure_backend_ports_freed()
        os._exit(0)

    def _on_mission_notification(
        self, avatar_id: str, title: str, message: str, status: str
    ) -> None:
        """Callback from mission watcher — tray notify + per-avatar floating badge."""
        if self._tray is not None:
            self._tray.show_notification(title, message, status)

        if self._floating_manager is not None and avatar_id and avatar_id != "unknown":
            self._floating_manager.refresh_unread_badges()

    def start_services(self) -> None:
        backend = _start_process("backend", _build_child_command("backend"))
        _register_child_for_kill_on_parent_exit(backend)
        self.processes["backend"] = backend
        _ensure_process_running("backend", self.processes["backend"])
        _wait_for_tcp(
            BACKEND_HOST,
            self.backend_port,
            STARTUP_TIMEOUT_SECONDS,
            process=self.processes["backend"],
        )

        from jiuwenavatar.channels.web.app_web import EmbeddedFrontendServer

        self._embedded_web = EmbeddedFrontendServer(
            self.frontend_host,
            self.frontend_port,
            f"http://{BACKEND_HOST}:{self.backend_port}",
            log_level="WARNING",
        )
        self._embedded_web.start(wait_for_gateway=True)
        _wait_for_http(
            self.frontend_host,
            self.frontend_port,
            "/",
            STARTUP_TIMEOUT_SECONDS,
        )
        logger.info("[desktop] services ready: %s (embedded web server)", self.frontend_url)

    def minimize_window(self) -> bool:
        if self.window is None or not hasattr(self.window, "minimize"):
            return False
        self.window.minimize()
        return True

    def toggle_fullscreen_window(self) -> bool:
        if self.window is None:
            return False
        if hasattr(self.window, "toggle_fullscreen"):
            self.window.toggle_fullscreen()
            return True
        if hasattr(self.window, "maximize"):
            self.window.maximize()
            return True
        return False

    def close_window(self) -> bool:
        """Close button behavior: hide to tray instead of full exit."""
        self._hide_window()
        return True

    def _hide_window(self, *, defer_blank: bool = False) -> None:
        """Hide the window to tray instead of closing."""
        if self.window is None:
            return
        try:
            self.window.hide()
            self._window_visible = False
            self._window_minimized_to_tray = True
            logger.info("[desktop] window hidden to tray")

            if defer_blank:
                threading.Thread(
                    target=self._blank_webview_content,
                    daemon=True,
                    name="blank-webview",
                ).start()
            else:
                self._blank_webview_content()

            # 首次最小化到托盘时提示用户
            if self._first_minimize_to_tray and self._tray is not None:
                self._first_minimize_to_tray = False
                self._tray.show_notification(
                    "JiuwenAvatar 已最小化",
                    "程序仍在后台运行，点击系统托盘图标可重新打开",
                    "info",
                )
        except Exception as exc:
            logger.warning("[desktop] failed to hide window: %s", exc)

    def _on_closing(self) -> bool:
        """Intercept native close (X button). Return False to cancel and hide to tray."""
        if self._tray is not None and not self._is_shutting_down:
            logger.info("[desktop] close intercepted via closing event, hiding to tray")
            # closing 在 WinForms UI 线程同步执行，可直接 hide；blank 放后台避免阻塞 FormClosing
            self._hide_window(defer_blank=True)
            return False
        return True

    def _invoke_webview_op(self, operation: Callable[[], None], *, timeout: float = 2.5) -> bool:
        """Run a pywebview window operation with a timeout."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(operation)
            try:
                future.result(timeout=timeout)
                return True
            except concurrent.futures.TimeoutError:
                logger.warning("[desktop] pywebview operation timed out after %.1fs", timeout)
                return False
            except Exception as exc:
                logger.warning("[desktop] pywebview operation failed: %s", exc)
                return False

    def _blank_webview_content(self) -> None:
        """Unload the React SPA while in tray to reduce Chromium renderer memory."""
        if self.window is None or not self._webview_content_active:
            return

        def _do_blank() -> None:
            self.window.load_html(
                "<!DOCTYPE html><html><body style=\"margin:0;background:#0f172a\"></body></html>"
            )

        if self._invoke_webview_op(_do_blank):
            self._webview_content_active = False
            logger.info("[desktop] webview content unloaded for tray idle")

    def _reports_deep_link_url(self, avatar_id: str) -> str:
        import time
        from urllib.parse import quote

        nav_ts = int(time.time() * 1000)
        return (
            f"{self.frontend_url}#reports?avatar={quote(avatar_id)}"
            f"&read=unread&_nav={nav_ts}"
        )

    def _open_reports_in_webview(self, avatar_id: str) -> bool:
        """Navigate SPA to reports (works even when window is already on another tab)."""
        if self.window is None:
            return False

        import json
        import time

        nav_ts = int(time.time() * 1000)
        target_url = self._reports_deep_link_url(avatar_id)
        avatar_js = json.dumps(avatar_id)
        js = (
            "(function(){"
            f"var f=window.__jiuwenOpenReports;"
            f"if(typeof f==='function'){{f({avatar_js},'unread');return;}}"
            f"window.location.hash='reports?avatar='+encodeURIComponent({avatar_js})"
            f"+'&read=unread&_nav={nav_ts}';"
            "})();"
        )

        def _do_navigate() -> None:
            if self._webview_content_active:
                try:
                    self.window.evaluate_js(js)
                except Exception as exc:
                    logger.debug("[desktop] evaluate_js open reports failed: %s", exc)
                    self.window.load_url(target_url)
            else:
                self.window.load_url(target_url)

        if self._invoke_webview_op(_do_navigate, timeout=8.0):
            self._webview_content_active = True
            logger.info("[desktop] opened reports for avatar %s", avatar_id)
            return True
        return False

    def _switch_avatar_in_webview(self, avatar_id: str) -> bool:
        """Switch to chat page with the given avatar selected."""
        if self.window is None:
            return False

        import json

        avatar_js = json.dumps(avatar_id)
        js = (
            f"window.__jiuwenSwitchAvatar && window.__jiuwenSwitchAvatar({avatar_js});"
        )

        def _do_switch() -> None:
            if self._webview_content_active:
                try:
                    self.window.evaluate_js(js)
                except Exception as exc:
                    logger.debug("[desktop] evaluate_js switch avatar failed: %s", exc)
                    self.window.load_url(self.frontend_url)
            else:
                self.window.load_url(self.frontend_url)

        if self._invoke_webview_op(_do_switch, timeout=8.0):
            self._webview_content_active = True
            logger.info("[desktop] switched to avatar %s in chat", avatar_id)
            return True
        return False

    def _switch_session_in_webview(self, session_id: str, avatar_id: str) -> bool:
        """Explictly switch to a specific session in the webview (used after quick-assign)."""
        if self.window is None:
            return False

        import json

        sid_js = json.dumps(session_id)
        aid_js = json.dumps(avatar_id)
        js = (
            f"window.__jiuwenSwitchSession && window.__jiuwenSwitchSession({sid_js}, {aid_js});"
        )

        def _do_switch() -> None:
            if self._webview_content_active:
                try:
                    self.window.evaluate_js(js)
                except Exception as exc:
                    logger.debug("[desktop] evaluate_js switch session failed: %s", exc)
                    self.window.load_url(self.frontend_url)
            else:
                self.window.load_url(self.frontend_url)

        if self._invoke_webview_op(_do_switch, timeout=8.0):
            self._webview_content_active = True
            logger.info("[desktop] switched to session %s for avatar %s", session_id, avatar_id)
            return True
        return False

    def _reload_webview_content(self, url: str | None = None) -> bool:
        """Reload the frontend after tray idle blanking."""
        if self.window is None:
            return False

        load_url = url or self.frontend_url

        def _do_reload() -> None:
            self.window.load_url(load_url)

        if self._invoke_webview_op(_do_reload, timeout=8.0):
            self._webview_content_active = True
            logger.info("[desktop] webview content reloaded: %s", load_url)
            return True
        return False

    def _pywebview_show_with_timeout(self, timeout: float = 2.5) -> bool:
        """Call pywebview show/restore with a timeout to avoid indefinite Invoke deadlocks."""
        if self.window is None:
            return False

        def _do_show() -> None:
            self.window.show()
            self.window.restore()
            self.window.on_top = True
            self.window.on_top = False

        return self._invoke_webview_op(_do_show, timeout=timeout)

    def _show_window(self, *, avatar_id: str | None = None, open_reports: bool = False) -> None:
        """Restore the hidden/minimized window and bring it to front."""
        target_url = (
            self._reports_deep_link_url(avatar_id)
            if open_reports and avatar_id
            else self.frontend_url
        )
        if not self._show_window_lock.acquire(blocking=False):
            logger.info("[desktop] show window skipped (already in progress)")
            return

        shown = False
        try:
            if self.window is not None:
                if os.name == "nt":
                    from jiuwenavatar.channels.desktop import win_window

                    hwnd = win_window.get_pywebview_hwnd(self.window)
                    if win_window.bring_window_to_front(hwnd):
                        shown = True
                        logger.info("[desktop] window shown via Win32 (hwnd=%s)", hwnd)

                if not shown:
                    try:
                        shown = self._pywebview_show_with_timeout()
                        if shown:
                            logger.info("[desktop] window shown via pywebview")
                    except Exception:
                        logger.info("[desktop] window was destroyed, recreating...")
                        self.window = None

            if not shown and self.window is None:
                self.window = webview.create_window(
                    "JiuwenAvatar",
                    url=target_url,
                    width=1440,
                    height=960,
                    min_size=(1100, 720),
                    background_color="#0f172a",
                )
                self.window.events.closing += self._on_closing
                self.window.events.closed += self._on_closed
                webview.start(gui="edgechromium" if os.name == "nt" else None, private_mode=False)
                shown = True
                logger.info("[desktop] new window created")

            if shown:
                if open_reports and avatar_id:
                    self._open_reports_in_webview(avatar_id)
                elif avatar_id and not open_reports:
                    self._switch_avatar_in_webview(avatar_id)
                elif not self._webview_content_active:
                    shown = self._reload_webview_content(self.frontend_url)
                self._window_visible = True
                self._window_minimized_to_tray = False
            else:
                logger.warning("[desktop] failed to show main window")
        except Exception as exc:
            logger.error("[desktop] failed to recreate window: %s", exc)
        finally:
            self._show_window_lock.release()

    def download_file(self, url: str, filename: str) -> bool:
        """下载文件到用户下载目录（异步执行，避免阻塞 UI）。"""
        def _download() -> None:
            try:
                import urllib.request

                # 获取下载目录
                download_dir = Path.home() / "Downloads"
                if not download_dir.exists():
                    download_dir.mkdir(parents=True, exist_ok=True)

                # 处理文件名冲突
                target_path = download_dir / filename
                if target_path.exists():
                    base, ext = Path(filename).stem, Path(filename).suffix
                    counter = 1
                    while target_path.exists():
                        target_path = download_dir / f"{base} ({counter}){ext}"
                        counter += 1

                # 下载文件
                urllib.request.urlretrieve(url, target_path)
                logger.info("[desktop] file downloaded to: %s", target_path)

                # 下载完成后提醒用户并打开文件
                self._show_tray_notification_download(str(target_path))
            except Exception as exc:  # noqa: BLE001
                logger.error("[desktop] download failed: %s", exc)

        threading.Thread(target=_download, daemon=True).start()
        return True

    def _show_tray_notification_download(self, file_path: str) -> None:
        """Show download complete tray notification instead of blocking dialog."""
        if self._tray is not None:
            try:
                self._tray.show_notification("下载完成", f"文件已下载: {Path(file_path).name}")
            except Exception:
                pass
            return

        # Fallback to old dialog if no tray
        self._show_download_complete_dialog(file_path)

    @staticmethod
    def _show_download_complete_dialog(file_path: str) -> None:
        """Legacy download complete dialog (used when tray is not available)."""
        try:
            if os.name == "nt":
                import ctypes
                result = ctypes.windll.user32.MessageBoxW(
                    0,
                    f"文件已下载到:\n{file_path}\n\n是否打开所在文件夹？",
                    "下载完成",
                    0x44  # MB_YESNO + MB_ICONINFORMATION
                )
                if result == 6:  # IDYES
                    explorer_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
                    subprocess.Popen(
                        [explorer_path, "/select,", file_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=_creationflags(),
                    )
            elif sys.platform == "darwin":
                result = subprocess.run(
                    ["/usr/bin/osascript", "-e", f'''
                    display alert "下载完成" message "文件已下载到:\\n{file_path}\\n\\n是否打开所在文件夹？" buttons {"取消", "打开文件夹"} default button "打开文件夹" as informational
                    '''],
                    capture_output=True,
                    text=True,
                )
                if "打开文件夹" in result.stdout:
                    subprocess.Popen(
                        ["/usr/bin/open", "-R", file_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error("[desktop] failed to show download complete: %s", exc)

    def install_update(self, installer_path: str) -> bool:
        target = Path(installer_path).expanduser().resolve()
        if not target.is_file():
            logger.error("[desktop] installer not found: %s", target)
            return False

        app_executable = Path(sys.executable).resolve()

        if os.name == "nt":
            ok = self._launch_windows_install_helper(target, app_executable)
        elif sys.platform == "darwin":
            ok = self._launch_macos_install_helper(target)
        else:
            ok = self._launch_linux_install_helper(target, app_executable)

        if not ok:
            logger.error("[desktop] failed to launch update helper for %s", sys.platform)
            return False

        logger.info("[desktop] launched update helper for %s, parent pid=%d", sys.platform, os.getpid())
        self.close_window()
        return True

    @staticmethod
    def _launch_macos_install_helper(target: Path) -> bool:
        parent_pid = os.getpid()
        updates_dir = get_user_workspace_dir() / ".updates"
        updates_dir.mkdir(parents=True, exist_ok=True)

        if not os.access(updates_dir, os.W_OK):
            logger.error("[desktop] no write permission for updates directory: %s", updates_dir)
            return False

        helper_content = f"""#!/bin/bash
set -e
PARENT_PID={parent_pid}
while kill -0 "$PARENT_PID" 2>/dev/null; do
    sleep 1
done
open "{target}"
"""
        helper_path = updates_dir / "_install_helper.sh"
        helper_path.write_text(helper_content, encoding="utf-8")
        helper_path.chmod(0o755)

        subprocess.Popen(
            ["/bin/bash", str(helper_path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[desktop] macOS install helper launched, target=%s", target)
        return True

    @staticmethod
    def _launch_linux_install_helper(target: Path, app_executable: Path) -> bool:
        parent_pid = os.getpid()
        updates_dir = get_user_workspace_dir() / ".updates"
        updates_dir.mkdir(parents=True, exist_ok=True)

        if not os.access(updates_dir, os.W_OK):
            logger.error("[desktop] no write permission for updates directory: %s", updates_dir)
            return False

        install_dir = str(app_executable.parent.resolve())
        backup_dir = f"{install_dir}.bak.$RANDOM"

        helper_content = f"""#!/bin/bash
set -e
PARENT_PID={parent_pid}
while kill -0 "$PARENT_PID" 2>/dev/null; do
    sleep 1
done

BACKUP="{backup_dir}"
if [ -d "{install_dir}" ]; then
    mv "{install_dir}" "$BACKUP"
fi
mkdir -p "{install_dir}"
tar xzf "{target}" -C "{install_dir}"
rm -rf "$BACKUP" 2>/dev/null || true
nohup "{install_dir}/jiuwenavatar" >/dev/null 2>&1 &
"""
        helper_path = updates_dir / "_install_helper.sh"
        helper_path.write_text(helper_content, encoding="utf-8")
        helper_path.chmod(0o755)

        subprocess.Popen(
            ["/bin/bash", str(helper_path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[desktop] Linux install helper launched, target=%s", target)
        return True

    @staticmethod
    def _launch_windows_install_helper(target: Path, app_executable: Path) -> bool:
        detached_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | _creationflags()
        )
        helper_cmd = _build_child_command(
            "update-helper",
            [
                "--installer-path",
                str(target),
                "--app-executable",
                str(app_executable),
                "--parent-pid",
                str(os.getpid()),
            ],
        )
        logger.info("[desktop] launching update helper: %s", helper_cmd)
        subprocess.Popen(
            helper_cmd,
            creationflags=detached_flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    def shutdown(self) -> None:
        """Full shutdown: stop watchers, tray, floating widget, child processes."""
        with self._lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True

        logger.info("[desktop] shutdown started")

        # Stop mission watcher first
        if self._mission_watcher is not None:
            self._mission_watcher.stop()

        if self._embedded_web is not None:
            try:
                self._embedded_web.stop()
            except Exception as exc:
                logger.debug("[desktop] embedded web stop error: %s", exc)
            self._embedded_web = None

        # Stop floating widget
        if self._floating_manager is not None:
            try:
                self._floating_manager.destroy()
            except Exception as exc:
                logger.debug("[desktop] floating widget destroy error: %s", exc)

        # Stop tray icon
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception as exc:
                logger.debug("[desktop] tray stop error: %s", exc)

        deadline = time.monotonic() + SHUTDOWN_PROCESS_GRACE_SECONDS
        logger.info("[desktop] shutting down child processes")

        for name, process in list(self.processes.items()):
            if process.poll() is None:
                logger.info("[desktop] terminating %s pid=%s", name, process.pid)
                _terminate_process_tree(process)

        while time.monotonic() < deadline:
            if all(process.poll() is not None for process in self.processes.values()):
                break
            time.sleep(0.2)

        for name, process in list(self.processes.items()):
            if process.poll() is None:
                logger.warning("[desktop] force killing %s pid=%s", name, process.pid)
                _kill_process_tree(process)

        self.processes.clear()
        _ensure_backend_ports_freed()
        logger.info("[desktop] shutdown complete")

    def run(self, window_title: str, width: int, height: int, debug: bool) -> None:
        # Persistent profile dir — do NOT wipe on startup; localStorage (e.g. report
        # read state) lives here and must survive restarts.
        storage_path = get_user_workspace_dir() / "webview"
        storage_path.mkdir(parents=True, exist_ok=True)

        # WebView2/WKWebView HTTP caches survive exe upgrades; clear before loading UI.
        cleared = clear_webview_http_cache(storage_path)
        if cleared:
            logger.info("[desktop] cleared %s WebView HTTP cache dir(s)", cleared)

        self.window = webview.create_window(
            window_title,
            html=self._build_loading_html(),
            js_api=_WindowApi(self),
            width=width,
            height=height,
            min_size=(1100, 720),
            frameless=False,
            easy_drag=False,
            draggable=True,
            text_select=True,
            background_color="#f0f4ff",
        )

        self.window.events.loaded += self._on_loaded_first
        self.window.events.closing += self._on_closing
        self.window.events.closed += self._on_closed
        self.window.events.minimized += self._on_minimized

        def _startup_sequence() -> None:
            try:
                self.start_services()
                # Start tray icon, floating widget and mission watcher after backend is ready
                self._setup_tray()
                self._setup_floating_widget()
                self._start_mission_watcher()
                if self.window is not None:
                    self.window.load_url(self.frontend_url)
            except Exception as exc:
                logger.error("[desktop] service startup failed: %s", exc)
                self._show_startup_error(str(exc))

        threading.Thread(target=_startup_sequence, daemon=True).start()

        gui = "edgechromium" if os.name == "nt" else None
        logger.info("[desktop] opening window with loading screen")
        webview.start(
            debug=debug,
            gui=gui,
            private_mode=False,
            storage_path=str(storage_path),
        )

    def _show_startup_error(self, message: str) -> None:
        """Replace loading spinner with a visible startup failure message."""
        if self.window is None:
            return
        safe = (
            message.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
.box{{max-width:520px;padding:32px;text-align:center}}
h1{{font-size:20px;margin:0 0 12px;color:#f87171}}
p{{font-size:14px;line-height:1.6;color:#94a3b8;margin:0}}
code{{display:block;margin-top:16px;padding:12px;background:#1e293b;border-radius:8px;
font-size:12px;color:#cbd5e1;word-break:break-all;text-align:left}}
</style></head><body><div class="box">
<h1>启动失败</h1>
<p>后端服务未能就绪，请关闭后重试或查看 desktop.log。</p>
<code>{safe}</code>
</div></body></html>"""

        def _load() -> None:
            self.window.load_html(html)

        self._invoke_webview_op(_load, timeout=5.0)

    @staticmethod
    def _build_loading_html() -> str:
        import base64

        from jiuwenavatar.channels.desktop.brand_assets import find_brand_asset

        logo_html = ""
        logo_path = find_brand_asset("jiuwen_avatar.png")
        if logo_path is None:
            logo_path = find_brand_asset("jiuwen-avatar.png")
        if logo_path and logo_path.is_file():
            try:
                encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
                logo_html = (
                    f'<img src="data:image/png;base64,{encoded}" alt="" />'
                )
            except Exception:  # noqa: BLE001
                pass

        return r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:linear-gradient(135deg,#f0f4ff 0%,#e8f0fe 30%,#f5f3ff 70%,#eef2ff 100%);
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
color:#1e293b;display:flex;align-items:center;justify-content:center}
.root{display:flex;flex-direction:column;align-items:center;gap:28px;padding:48px}

/* Logo */
.logo{width:80px;height:80px;display:flex;align-items:center;justify-content:center;
background:#fff;border-radius:20px;box-shadow:0 4px 24px rgba(96,165,250,.15)}
.logo img{width:56px;height:56px;border-radius:12px;object-fit:contain}

/* App name */
.app-name{font-size:26px;font-weight:700;letter-spacing:-.5px;color:#0f172a}

/* Subtitle */
.subtitle{font-size:14px;color:#64748b;margin-top:-16px;font-weight:400}

/* Spinner */
.spinner{width:36px;height:36px;border:3px solid #e2e8f0;
border-top-color:#60a5fa;border-radius:50%;animation:spin 1.4s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* Tip area */
.tip-area{margin-top:4px;text-align:center;min-height:72px;
display:flex;flex-direction:column;align-items:center;gap:10px}
.tip-label{font-size:12px;font-weight:500;letter-spacing:2px;color:#94a3b8;text-transform:uppercase}
.tip-text{font-size:14px;color:#475569;max-width:380px;line-height:1.6;
transition:opacity .4s ease,transform .4s ease}
.tip-text.fade-out{opacity:0;transform:translateY(-8px)}
.tip-text.fade-in{opacity:1;transform:translateY(0)}

/* Dots */
.dots{display:flex;gap:6px;margin-top:8px;justify-content:center}
.dot{width:6px;height:6px;border-radius:50%;background:#cbd5e1;transition:all .3s ease}
.dot.active{background:#60a5fa;width:20px;border-radius:3px}

/* Loading bar */
.loading-bar{width:200px;height:3px;background:#e2e8f0;border-radius:2px;overflow:hidden;margin-top:4px}
.loading-bar-inner{width:30%;height:100%;background:linear-gradient(90deg,#60a5fa,#818cf8);border-radius:2px;
animation:loading 2s ease-in-out infinite}
@keyframes loading{0%{transform:translateX(-100%)}50%{transform:translateX(233%)}100%{transform:translateX(-100%)}}

.footer-text{font-size:11px;color:#94a3b8;margin-top:8px;letter-spacing:.5px}
</style>
</head>
<body>
<div class="root">
<div class="logo">__LOGO_SVG__</div>
<div class="app-name">JiuwenAvatar</div>
<div class="subtitle">你的 AI 数字分身团队</div>
<div class="spinner"></div>
<div class="tip-area">
    <div class="tip-label" id="tipLabel">TIPS</div>
    <div class="tip-text" id="tip"></div>
</div>
<div class="dots" id="dots"></div>
<div class="loading-bar"><div class="loading-bar-inner"></div></div>
<div class="footer-text">服务启动中，请稍候…</div>
</div>
<script>
const tips=[
"为每个专业角色创建专属数字分身 — 开发、测试、审查，各司其职",
"一键下发任务，AI 分身自动规划、执行、交付，全程无需人工干预",
"多分身协同作战，复杂项目拆解为并行任务，效率成倍提升",
"每个分身拥有独立记忆与上下文，随时接续未完成工作",
"支持 Git 仓库接入，分身直接读写代码，评审、修复、优化一气呵成",
"像管理团队一样管理你的 AI 分身，拖拽排序，按需召唤",
"深度集成飞书、钉钉、Telegram，随时随地给分身下达指令",
"分身持续学习你的偏好与反馈，越用越懂你，越用越顺手"
];
let idx=0;
const el=document.getElementById('tip');
const dotsEl=document.getElementById('dots');
const labelEl=document.getElementById('tipLabel');

tips.forEach((_,i)=>{
const d=document.createElement('div');
d.className='dot'+(i===0?' active':'');
dotsEl.appendChild(d);
});

function showTip(){
const dots=dotsEl.children;
for(let i=0;i<dots.length;i++) dots[i].className='dot'+(i===idx?' active':'');
el.className='tip-text fade-out';
labelEl.textContent='TIPS';
setTimeout(()=>{
    el.textContent=tips[idx];
    el.className='tip-text fade-in';
},400);
idx=(idx+1)%tips.length;
}
showTip();
setInterval(showTip,3500);
</script>
</body>
</html>""".replace("__LOGO_SVG__", logo_html)

    def _on_loaded_first(self) -> None:
        if self.window is not None:
            # 窗口首次加载后最大化（全屏会影响用户体验）
            if hasattr(self.window, "maximize"):
                self.window.maximize()
            self.window.events.loaded -= self._on_loaded_first
            self.window.events.loaded += self._on_loaded

    def _on_loaded(self) -> None:
        pass

    def _on_minimized(self) -> None:
        """Window minimized event — optional future use (e.g. minimize to tray)."""
        pass

    def _on_closed(self) -> None:
        """Window destroyed — ensure cleanup if the window was closed unexpectedly."""
        if not self._is_shutting_down:
            logger.warning(
                "[desktop] window closed without quit flag; running shutdown from closed event"
            )
        self.shutdown()


def _psutil_terminate(pid: int, force: bool = False) -> None:
    """Terminate a process and all its descendants using psutil.

    Unlike ``taskkill.exe``, this is a pure-Python operation that does not
    spawn an external console process, avoiding console window flashes on
    Windows (console=False builds).
    """
    try:
        import psutil

        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        kill_fn = (lambda p: p.kill()) if force else (lambda p: p.terminate())
        for child in reversed(children):
            try:
                kill_fn(child)
            except psutil.NoSuchProcess:
                pass
            except Exception as exc:
                logger.debug("[desktop] failed to kill child pid=%s of %s: %s", child.pid, pid, exc)
        try:
            kill_fn(parent)
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            logger.warning("[desktop] failed to %s pid=%s: %s", "kill" if force else "terminate", pid, exc)
    except ImportError:
        logger.warning("[desktop] psutil unavailable; cannot terminate pid=%s", pid)
    except psutil.NoSuchProcess:
        pass
    except Exception as exc:
        logger.warning("[desktop] process tree termination failed for pid=%s: %s", pid, exc)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Gracefully terminate a process and all its descendants."""
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
    else:
        _psutil_terminate(process.pid, force=False)


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Force kill a process and all its descendants."""
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
    else:
        _psutil_terminate(process.pid, force=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch JiuwenAvatar desktop window.")
    parser.add_argument("--title", default="JiuwenAvatar", help="Desktop window title.")
    parser.add_argument("--width", type=int, default=1440, help="Initial window width.")
    parser.add_argument(
        "--height", type=int, default=960, help="Initial window height."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable pywebview debug mode.",
    )
    parser.add_argument(UPDATE_HELPER_FLAG, action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--installer-path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--app-executable", default="", help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, default=0, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if getattr(args, "desktop_install_update", False):
        _launch_windows_installer_helper(args.installer_path, args.app_executable, args.parent_pid)
        return

    runtime = DesktopRuntime(
        frontend_host=FRONTEND_HOST,
        frontend_port=FRONTEND_PORT,
        backend_port=BACKEND_PORT,
    )
    try:
        runtime.run(
            window_title=args.title,
            width=args.width,
            height=args.height,
            debug=args.debug,
        )
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    main()