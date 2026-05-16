from __future__ import annotations

import argparse
import http.client
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from logging.handlers import RotatingFileHandler

import webview

from jiuwenclaw.common.utils import get_user_workspace_dir, get_logs_dir


BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 19000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173
APP_CHILD_FLAG = "--desktop-run-app"
WEB_CHILD_FLAG = "--desktop-run-web"
UPDATE_HELPER_FLAG = "--desktop-install-update"
STARTUP_TIMEOUT_SECONDS = 45.0


def _setup_logger() -> logging.Logger:
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)

    desktop_logger = logging.getLogger("jiuwenclaw.channels.desktop")
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
        if name == "app":
            flag = APP_CHILD_FLAG
        elif name == "web":
            flag = WEB_CHILD_FLAG
        else:
            flag = UPDATE_HELPER_FLAG
        base = [sys.executable, flag]
    elif name == "app":
        base = [sys.executable, "-m", "jiuwenclaw.app"]
    elif name == "web":
        base = [sys.executable, "-m", "jiuwenclaw.channels.web.app_web"]
    else:
        base = [sys.executable, "-m", "jiuwenclaw.channels.desktop.desktop_app", UPDATE_HELPER_FLAG]
    if extra_args:
        base.extend(extra_args)
    return base


def _build_child_env(name: str) -> dict[str, str]:
    env = os.environ.copy()
    if name == "app":
        env["WEB_HOST"] = BACKEND_HOST
        env["WEB_PORT"] = str(BACKEND_PORT)
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


def _wait_for_pid_exit(pid: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)


def _launch_windows_installer_helper(installer_path: str, app_executable: str) -> None:
    target = Path(installer_path).expanduser().resolve()
    app_path = Path(app_executable).expanduser().resolve()

    _wait_for_pid_exit(os.getppid())

    subprocess.run(
        [
            str(target),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            "/CLOSEAPPLICATIONS",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )
    time.sleep(2)
    subprocess.Popen(
        [str(app_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )


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
        self._is_shutting_down = False

    @property
    def frontend_url(self) -> str:
        return f"http://{self.frontend_host}:{self.frontend_port}"

    def start_services(self) -> None:
        self.processes["app"] = _start_process("app", _build_child_command("app"))
        _ensure_process_running("app", self.processes["app"])
        _wait_for_tcp(
            BACKEND_HOST,
            self.backend_port,
            STARTUP_TIMEOUT_SECONDS,
            process=self.processes["app"],
        )

        web_command = _build_child_command(
            "web",
            [
                "--host",
                self.frontend_host,
                "--port",
                str(self.frontend_port),
                "--proxy-target",
                f"http://{BACKEND_HOST}:{self.backend_port}",
            ],
        )
        self.processes["web"] = _start_process("web", web_command)
        _ensure_process_running("web", self.processes["web"])
        _wait_for_http(
            self.frontend_host,
            self.frontend_port,
            "/",
            STARTUP_TIMEOUT_SECONDS,
            process=self.processes["web"],
        )
        logger.info("[desktop] services ready: %s", self.frontend_url)

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
        if self.window is None or not hasattr(self.window, "destroy"):
            return False

        def _delayed_destroy() -> None:
            time.sleep(0.15)
            try:
                self.window.destroy()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[desktop] failed to close desktop window: %s", exc)

        threading.Thread(target=_delayed_destroy, daemon=True).start()
        return True

    def install_update(self, installer_path: str) -> bool:
        if os.name != "nt":
            logger.warning("[desktop] update install is only supported on Windows")
            return False

        target = Path(installer_path).expanduser().resolve()
        if not target.is_file():
            logger.error("[desktop] installer not found: %s", target)
            return False

        app_executable = Path(sys.executable).resolve()

        detached_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | _creationflags()
        )
        subprocess.Popen(
            _build_child_command(
                "update-helper",
                [
                    "--installer-path",
                    str(target),
                    "--app-executable",
                    str(app_executable),
                ],
            ),
            creationflags=detached_flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[desktop] launched update installer helper: %s", target)
        self.close_window()
        return True

    def shutdown(self) -> None:
        with self._lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True

        deadline = time.monotonic() + 8.0
        logger.info("[desktop] shutting down child processes")

        for process in self.processes.values():
            if process.poll() is None:
                _terminate_process_tree(process)

        while time.monotonic() < deadline:
            if all(process.poll() is not None for process in self.processes.values()):
                break
            time.sleep(0.2)

        for process in self.processes.values():
            if process.poll() is None:
                _kill_process_tree(process)

        self.processes.clear()

    def run(self, window_title: str, width: int, height: int, debug: bool) -> None:
        storage_path = get_user_workspace_dir() / "tmp" / "webview"
        if storage_path.exists():
            shutil.rmtree(storage_path)
        storage_path.mkdir(parents=True, exist_ok=True)

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
            background_color="#0f172a",
        )

        self.window.events.loaded += self._on_loaded_first
        self.window.events.closed += self._on_closed

        def _start_services_and_navigate() -> None:
            try:
                self.start_services()
                if self.window is not None:
                    self.window.load_url(self.frontend_url)
            except Exception as exc:
                logger.error("[desktop] service startup failed: %s", exc)

        threading.Thread(target=_start_services_and_navigate, daemon=True).start()

        gui = "edgechromium" if os.name == "nt" else None
        logger.info("[desktop] opening window with loading screen")
        webview.start(
            debug=debug,
            gui=gui,
            private_mode=False,
            storage_path=str(storage_path),
        )

    @staticmethod
    def _build_loading_html() -> str:
        logo_svg = ""
        pkg_dir = Path(__file__).resolve().parent
        logo_path = pkg_dir.parent / "web" / "frontend" / "dist" / "logo.svg"
        if not logo_path.is_file():
            logo_path = pkg_dir.parent / "web" / "frontend" / "public" / "logo.svg"
        if logo_path.is_file():
            try:
                logo_svg = logo_path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass

        return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:#0f172a;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
color:#e2e8f0;display:flex;align-items:center;justify-content:center}
.root{display:flex;flex-direction:column;align-items:center;gap:32px;padding:40px}

/* Logo */
.logo{width:64px;height:64px;border-radius:16px;
background:linear-gradient(135deg,#3b82f6,#8b5cf6);
display:flex;align-items:center;justify-content:center;
box-shadow:0 8px 24px rgba(59,130,246,.25)}
.logo svg{width:64px;height:64px;border-radius:16px}

/* App name */
.app-name{font-size:22px;font-weight:700;letter-spacing:-.3px;color:#f1f5f9}

/* Spinner */
.spinner{width:32px;height:32px;border:3px solid rgba(148,163,184,.2);
border-top-color:#60a5fa;border-radius:50%;animation:spin 1.5s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* Tip area */
.tip-area{margin-top:8px;text-align:center;min-height:60px;
display:flex;flex-direction:column;align-items:center;gap:8px}
.tip-label{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#475569}
.tip-text{font-size:13px;color:#94a3b8;max-width:320px;line-height:1.5;
transition:opacity .4s ease,transform .4s ease}
.tip-text.fade-out{opacity:0;transform:translateY(-8px)}
.tip-text.fade-in{opacity:1;transform:translateY(0)}

/* Dots */
.dots{display:flex;gap:4px;justify-content:center}
.dot{width:4px;height:4px;border-radius:50%;background:#475569}
.dot.active{background:#60a5fa;animation:pulse 1.2s ease infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
</style>
</head>
<body>
<div class="root">
<div class="logo">__LOGO_SVG__</div>
<div class="app-name">JiuwenSwarm</div>
<div class="spinner"></div>
<div class="tip-area">
    <div class="tip-label">专属智能AI Agent助理</div>
    <div class="tip-text" id="tip"></div>
</div>
<div class="dots" id="dots"></div>
<div class="tip-label" style="margin-top:16px">服务启动加载中</div>
</div>
<script>
const tips=[
"多智能体协作 —— 编排多个专业 Agent 协同工作，群体智能涌现",
"多端接入 —— 支持 Web、飞书、微信、钉钉、Telegram 等多种交互方式",
"贴身任务管家 —— 精准理解复杂指令，智能排期，有条不紊完成任务",
"自主演进 —— 根据你的反馈自动调整技能，持续进化，越用越懂你"
];
let idx=0;
const el=document.getElementById('tip');
const dotsEl=document.getElementById('dots');

tips.forEach((_,i)=>{
const d=document.createElement('div');
d.className='dot'+(i===0?' active':'');
dotsEl.appendChild(d);
});

function showTip(){
const dots=dotsEl.children;
for(let i=0;i<dots.length;i++) dots[i].className='dot'+(i===idx?' active':'');
el.className='tip-text fade-out';
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
</html>""".replace("__LOGO_SVG__", logo_svg)

    def _on_loaded_first(self) -> None:
        if self.window is not None:
            self.window.events.loaded -= self._on_loaded_first
            self.window.events.loaded += self._on_loaded

    def _on_loaded(self) -> None:
        pass

    def _on_closed(self) -> None:
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
        # 获取所有子孙进程（在杀父进程之前先拿到完整列表）
        children = parent.children(recursive=True)
        kill_fn = (lambda p: p.kill()) if force else (lambda p: p.terminate())
        # 先杀子孙，再杀父进程，避免子孙变成孤儿
        for child in reversed(children):
            try:
                kill_fn(child)
            except psutil.NoSuchProcess:
                pass
        try:
            kill_fn(parent)
        except psutil.NoSuchProcess:
            pass
    except Exception:  # noqa: BLE001
        pass


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
    parser = argparse.ArgumentParser(description="Launch JiuwenSwarm desktop window.")
    parser.add_argument("--title", default="JiuwenSwarm", help="Desktop window title.")
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
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if getattr(args, "desktop_install_update", False):
        _launch_windows_installer_helper(args.installer_path, args.app_executable)
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
