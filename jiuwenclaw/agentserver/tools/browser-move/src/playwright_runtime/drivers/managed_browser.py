#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Managed isolated browser launcher for CDP attach."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

from playwright_runtime.profiles import BrowserProfile


def _candidate_binaries() -> list[str]:
    names = [
        "chrome",
        "chromium",
        "msedge",
        "brave",
    ]
    resolved = [shutil.which(name) for name in names]
    binaries = [item for item in resolved if item]

    if os.name == "nt":
        windows_defaults = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Chromium\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for path in windows_defaults:
            if Path(path).exists():
                binaries.append(path)
    return binaries


class ManagedBrowserDriver:
    """Launch and manage a dedicated local Chromium-family process."""

    def __init__(self, profile: BrowserProfile) -> None:
        self.profile = profile
        self._process: Optional[subprocess.Popen] = None

    @property
    def cdp_endpoint(self) -> str:
        host = self.profile.host or "127.0.0.1"
        return f"http://{host}:{int(self.profile.debug_port)}"

    def _resolve_binary(self) -> str:
        explicit = (self.profile.browser_binary or "").strip()
        if explicit:
            candidate = Path(explicit).expanduser()
            if candidate.exists():
                return str(candidate)
            resolved = shutil.which(explicit)
            if resolved:
                return resolved
            raise RuntimeError(f"Configured browser binary not found: {explicit}")

        candidates = _candidate_binaries()
        if not candidates:
            raise RuntimeError(
                "No Chromium-family browser binary found. Set BROWSER_MANAGED_BINARY to chrome/msedge/brave path."
            )
        return candidates[0]

    def _build_args(self, binary: str) -> list[str]:
        user_data_dir = Path(self.profile.user_data_dir).expanduser()
        user_data_dir.mkdir(parents=True, exist_ok=True)

        host = (self.profile.host or "127.0.0.1").strip() or "127.0.0.1"
        port = int(self.profile.debug_port)
        if port <= 0:
            raise RuntimeError(f"Invalid debug port for managed browser profile: {port}")

        args = [
            binary,
            f"--remote-debugging-address={host}",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        args.extend(self.profile.extra_args)
        return args

    def _is_endpoint_ready(self) -> bool:
        endpoint = f"{self.cdp_endpoint}/json/version"
        try:
            with urlopen(endpoint, timeout=1.5) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
                if isinstance(payload, dict):
                    return bool(payload.get("webSocketDebuggerUrl") or payload.get("Browser"))
        except (URLError, TimeoutError, OSError, ValueError):
            return False
        return False

    def start(self, timeout_s: float = 20.0) -> str:
        if self._process is not None and self._process.poll() is None:
            if self._is_endpoint_ready():
                return self.cdp_endpoint

        binary = self._resolve_binary()
        args = self._build_args(binary)
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
        )

        deadline = time.time() + max(1.0, float(timeout_s))
        while time.time() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"Managed browser process exited early with code {self._process.returncode}"
                )
            if self._is_endpoint_ready():
                return self.cdp_endpoint
            time.sleep(0.25)

        raise RuntimeError(f"Managed browser CDP endpoint not ready after {timeout_s:.1f}s: {self.cdp_endpoint}")

    def stop(self, wait_timeout_s: float = 5.0) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=max(0.5, float(wait_timeout_s)))
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

