# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System tray icon for Windows desktop.

提供系统托盘图标、右键菜单、Windows 气球通知/Toast 通知功能。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from jiuwenavatar.channels.desktop.brand_assets import find_logo_path, prepare_tray_icon_image

logger = logging.getLogger("jiuwenavatar.channels.desktop.tray")


_PIL_AVAILABLE = False
try:
    from PIL import Image, ImageDraw, ImageFont  # noqa: F401

    _PIL_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]

_PYSTRAY_AVAILABLE = False
try:
    import pystray  # noqa: F401

    _PYSTRAY_AVAILABLE = True
except ImportError:
    pystray = None  # type: ignore[assignment]


def _create_tray_image() -> Any:
    """Create a default tray icon (blue rounded square with team network icon).

    Falls back gracefully when PIL is not available.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow not installed, tray icon will be skipped")
        return None  # type: ignore[return-value]

    size = (64, 64)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Blue rounded background (gradient-like color from the SVG)
    draw.rounded_rectangle([(2, 2), (61, 61)], radius=14, fill=(44, 136, 255, 255))

    # Draw team network icon (white circles connected by lines)
    icon_color = (255, 255, 255, 255)
    center = 32

    # Main center circle
    r_main = 6
    draw.ellipse(
        [center - r_main, center - r_main, center + r_main, center + r_main],
        fill=icon_color
    )

    # Corner circles (smaller)
    r_small = 4
    offset = 14
    positions = [
        (center - offset, center - offset),  # Top-left
        (center + offset, center - offset),  # Top-right
        (center - offset, center + offset),  # Bottom-left
        (center + offset, center + offset),  # Bottom-right
    ]

    # Draw connecting lines first (so circles are on top)
    for px, py in positions:
        draw.line([(center, center), (px, py)], fill=icon_color, width=2)

    # Draw corner circles
    for px, py in positions:
        draw.ellipse(
            [px - r_small, py - r_small, px + r_small, py + r_small],
            fill=icon_color
        )

    return img


def _load_icon_file() -> Any:
    """Try loading logo.ico/png as tray icon."""
    if not _PIL_AVAILABLE:
        return None

    logo_path = find_logo_path()
    if logo_path is None:
        logger.warning("No tray logo file found")
        return None

    try:
        img = Image.open(str(logo_path))
        img.load()
        logger.info("Loaded tray icon from: %s", logo_path)
        return prepare_tray_icon_image(img, size=64)
    except Exception as exc:
        logger.warning("Failed to load tray icon from %s: %s", logo_path, exc)
        return None


class TrayIcon:
    """System tray icon with right-click menu and notification support.

    Usage::

        tray = TrayIcon(on_open=show_window, on_quit=cleanup_and_exit)
        tray.start()
        tray.show_notification("Task completed", "dev-reviewer finished")
    """

    def __init__(
        self,
        on_open: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        on_toggle_floating: Callable[[], None] | None = None,
    ) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._on_toggle_floating = on_toggle_floating
        self._icon: Any = None  # pystray.Icon
        self._lock = RLock()
        self._started = False

    def start(self) -> None:
        """Start the tray icon in a background thread."""
        with self._lock:
            if self._started:
                return
            self._started = True

        if not _PYSTRAY_AVAILABLE:
            logger.warning(
                "pystray not installed; tray icon disabled. "
                "Install with: pip install pystray Pillow"
            )
            return

        image = _load_icon_file() or _create_tray_image()
        if image is None:
            logger.warning("Failed to create tray icon image")
            return

        menu_items = [
            pystray.MenuItem("打开主面板", self._on_open_clicked, default=True),
        ]
        if self._on_toggle_floating is not None:
            menu_items.append(
                pystray.MenuItem("显示/隐藏悬浮图标", self._on_toggle_floating_clicked)
            )
        menu_items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_quit_clicked),
        ])
        menu = pystray.Menu(*menu_items)

        self._icon = pystray.Icon(
            "jiuwenavatar",
            image,
            "JiuwenAvatar",
            menu,
        )
        # run_detached starts the icon in a daemon thread
        self._icon.run_detached()
        logger.info("Tray icon started")

    def stop(self) -> None:
        """Stop and remove the tray icon."""
        with self._lock:
            if not self._started:
                return
            self._started = False

        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:
                logger.debug("Tray icon stop ignored: %s", exc)
            self._icon = None
            logger.info("Tray icon stopped")

    def show_notification(
        self,
        title: str,
        message: str,
        status: str = "completed",
    ) -> None:
        """Show a balloon / toast notification from the tray icon.

        Args:
            title: Notification title (e.g. "任务完成")
            message: Notification body text
            status: "completed" or "failed" (affects icon/behavior)
        """
        if self._icon is None:
            return
        try:
            # pystray's notify() shows a balloon/Toast notification
            self._icon.notify(message, title=title)
            logger.info(
                "Tray notification: title=%s status=%s msg_len=%d",
                title, status, len(message),
            )
        except Exception as exc:
            logger.debug("Tray notify failed (non-critical): %s", exc)

    def _on_open_clicked(self) -> None:
        if self._on_open:
            try:
                self._on_open()
            except Exception as exc:
                logger.error("Tray 'open' callback failed: %s", exc)

    def _on_toggle_floating_clicked(self) -> None:
        if self._on_toggle_floating:
            try:
                self._on_toggle_floating()
            except Exception as exc:
                logger.error("Tray 'toggle floating' callback failed: %s", exc)

    def _on_quit_clicked(self) -> None:
        if self._on_quit:
            try:
                self._on_quit()
            except Exception as exc:
                logger.error("Tray 'quit' callback failed: %s", exc)


try:
    from typing import Any
except ImportError:
    pass  # pragma: no cover