from __future__ import annotations

import argparse
import http.client
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from logging.handlers import RotatingFileHandler

import webview

from jiuwenclaw.utils import get_user_workspace_dir, get_logs_dir


BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 19000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173
APP_CHILD_FLAG = "--desktop-run-app"
WEB_CHILD_FLAG = "--desktop-run-web"
UPDATE_HELPER_FLAG = "--desktop-install-update"
STARTUP_TIMEOUT_SECONDS = 45.0

DESKTOP_BRIDGE_SCRIPT = r"""
(() => {
  if (window.__jiuwenclawDesktopControlsMounted) {
    return;
  }

  const style = document.createElement('style');
  style.textContent = `
    :root {
      --desktop-controls-surface: rgba(22, 28, 45, 0.76);
      --desktop-controls-border: rgba(71, 85, 105, 0.4);
      --desktop-controls-icon: rgba(226, 232, 240, 0.82);
      --desktop-controls-icon-strong: rgba(248, 250, 252, 0.98);
      --desktop-controls-hover: rgba(255, 255, 255, 0.08);
      --desktop-controls-close: rgba(127, 29, 29, 0.22);
      --desktop-controls-close-border: rgba(248, 113, 113, 0.28);
      --desktop-controls-close-icon: rgba(254, 202, 202, 0.96);
    }
    :root[data-theme="light"] {
      --desktop-controls-surface: rgba(255, 255, 255, 0.92);
      --desktop-controls-border: rgba(148, 163, 184, 0.28);
      --desktop-controls-icon: rgba(15, 23, 42, 0.72);
      --desktop-controls-icon-strong: rgba(2, 6, 23, 0.88);
      --desktop-controls-hover: rgba(148, 163, 184, 0.16);
      --desktop-controls-close: rgba(254, 226, 226, 0.92);
      --desktop-controls-close-border: rgba(248, 113, 113, 0.32);
      --desktop-controls-close-icon: rgba(185, 28, 28, 0.88);
    }
    #__jiuwenclaw_desktop_controls {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-left: 10px;
      padding: 3px;
      border-radius: 14px;
      border: 1px solid var(--desktop-controls-border);
      background: var(--desktop-controls-surface);
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.12);
      pointer-events: auto;
      user-select: none;
      flex-shrink: 0;
    }
    #__jiuwenclaw_desktop_controls button {
      width: 28px;
      height: 28px;
      border: 1px solid transparent;
      border-radius: 10px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--desktop-controls-icon);
      background: transparent;
      transition:
        transform 120ms ease,
        background 120ms ease,
        border-color 120ms ease,
        color 120ms ease,
        box-shadow 120ms ease;
    }
    #__jiuwenclaw_desktop_controls button:hover {
      transform: translateY(-1px);
      background: var(--desktop-controls-hover);
      border-color: var(--desktop-controls-border);
      color: var(--desktop-controls-icon-strong);
    }
    #__jiuwenclaw_desktop_controls button:active {
      transform: translateY(0);
    }
    #__jiuwenclaw_desktop_controls button svg {
      width: 14px;
      height: 14px;
      stroke: currentColor;
      stroke-width: 1.9;
      fill: none;
      vector-effect: non-scaling-stroke;
    }
    #__jiuwenclaw_desktop_controls button[data-action="fullscreen"] svg {
      width: 13px;
      height: 13px;
    }
    #__jiuwenclaw_desktop_controls button[data-action="close"] {
      color: var(--desktop-controls-close-icon);
    }
    #__jiuwenclaw_desktop_controls button[data-action="close"]:hover {
      background: var(--desktop-controls-close);
      border-color: var(--desktop-controls-close-border);
      color: var(--desktop-controls-close-icon);
      box-shadow: 0 6px 14px rgba(239, 68, 68, 0.12);
    }
    #__jiuwenclaw_desktop_controls.__floating {
      position: fixed;
      top: 12px;
      right: 12px;
      z-index: 2147483647;
      padding: 4px;
      margin-left: 0;
    }
    .pywebview-drag-region {
      cursor: default;
    }
  `;

  const container = document.createElement('div');
  container.id = '__jiuwenclaw_desktop_controls';
  container.innerHTML = `
    <button type="button" data-action="minimize" title="Minimize" aria-label="Minimize window">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5h10" /></svg>
    </button>
    <button type="button" data-action="fullscreen" title="Toggle fullscreen" aria-label="Toggle fullscreen">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5H3.5V5M11 3.5h1.5V5M5 12.5H3.5V11M11 12.5h1.5V11" /></svg>
    </button>
    <button type="button" data-action="close" title="Close" aria-label="Close window">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.25 4.25l7.5 7.5M11.75 4.25l-7.5 7.5" /></svg>
    </button>
  `;

  window.__jiuwenclawDesktopControlsMounted = true;

  const callApi = (methodName) => {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[methodName] !== 'function') {
      return Promise.resolve(false);
    }
    try {
      return Promise.resolve(api[methodName]());
    } catch (error) {
      console.error('[desktop-controls]', error);
      return Promise.resolve(false);
    }
  };

  container.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const button = target.closest('button[data-action]');
    if (!(button instanceof HTMLElement)) {
      return;
    }
    const action = button.dataset.action;
    if (action === 'minimize') {
      void callApi('minimize_window');
    } else if (action === 'fullscreen') {
      void callApi('toggle_fullscreen_window');
    } else if (action === 'close') {
      void callApi('close_window');
    }
  });

  const mountControls = () => {
    const topbar = document.querySelector('.topbar');
    if (topbar instanceof HTMLElement) {
      topbar.classList.add('pywebview-drag-region');
      const rightZone = topbar.lastElementChild;
      if (rightZone instanceof HTMLElement) {
        rightZone.appendChild(container);
        container.classList.remove('__floating');
        return true;
      }
      topbar.appendChild(container);
      container.classList.remove('__floating');
      return true;
    }

    container.classList.add('__floating');
    document.body.appendChild(container);
    return false;
  };

  document.head.appendChild(style);

  if (!mountControls()) {
    const observer = new MutationObserver(() => {
      if (mountControls()) {
        observer.disconnect();
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener('beforeunload', () => observer.disconnect(), { once: true });
  }
})();
"""


def _setup_logger() -> logging.Logger:
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)

    desktop_logger = logging.getLogger("jiuwenclaw.desktop")
    desktop_logger.setLevel(logging.INFO)
    desktop_logger.propagate = False

    for handler in desktop_logger.handlers[:]:
        handler.close()
        desktop_logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(process)d] %(levelname)s %(name)s %(filename)s:%(lineno)d: %(message)s",
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
        base = [sys.executable, "-m", "jiuwenclaw.app_web"]
    else:
        base = [sys.executable, "-m", "jiuwenclaw.desktop_app", UPDATE_HELPER_FLAG]
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
    return subprocess.Popen(
        command,
        env=_build_child_env(name),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )


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

    def download_file(self, url: str, filename: str) -> bool:
        """通过 webview 下载文件，解决 exe 中无法使用 <a> 标签下载的问题。"""
        # 如果是相对路径，拼接完整的 URL（使用前端 web server 端口）
        if url.startswith("/"):
            full_url = f"http://{self._runtime.frontend_host}:{self._runtime.frontend_port}{url}"
        else:
            full_url = url
        logger.info("[desktop] download_file called: url=%s, filename=%s", full_url, filename)
        return self._runtime.download_file(full_url, filename)


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
        self.window.destroy()
        return True

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
                self._show_download_complete(str(target_path))
            except Exception as exc:  # noqa: BLE001
                logger.error("[desktop] download failed: %s", exc)

        threading.Thread(target=_download, daemon=True).start()
        return True

    @staticmethod
    def _show_download_complete(file_path: str) -> None:
        """下载完成后提醒用户并打开文件所在文件夹。"""
        try:
            if os.name == "nt":
                import ctypes
                # Windows: 弹窗询问是否打开文件夹
                result = ctypes.windll.user32.MessageBoxW(
                    0,
                    f"文件已下载到:\n{file_path}\n\n是否打开所在文件夹？",
                    "下载完成",
                    0x44  # MB_YESNO + MB_ICONINFORMATION
                )
                if result == 6:  # IDYES
                    # 打开文件夹并选中文件
                    explorer_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
                    subprocess.Popen(
                        [explorer_path, "/select,", file_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=_creationflags(),
                    )
            elif sys.platform == "darwin":
                # macOS: 弹窗询问
                result = subprocess.run(
                    ["/usr/bin/osascript", "-e", f'''
                    display alert "下载完成" message "文件已下载到:\\n{file_path}\\n\\n是否打开所在文件夹？" buttons {"取消", "打开文件夹"} default button "打开文件夹" as informational
                    '''],
                    capture_output=True,
                    text=True,
                )
                if "打开文件夹" in result.stdout:
                    # 打开文件夹并选中文件
                    subprocess.Popen(
                        ["/usr/bin/open", "-R", file_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error("[desktop] failed to show download complete: %s", exc)

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
                process.terminate()

        while time.monotonic() < deadline:
            if all(process.poll() is not None for process in self.processes.values()):
                break
            time.sleep(0.2)

        for process in self.processes.values():
            if process.poll() is None:
                process.kill()

        self.processes.clear()

    def run(self, window_title: str, width: int, height: int, debug: bool) -> None:
        self.start_services()

        storage_path = get_user_workspace_dir() / "tmp" / "webview"
        storage_path.mkdir(parents=True, exist_ok=True)

        self.window = webview.create_window(
            window_title,
            self.frontend_url,
            js_api=_WindowApi(self),
            width=width,
            height=height,
            min_size=(1100, 720),
            frameless=True,
            easy_drag=False,
            draggable=True,
            text_select=True,
            background_color="#0f172a",
        )

        self.window.events.loaded += self._on_loaded
        self.window.events.closed += self._on_closed

        gui = "edgechromium" if os.name == "nt" else None
        logger.info("[desktop] opening window: %s", self.frontend_url)
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
            # 窗口首次加载后最大化（全屏会影响用户体验）
            if hasattr(self.window, "maximize"):
                self.window.maximize()
            self.window.events.loaded -= self._on_loaded_first
            self.window.events.loaded += self._on_loaded

    def _on_loaded(self) -> None:
        if self.window is None:
            return
        try:
            self.window.evaluate_js(DESKTOP_BRIDGE_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[desktop] failed to inject desktop controls: %s", exc)

    def _on_closed(self) -> None:
        self.shutdown()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch JiuwenClaw desktop window.")
    parser.add_argument("--title", default="JiuwenClaw", help="Desktop window title.")
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
