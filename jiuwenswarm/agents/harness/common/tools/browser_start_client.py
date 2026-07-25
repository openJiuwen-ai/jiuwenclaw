# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Start Chrome/Chromium with remote debugging enabled (cross-platform).

Reads browser settings from `config/config.yaml`:

browser:
  chrome_path: "<path or command>"
  remote_debugging_address: "127.0.0.1"
  remote_debugging_port: 9222
  user_data_dir: ""
  profile_directory: "Default"

`chrome_path` can also be a map by OS:

browser:
  chrome_path:
    windows: "C:\\path\\to\\chrome.exe"
    macos: "/Applications/Google Chrome.app"
    linux: "/usr/bin/google-chrome"
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import psutil
import yaml
from openjiuwen.harness.tools.browser_move.playwright_runtime.profiles import (
    BrowserProfile,
    BrowserProfileStore,
)

from jiuwenswarm.common.utils import get_user_workspace_dir


logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _config_path(custom_path: str = "") -> Path:
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    return _repo_root() / "config" / "config.yaml"



def _browser_runtime_state_root() -> Path:
    configured = (os.getenv("BROWSER_RUNTIME_STATE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return get_user_workspace_dir()


def _profile_store_path() -> Path:
    configured = (os.getenv("BROWSER_PROFILE_STORE_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _browser_runtime_state_root() / ".browser" / "profiles.json"


def _profile_name(profile_directory: str) -> str:
    configured = (os.getenv("BROWSER_PROFILE_NAME") or "").strip()
    if configured:
        return configured
    fallback = (profile_directory or "").strip()
    return fallback or "jiuwenswarm"



def _persist_browser_profile(
    *,
    host: str,
    port: int,
    chrome_exec: str,
    user_data_dir: str,
    profile_directory: str,
) -> None:
    store_path = _profile_store_path()
    store = BrowserProfileStore(store_path)
    profile = BrowserProfile(
        name=_profile_name(profile_directory),
        driver_type="remote",
        cdp_url=f"http://{host}:{port}",
        browser_binary=chrome_exec,
        user_data_dir=user_data_dir,
        debug_port=port,
        host=host,
        extra_args=[f"--profile-directory={profile_directory}"] if profile_directory else [],
    )
    store.upsert_profile(profile, select=True)
    logger.info(
        "Persisted browser profile for manual browser start: "
        f"profile={profile.name}, cdp_url={profile.cdp_url}, store_path={store_path}"
    )


def _load_browser_config(config_file: str = "") -> dict[str, Any]:
    cfg_file = _config_path(config_file)
    if not cfg_file.exists():
        return {}
    with cfg_file.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    browser_cfg = data.get("browser")
    return browser_cfg if isinstance(browser_cfg, dict) else {}


def _os_key() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def _resolve_chrome_path(raw_value: Any, os_name: str) -> str:
    if isinstance(raw_value, dict):
        for key in (os_name, "default"):
            value = raw_value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(raw_value, str):
        return raw_value.strip()
    return ""


def _default_chrome_candidates(os_name: str) -> list[str]:
    if os_name == "windows":
        local_app_data = os.getenv("LOCALAPPDATA", "")
        program_files = os.getenv("PROGRAMFILES", "C:\\Program Files")
        program_files_x86 = os.getenv("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        return [
            str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            "chrome.exe",
        ]
    if os_name == "macos":
        return [
            "/Applications/Google Chrome.app",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "Google Chrome",
            "google-chrome",
            "chromium",
        ]
    return [
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
    ]


def _normalize_chrome_executable(path_or_cmd: str, os_name: str) -> str:
    if not path_or_cmd:
        return ""

    expanded = os.path.expandvars(os.path.expanduser(path_or_cmd))
    path = Path(expanded)

    if os_name == "macos" and expanded.endswith(".app"):
        candidate = path / "Contents" / "MacOS" / "Google Chrome"
        if candidate.exists():
            return str(candidate)

    if path.exists():
        return str(path)

    resolved = shutil.which(expanded)
    if resolved:
        return resolved

    return ""


def _resolve_user_data_dir(raw_value: Any, os_name: str) -> str:
    if isinstance(raw_value, str) and raw_value.strip():
        return os.path.expandvars(os.path.expanduser(raw_value.strip()))

    if os_name == "windows":
        local_app_data = os.getenv("LOCALAPPDATA", "")
        return str(Path(local_app_data) / "ChromeCDPProfile")

    return str(Path.home() / "chrome-cdp-profile")


def _parse_cdp_from_env(default_host: str, default_port: int) -> tuple[str, int]:
    raw = (os.getenv("PLAYWRIGHT_CDP_URL") or "").strip()
    if not raw:
        return default_host, default_port

    # format: http://host:port
    try:
        no_scheme = raw.split("://", 1)[-1]
        host_port = no_scheme.split("/", 1)[0]
        host, port_text = host_port.rsplit(":", 1)
        return host, int(port_text)
    except Exception:
        return default_host, default_port


def _creation_flags_for_windows() -> int:
    flags = 0
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


@dataclass
class _BrowserProcessHandle:
    proc: "subprocess.Popen"
    chrome_exec: str
    host: str
    port: int
    user_data_dir: str


_BROWSER_PROCESS_REGISTRY: dict[int, _BrowserProcessHandle] = {}
_BROWSER_REGISTRY_LOCK = threading.Lock()


def _register_browser_process(handle: _BrowserProcessHandle) -> None:
    with _BROWSER_REGISTRY_LOCK:
        previous = _BROWSER_PROCESS_REGISTRY.pop(handle.proc.pid, None)
        if previous is not None and previous is not handle:
            logger.info(
                "Replacing previous browser process handle in registry "
                f"pid={previous.proc.pid}"
            )
        _BROWSER_PROCESS_REGISTRY[handle.proc.pid] = handle


def _unregister_browser_process(pid: int) -> Optional[_BrowserProcessHandle]:
    with _BROWSER_REGISTRY_LOCK:
        return _BROWSER_PROCESS_REGISTRY.pop(pid, None)


def _reap_browser_on_exit(handle: _BrowserProcessHandle) -> None:
    pid = handle.proc.pid
    try:
        try:
            handle.proc.wait()
        except Exception as exc:
            logger.warning(f"Browser reap watcher wait() failed pid={pid}: {exc}")
            return
        logger.info(
            f"Browser main process exited pid={pid}, reaping child processes "
            f"host={handle.host}, port={handle.port}"
        )
        try:
            _stop_existing_browser_service(
                chrome_exec=handle.chrome_exec,
                host=handle.host,
                port=handle.port,
                user_data_dir=handle.user_data_dir,
            )
        except Exception as exc:
            logger.warning(
                f"Browser reap watcher could not fully clean child processes pid={pid}: {exc}"
            )
    finally:
        _unregister_browser_process(pid)


def _normalized_path(value: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser((value or "").strip().strip('"')))
    return os.path.normcase(os.path.normpath(os.path.abspath(expanded)))


def _cmdline_option(args: list[str], option: str) -> str:
    prefix = f"{option}="
    for index, arg in enumerate(args):
        if arg.startswith(prefix):
            return arg[len(prefix):]
        if arg == option and index + 1 < len(args):
            return args[index + 1]
    return ""


def _find_existing_browser_processes(
    *, chrome_exec: str, port: int, user_data_dir: str
) -> list[Any]:
    """Find only Chrome processes belonging to this browser service."""
    expected_executable = _normalized_path(chrome_exec)
    expected_user_data_dir = _normalized_path(user_data_dir)
    matches: list[Any] = []
    for process in psutil.process_iter(["pid", "ppid", "exe", "cmdline"]):
        try:
            executable = str(process.info.get("exe") or "")
            args = [str(arg) for arg in (process.info.get("cmdline") or [])]
            if not executable:
                continue
            if _normalized_path(executable) != expected_executable:
                continue
            if _cmdline_option(args, "--remote-debugging-port") != str(port):
                continue

            actual_user_data_dir = _cmdline_option(args, "--user-data-dir")
            if not actual_user_data_dir:
                continue
            if _normalized_path(actual_user_data_dir) != expected_user_data_dir:
                continue

            matches.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return matches


def _port_is_open(host: str, port: int, *, timeout_s: float = 0.2) -> bool:
    connect_host = "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host
    try:
        with socket.create_connection((connect_host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _stop_existing_browser_service(
    *,
    chrome_exec: str,
    host: str,
    port: int,
    user_data_dir: str,
    timeout_s: float = 5.0,
) -> list[int]:
    matches = _find_existing_browser_processes(
        chrome_exec=chrome_exec,
        port=port,
        user_data_dir=user_data_dir,
    )
    if not matches:
        return []

    matched_pids = {process.pid for process in matches}
    roots = [
        process
        for process in matches
        if int(process.info.get("ppid") or 0) not in matched_pids
    ] or matches
    targets: dict[int, Any] = {process.pid: process for process in matches}
    for root in roots:
        try:
            targets.update({child.pid: child for child in root.children(recursive=True)})
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

    processes = list(targets.values())
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=timeout_s)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    if alive:
        _, alive = psutil.wait_procs(alive, timeout=2.0)
    if alive:
        remaining = ", ".join(str(process.pid) for process in alive)
        raise RuntimeError(f"Could not stop existing browser service processes: {remaining}")

    deadline = time.monotonic() + timeout_s
    while _port_is_open(host, port) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _port_is_open(host, port):
        raise RuntimeError(f"Browser service port {host}:{port} did not close after restart")

    stopped_pids = sorted(targets)
    logger.info(
        "Stopped existing browser service before restart: "
        f"pids={stopped_pids}, host={host}, port={port}"
    )
    return stopped_pids


def start_browser(*, dry_run: bool = False, config_file: str = "") -> int:
    browser_cfg = _load_browser_config(config_file)
    os_name = _os_key()
    logger.info(
        "Starting browser service from browser_start_client: "
        f"config_file={_config_path(config_file)}, os={os_name}"
    )

    chrome_cfg = _resolve_chrome_path(browser_cfg.get("chrome_path"), os_name)
    if not chrome_cfg:
        chrome_cfg = os.getenv("CHROME_PATH", "").strip()

    if not chrome_cfg:
        raise FileNotFoundError(
            "Chrome path is required. Please set browser.chrome_path in config/config.yaml "
            "or CHROME_PATH env."
        )
    chrome_exec = _normalize_chrome_executable(chrome_cfg, os_name)
    logger.info(
        "Resolved Chrome executable for browser service: "
        f"configured={chrome_cfg}, resolved={chrome_exec or '(not found)'}"
    )
    if not chrome_exec:
        raise FileNotFoundError(
            f"Chrome executable not found for configured path: {chrome_cfg}"
        )

    host = str(browser_cfg.get("remote_debugging_address") or "127.0.0.1").strip()
    port = int(browser_cfg.get("remote_debugging_port") or 9222)
    host, port = _parse_cdp_from_env(host, port)

    user_data_dir = _resolve_user_data_dir(browser_cfg.get("user_data_dir"), os_name)
    profile_directory = str(browser_cfg.get("profile_directory") or "Default").strip()

    raw_headless = browser_cfg.get("headless", True)
    headless = bool(raw_headless) if isinstance(raw_headless, bool) else True

    logger.info(
        "Resolved browser launch parameters: "
        f"host={host}, port={port}, user_data_dir={user_data_dir}, "
        f"profile_directory={profile_directory or '(empty)'}, headless={headless}"
    )

    args = [
        chrome_exec,
        f"--remote-debugging-address={host}",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
    ]
    if headless:
        args.append("--headless=new")
    if profile_directory:
        args.append(f"--profile-directory={profile_directory}")

    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os_name == "windows":
        kwargs["creationflags"] = _creation_flags_for_windows()
    else:
        kwargs["start_new_session"] = True

    if dry_run:
        logger.info("Dry run: browser launch command prepared.")
        logger.info(" ".join(args))
        return 0

    _stop_existing_browser_service(
        chrome_exec=chrome_exec,
        host=host,
        port=port,
        user_data_dir=user_data_dir,
    )
    if _port_is_open(host, port):
        raise RuntimeError(
            f"Browser service port {host}:{port} is occupied by an unrecognized process. "
            "Stop that process or choose another remote_debugging_port."
        )

    logger.info(
        "Launching browser process with remote debugging enabled: "
        f"command={args}"
    )
    proc = subprocess.Popen(args, **kwargs)
    logger.info(f"Browser process launched successfully: pid={proc.pid}")
    handle = _BrowserProcessHandle(
        proc=proc,
        chrome_exec=chrome_exec,
        host=host,
        port=port,
        user_data_dir=user_data_dir,
    )
    _register_browser_process(handle)
    watcher = threading.Thread(
        target=_reap_browser_on_exit,
        args=(handle,),
        name=f"browser-reap-{proc.pid}",
        daemon=True,
    )
    watcher.start()
    _persist_browser_profile(
        host=host,
        port=port,
        chrome_exec=chrome_exec,
        user_data_dir=user_data_dir,
        profile_directory=profile_directory,
    )
    logger.info(f"Chrome started (pid={proc.pid}) at {host}:{port}")
    logger.info(f"Executable: {chrome_exec}")
    logger.info(f"Profile dir: {user_data_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Chrome with CDP enabled.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved launch command without starting Chrome.",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Optional path to config yaml (default: config/config.yaml).",
    )
    args = parser.parse_args()
    try:
        return start_browser(dry_run=args.dry_run, config_file=args.config)
    except Exception as exc:
        logger.error("Failed to start Chrome: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
