# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""桌面悬浮图标 - 类似豆包的悬浮小部件.

使用 tkinter 实现真正的桌面悬浮窗口，独立于主应用窗口。

功能：
  - 悬浮在桌面右侧，可拖拽移动
  - 有新消息时显示红色数字角标（左上角）
  - 点击打开主窗口
  - 右键菜单支持隐藏
  - 鼠标悬停时有视觉反馈（放大、发光效果）
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Callable

from jiuwenavatar.channels.desktop.brand_assets import find_logo_path, prepare_floating_icon

logger = logging.getLogger("jiuwenavatar.channels.desktop.floating")

_WIDGET_SIZE = 72  # Slightly larger for better visibility
_ICON_SIZE = 56
_ICON_SIZE_HOVER = 62  # Larger on hover
_ICON_SIZE_PRESSED = 52  # Smaller when pressed


class FloatingWidget:
    """Desktop floating widget like Doubao.

    Uses tkinter to create a truly independent desktop window.
    Features hover effects and smooth interactions.
    """

    def __init__(
        self,
        on_open_main: Callable[[], None] | None = None,
        on_hide: Callable[[], None] | None = None,
    ) -> None:
        self._on_open_main = on_open_main
        self._on_hide = on_hide
        self._root = None
        self._canvas = None
        self._badge_text = None
        self._badge_bg = None
        self._visible = True
        self._success_count = 0  # 成功任务数（蓝色，左上角）
        self._failure_count = 0  # 失败任务数（红色，右上角）
        self._lock = threading.Lock()
        self._started = False
        self._drag_data = {"x": 0, "y": 0, "moved": False}
        
        # Image caches for different states
        self._photo_normal = None
        self._photo_hover = None
        self._photo_pressed = None
        self._current_image_id = None
        
        # Hover state
        self._is_hovered = False
        self._is_pressed = False

    def _get_screen_size(self) -> tuple[int, int]:
        """Get primary screen dimensions."""
        try:
            if os.name == "nt":
                import ctypes
                user32 = ctypes.windll.user32
                return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
            else:
                return (1920, 1080)
        except Exception:
            return (1920, 1080)

    def _get_work_area(self) -> tuple[int, int, int, int]:
        """Get work area (screen minus taskbar) on Windows.
        
        Returns:
            (left, top, right, bottom) of the work area
        """
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                
                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG),
                    ]
                
                rect = RECT()
                # SPI_GETWORKAREA = 0x0030
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
                return (rect.left, rect.top, rect.right, rect.bottom)
            else:
                w, h = self._get_screen_size()
                return (0, 0, w, h)
        except Exception:
            w, h = self._get_screen_size()
            return (0, 0, w, h)

    def _get_initial_position(self) -> tuple[int, int]:
        """Get initial position at right edge of primary screen, avoiding taskbar."""
        left, top, right, bottom = self._get_work_area()
        work_width = right - left
        work_height = bottom - top
        
        # Position at right edge of work area (not screen), avoiding taskbar
        x = right - _WIDGET_SIZE - 5  # 5px padding from edge
        y = top + (work_height - _WIDGET_SIZE) // 2
        
        return (x, y)

    def _snap_to_right_edge(self, current_y: int | None = None) -> None:
        """Snap the widget to the right edge of the work area (avoiding taskbar)."""
        if self._root is None:
            return
        
        left, top, right, bottom = self._get_work_area()
        work_height = bottom - top
        
        x = right - _WIDGET_SIZE - 5  # 5px padding from edge, avoid taskbar
        
        # Keep current Y position if provided, otherwise get from window
        if current_y is None:
            try:
                current_y = self._root.winfo_y()
            except Exception:
                current_y = top + (work_height - _WIDGET_SIZE) // 2
        
        # Clamp Y to work area bounds
        current_y = max(top, min(current_y, bottom - _WIDGET_SIZE))
        
        self._root.geometry(f"+{x}+{current_y}")

    def _load_logo_images(self):
        """Load logo images for normal, hover, and pressed states."""
        try:
            from PIL import Image, ImageTk, ImageEnhance, ImageFilter
        except ImportError:
            logger.warning("PIL not available, using fallback icon")
            return False

        logo_path = find_logo_path()
        
        try:
            if logo_path is None:
                logger.error("Brand logo missing; floating widget will show placeholder")
                return False

            base_img = Image.open(str(logo_path))
            base_img.load()
            logger.info("Loaded floating widget logo from %s", logo_path)

            img_normal = prepare_floating_icon(base_img, _ICON_SIZE, logo_path=logo_path)
            self._photo_normal = ImageTk.PhotoImage(img_normal)

            img_hover = prepare_floating_icon(base_img, _ICON_SIZE_HOVER, logo_path=logo_path)
            enhancer = ImageEnhance.Brightness(img_hover)
            img_hover = enhancer.enhance(1.15)
            self._photo_hover = ImageTk.PhotoImage(img_hover)

            img_pressed = prepare_floating_icon(base_img, _ICON_SIZE_PRESSED, logo_path=logo_path)
            enhancer = ImageEnhance.Brightness(img_pressed)
            img_pressed = enhancer.enhance(0.9)
            self._photo_pressed = ImageTk.PhotoImage(img_pressed)
            
            logger.info("Logo images loaded successfully")
            return True
            
        except Exception as exc:
            logger.warning("Failed to load logo images: %s", exc)
            return False

    def _create_fallback_image(self):
        """Create a fallback PIL image when no logo file is found."""
        from PIL import Image, ImageDraw
        
        size = 256  # High res for scaling
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Blue gradient-like background
        padding = 8
        corner_radius = size // 4
        base_color = (44, 136, 255, 255)
        
        draw.rounded_rectangle(
            [(padding, padding), (size - padding - 1, size - padding - 1)],
            radius=corner_radius,
            fill=base_color
        )
        
        # Draw team network pattern
        icon_color = (255, 255, 255, 255)
        center = size // 2
        
        # Main center circle
        r_main = 24
        draw.ellipse(
            [center - r_main, center - r_main, center + r_main, center + r_main],
            fill=icon_color
        )
        
        # Corner circles
        r_small = 16
        offset = 64
        positions = [
            (center - offset, center - offset),
            (center + offset, center - offset),
            (center - offset, center + offset),
            (center + offset, center + offset),
        ]
        
        for px, py in positions:
            draw.ellipse(
                [px - r_small, py - r_small, px + r_small, py + r_small],
                fill=icon_color
            )
        
        # Connecting lines
        line_width = 8
        for px, py in positions:
            draw.line([(center, center), (px, py)], fill=icon_color, width=line_width)
        
        return img

    def _draw_icon(self):
        """Draw the floating icon."""
        if self._canvas is None:
            return

        self._canvas.delete("all")
        
        # Load images if not already loaded
        if self._photo_normal is None:
            self._load_logo_images()
        
        center_x = _WIDGET_SIZE // 2
        center_y = _WIDGET_SIZE // 2
        
        # Select appropriate image based on state
        if self._is_pressed and self._photo_pressed:
            photo = self._photo_pressed
        elif self._is_hovered and self._photo_hover:
            photo = self._photo_hover
        elif self._photo_normal:
            photo = self._photo_normal
        else:
            # Ultimate fallback - draw simple circle
            self._draw_simple_fallback()
            return
        
        # Draw the image
        self._current_image_id = self._canvas.create_image(
            center_x, center_y, image=photo, anchor="center"
        )
        
        # Draw badge on top
        self._update_badge_display()

    def _draw_simple_fallback(self):
        """Draw a simple fallback icon when images fail."""
        center_x = _WIDGET_SIZE // 2
        center_y = _WIDGET_SIZE // 2
        radius = _ICON_SIZE // 2
        
        # Blue circle
        self._canvas.create_oval(
            center_x - radius, center_y - radius,
            center_x + radius, center_y + radius,
            fill="#2C88FF", outline=""
        )
        
        # Team icon (simplified)
        r_center = 6
        r_outer = 4
        offset = 14
        line_color = "white"
        
        # Center dot
        self._canvas.create_oval(
            center_x - r_center, center_y - r_center,
            center_x + r_center, center_y + r_center,
            fill="white", outline=""
        )
        
        # Corner dots and lines
        positions = [
            (center_x - offset, center_y - offset),
            (center_x + offset, center_y - offset),
            (center_x - offset, center_y + offset),
            (center_x + offset, center_y + offset),
        ]
        
        for px, py in positions:
            self._canvas.create_line(center_x, center_y, px, py, fill=line_color, width=2)
            self._canvas.create_oval(
                px - r_outer, py - r_outer, px + r_outer, py + r_outer,
                fill="white", outline=""
            )
        
        self._update_badge_display()

    def _update_badge_display(self):
        """Update the badge count display.
        
        成功任务：蓝色圆圈，左上角
        失败任务：红色圆圈，右上角
        两者可以同时显示
        """
        if self._canvas is None:
            return

        # Delete old badge elements
        self._canvas.delete("badge")

        badge_radius = 10
        
        # 成功 badge（蓝色，左上角）
        if self._success_count > 0:
            badge_x = 12
            badge_y = 12
            text = str(self._success_count) if self._success_count <= 99 else "99+"
            
            # Blue circle with white border
            self._canvas.create_oval(
                badge_x - badge_radius, badge_y - badge_radius,
                badge_x + badge_radius, badge_y + badge_radius,
                fill="#3B82F6", outline="#FFFFFF", width=2, tags="badge"
            )
            self._canvas.create_text(
                badge_x, badge_y,
                text=text,
                font=("Segoe UI", 8, "bold"),
                fill="white",
                tags="badge"
            )

        # 失败 badge（红色，右上角）
        if self._failure_count > 0:
            badge_x = _WIDGET_SIZE - 12
            badge_y = 12
            text = str(self._failure_count) if self._failure_count <= 99 else "99+"
            
            # Red circle with white border
            self._canvas.create_oval(
                badge_x - badge_radius, badge_y - badge_radius,
                badge_x + badge_radius, badge_y + badge_radius,
                fill="#EF4444", outline="#FFFFFF", width=2, tags="badge"
            )
            self._canvas.create_text(
                badge_x, badge_y,
                text=text,
                font=("Segoe UI", 8, "bold"),
                fill="white",
                tags="badge"
            )

    def _on_enter(self, event):
        """Handle mouse enter - show hover effect."""
        self._is_hovered = True
        self._draw_icon()
        # Change cursor to hand
        if self._canvas:
            self._canvas.config(cursor="hand2")

    def _on_leave(self, event):
        """Handle mouse leave - remove hover effect."""
        self._is_hovered = False
        self._is_pressed = False
        self._draw_icon()
        if self._canvas:
            self._canvas.config(cursor="")

    def _on_drag_start(self, event):
        """Record initial position for dragging."""
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y
        self._drag_data["moved"] = False
        self._is_pressed = True
        self._draw_icon()

    def _on_drag_motion(self, event):
        """Handle drag motion."""
        if self._root is None:
            return

        dx = event.x - self._drag_data["x"]
        dy = event.y - self._drag_data["y"]

        if abs(dx) > 3 or abs(dy) > 3:
            self._drag_data["moved"] = True
            # When dragging, show normal state
            if self._is_pressed:
                self._is_pressed = False
                self._draw_icon()

        x = self._root.winfo_x() + dx
        y = self._root.winfo_y() + dy
        self._root.geometry(f"+{x}+{y}")

    def _on_click(self, event):
        """Handle click - open main window if not dragging, snap to right edge after drag."""
        self._is_pressed = False
        self._draw_icon()
        
        if self._drag_data.get("moved", False):
            # Dragged - snap back to right edge
            try:
                current_y = self._root.winfo_y() if self._root else None
                self._snap_to_right_edge(current_y)
            except Exception as exc:
                logger.debug("snap_to_right_edge failed: %s", exc)
        else:
            # Clicked without dragging - open main window
            # 在新线程中执行回调，避免 tkinter 线程与 pywebview 主线程死锁
            if self._on_open_main:
                threading.Thread(
                    target=self._safe_open_main_callback,
                    daemon=True,
                    name="floating-open-main"
                ).start()

    def _on_right_click(self, event):
        """Handle right click - show context menu."""
        if self._root is None:
            return

        try:
            import tkinter as tk
            menu = tk.Menu(self._root, tearoff=0)
            menu.add_command(label="打开主面板", command=self._handle_open)
            menu.add_separator()
            menu.add_command(label="隐藏悬浮图标", command=self._handle_hide)
            menu.tk_popup(event.x_root, event.y_root)
        except Exception as exc:
            logger.debug("Context menu failed: %s", exc)

    def _handle_hide(self):
        """Handle hide from context menu."""
        self.hide()
        if self._on_hide:
            try:
                self._on_hide()
            except Exception as exc:
                logger.error("on_hide callback failed: %s", exc)

    def _handle_open(self):
        """Handle open from context menu."""
        # 在新线程中执行回调，避免 tkinter 线程与 pywebview 主线程死锁
        if self._on_open_main:
            threading.Thread(
                target=self._safe_open_main_callback,
                daemon=True,
                name="floating-open-main"
            ).start()

    def _safe_open_main_callback(self):
        """Thread-safe wrapper for open_main callback."""
        try:
            logger.info("[floating] open main window requested")
            if self._on_open_main:
                self._on_open_main()
        except Exception as exc:
            logger.error("open_main_window callback failed: %s", exc)

    def _run_tk(self):
        """Run the tkinter main loop in a separate thread."""
        logger.info("_run_tk: starting tkinter initialization")
        try:
            import tkinter as tk
            logger.info("_run_tk: tkinter imported successfully")
        except ImportError as e:
            logger.error("tkinter not available, floating widget disabled: %s", e)
            return

        try:
            logger.info("_run_tk: creating Tk root window")
            self._root = tk.Tk()
            self._root.title("")

            logger.info("_run_tk: configuring window attributes")
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)

            if os.name == "nt":
                try:
                    self._root.attributes("-transparentcolor", "gray15")
                    logger.info("_run_tk: Windows transparent color set")
                except Exception as e:
                    logger.warning("_run_tk: transparentcolor failed: %s", e)
                bg_color = "gray15"
            else:
                self._root.attributes("-alpha", 0.95)
                bg_color = "gray15"

            self._root.configure(bg=bg_color)

            x, y = self._get_initial_position()
            logger.info("_run_tk: initial position (%d, %d)", x, y)
            self._root.geometry(f"{_WIDGET_SIZE}x{_WIDGET_SIZE}+{x}+{y}")

            self._canvas = tk.Canvas(
                self._root,
                width=_WIDGET_SIZE,
                height=_WIDGET_SIZE,
                bg=bg_color,
                highlightthickness=0
            )
            self._canvas.pack()

            self._draw_icon()
            logger.info("_run_tk: icon drawn, bindings being set")

            # Mouse bindings
            self._canvas.bind("<Enter>", self._on_enter)
            self._canvas.bind("<Leave>", self._on_leave)
            self._canvas.bind("<Button-1>", self._on_drag_start)
            self._canvas.bind("<B1-Motion>", self._on_drag_motion)
            self._canvas.bind("<ButtonRelease-1>", self._on_click)
            self._canvas.bind("<Button-3>", self._on_right_click)

            logger.info("Floating widget started at (%d, %d), entering mainloop", x, y)

            self._root.mainloop()
            logger.info("_run_tk: mainloop exited")

        except Exception as exc:
            logger.error("Floating widget failed: %s", exc, exc_info=True)
        finally:
            self._started = False
            self._root = None
            logger.info("_run_tk: cleanup complete")

    def start(self) -> None:
        """Start the floating widget in a background thread."""
        with self._lock:
            if self._started:
                logger.info("Floating widget already started, skipping")
                return
            self._started = True

        logger.info("Starting floating widget thread...")
        thread = threading.Thread(target=self._run_tk, name="floating-widget", daemon=True)
        thread.start()
        logger.info("Floating widget thread started")

    def create_window(self) -> bool:
        """Alias for start() for compatibility."""
        self.start()
        return True

    def show(self) -> None:
        """Show the floating widget."""
        if self._root and not self._visible:
            def _show():
                try:
                    self._root.deiconify()
                    self._visible = True
                    logger.info("Floating widget shown")
                except Exception as exc:
                    logger.debug("show failed: %s", exc)
            try:
                self._root.after(0, _show)
            except Exception:
                pass

    def hide(self) -> None:
        """Hide the floating widget."""
        if self._root and self._visible:
            def _hide():
                try:
                    self._root.withdraw()
                    self._visible = False
                    logger.info("Floating widget hidden")
                except Exception as exc:
                    logger.debug("hide failed: %s", exc)
            try:
                self._root.after(0, _hide)
            except Exception:
                pass

    def toggle(self) -> None:
        """Toggle visibility."""
        if self._root:
            def _toggle():
                try:
                    if self._visible:
                        self._root.withdraw()
                        self._visible = False
                        logger.info("Floating widget hidden")
                    else:
                        self._root.deiconify()
                        self._visible = True
                        logger.info("Floating widget shown")
                except Exception as exc:
                    logger.debug("toggle failed: %s", exc)
            try:
                self._root.after(0, _toggle)
            except Exception:
                pass

    def set_success_count(self, count: int) -> None:
        """Update the success badge count (blue, top-left)."""
        self._success_count = max(0, count)
        self._refresh_badge()

    def set_failure_count(self, count: int) -> None:
        """Update the failure badge count (red, top-right)."""
        self._failure_count = max(0, count)
        self._refresh_badge()

    def _refresh_badge(self) -> None:
        """Refresh badge display on UI thread."""
        if self._root and self._canvas:
            try:
                self._root.after(0, self._update_badge_display)
            except Exception as exc:
                logger.debug("_refresh_badge failed: %s", exc)

    def increment_badge(self, is_failure: bool = False) -> None:
        """Increment badge count by 1.
        
        Args:
            is_failure: True = 失败（红色右上角），False = 成功（蓝色左上角）
        """
        if is_failure:
            self._failure_count += 1
        else:
            self._success_count += 1
        self._refresh_badge()
        logger.info("[floating] badge updated: success=%d failure=%d", self._success_count, self._failure_count)

    def clear_badge(self) -> None:
        """Clear both badge counts."""
        self._success_count = 0
        self._failure_count = 0
        self._refresh_badge()

    def destroy(self) -> None:
        """Stop and destroy the floating widget."""
        if not self._started:
            return

        if self._root:
            def _destroy():
                try:
                    self._root.quit()
                except Exception:
                    pass
            try:
                self._root.after(0, _destroy)
            except Exception as exc:
                logger.debug("destroy failed: %s", exc)

        self._started = False
        logger.info("Floating widget destroyed")

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def badge_count(self) -> int:
        """Total badge count (success + failure)."""
        return self._success_count + self._failure_count
    
    @property
    def success_count(self) -> int:
        return self._success_count
    
    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def window(self):
        return self._root


# Backward-compatible alias — desktop now uses per-avatar FloatingWidgetManager.
from jiuwenavatar.channels.desktop.floating_widget_manager import FloatingWidgetManager as FloatingWidget  # noqa: E402,F401
