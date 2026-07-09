# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Windows-native helpers to show/focus the pywebview main window.

pywebview on WinForms marshals show/restore via ``Control.Invoke()``, which blocks
the caller until the UI thread processes the message. When the UI thread is busy
or wedged, floating-widget clicks hang and the app becomes "Not Responding".

These helpers use Win32 APIs directly from any thread and do not require Invoke.
"""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger("jiuwenavatar.channels.desktop.win_window")

SW_RESTORE = 9
SW_SHOW = 5
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040


def get_pywebview_hwnd(window) -> int | None:
    """Return the Win32 HWND for a pywebview window, or None."""
    if window is None:
        return None
    try:
        native = getattr(window, "native", None)
        if native is None:
            return None
        handle = getattr(native, "Handle", None)
        if handle is None:
            return None
        return int(handle.ToInt32())
    except Exception as exc:
        logger.debug("get_pywebview_hwnd failed: %s", exc)
        return None


def find_window_by_title(title: str) -> int | None:
    """Find a top-level window HWND by exact title."""
    if sys.platform != "win32":
        return None
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        return int(hwnd) if hwnd else None
    except Exception as exc:
        logger.debug("find_window_by_title failed: %s", exc)
        return None


def bring_window_to_front(hwnd: int | None, *, title_fallback: str = "JiuwenAvatar") -> bool:
    """Show, restore, and focus a window without going through WinForms Invoke."""
    if sys.platform != "win32":
        return False

    if not hwnd:
        hwnd = find_window_by_title(title_fallback)
    if not hwnd:
        logger.debug("bring_window_to_front: no hwnd")
        return False

    user32 = ctypes.windll.user32
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        # Brief topmost flash brings the window above other apps reliably.
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        user32.SetWindowPos(
            hwnd,
            HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        _set_foreground_window(hwnd)
        return True
    except Exception as exc:
        logger.warning("bring_window_to_front failed: %s", exc)
        return False


def _set_foreground_window(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    current = user32.GetForegroundWindow()
    if current == hwnd:
        return

    current_thread = kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(current, None)
    attached = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
