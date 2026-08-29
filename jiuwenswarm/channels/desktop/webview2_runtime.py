from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

WEBVIEW2_RUNTIME_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

_WEBVIEW2_REGISTRY_LOCATIONS = (
    (
        "HKEY_LOCAL_MACHINE",
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_ID}",
    ),
    (
        "HKEY_CURRENT_USER",
        rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_ID}",
    ),
)


def _is_registered_version(value: object) -> bool:
    version = str(value).strip() if value is not None else ""
    return bool(version) and version != "0.0.0.0"


def is_webview2_runtime_registered(registry: Any | None = None) -> bool:
    """Return whether the current Windows user can discover Evergreen WebView2.

    Microsoft documents the ``pv`` values below as the lightweight presence
    check for per-machine and per-user Evergreen Runtime installations.  This
    deliberately does not start a process, touch the network, or probe the
    runtime binaries, so it stays off the expensive desktop startup path.
    """

    if registry is None:
        if sys.platform != "win32":
            return True
        import winreg as registry

    for root_name, subkey in _WEBVIEW2_REGISTRY_LOCATIONS:
        try:
            root = getattr(registry, root_name)
            with registry.OpenKey(root, subkey) as key:
                version, _value_type = registry.QueryValueEx(key, "pv")
        except (AttributeError, OSError):
            continue
        if _is_registered_version(version):
            return True

    return False


def show_webview2_runtime_missing_dialog(display_name: str) -> None:
    """Show a WebView-independent Windows error without starting other apps."""

    if sys.platform != "win32":
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    message_box = user32.MessageBoxW
    message_box.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
    ]
    message_box.restype = ctypes.c_int

    message = (
        "未检测到或未正确注册 Microsoft Edge WebView2 Runtime。\n\n"
        f"{display_name} 桌面端需要此运行环境，且本次不会启动本地服务。\n"
        "请安装或修复 WebView2 Runtime 后重试。"
    )
    flags = 0x00000000 | 0x00000010 | 0x00010000  # OK | ICONERROR | SETFOREGROUND
    message_box(None, message, f"{display_name} 启动失败", flags)
