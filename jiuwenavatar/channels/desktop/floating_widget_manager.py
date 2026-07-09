# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-avatar desktop floating widgets — one buoy per digital avatar instance."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from jiuwenavatar.channels.desktop.brand_assets import (
    prepare_floating_icon,
    resolve_avatar_logo_source,
    resolve_avatar_role_label,
)

logger = logging.getLogger("jiuwenavatar.channels.desktop.floating")

def _dpi_scale() -> float:
    """Return the DPI scaling factor (1.0 = 100%).

    On Windows the reported logical DPI is divided by 96 (the default).
    All pixel constants in this module are multiplied by this factor so that
    the widget has the same physical size regardless of system scaling.
    """
    try:
        if os.name == "nt":
            import ctypes
            try:
                hdc = ctypes.windll.user32.GetDC(0)
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
                if dpi > 0:
                    return dpi / 96.0
            except Exception:
                pass
    except Exception:
        pass
    return 1.0


_DPI = _dpi_scale()

# Base pixel values at 100% DPI; multiplied by _DPI so that physical size
# stays the same regardless of system scaling.
_WIDGET_WIDTH = max(36, int(round(72 * _DPI)))
_ICON_SIZE = max(28, int(round(56 * _DPI)))
_ICON_SIZE_HOVER = max(32, int(round(60 * _DPI)))
_ICON_SIZE_PRESSED = max(24, int(round(52 * _DPI)))
_WIDGET_HEIGHT = _ICON_SIZE + 8
_ICON_CENTER_Y = _ICON_SIZE // 2 + 4
_SLOT_GAP = max(8, int(round(18 * _DPI)))
_BADGE_RADIUS = max(5, int(round(10 * _DPI)))
_BUSY_DOT_RADIUS = max(5, int(round(9 * _DPI)))
_BUSY_DOT_RIM = 0.68
_BUSY_DOT_ANGLE = math.radians(42)
_AVATAR_SYNC_INTERVAL = 3.0


def _get_work_area() -> tuple[int, int, int, int]:
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
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return (0, 0, 1920, 1080)


def _slot_position(slot_index: int) -> tuple[int, int]:
    left, top, right, bottom = _get_work_area()
    work_height = bottom - top
    x = right - _WIDGET_WIDTH - 5
    total_slots = max(1, slot_index + 1)
    stack_height = total_slots * _WIDGET_HEIGHT + max(0, total_slots - 1) * _SLOT_GAP
    base_y = top + max(0, (work_height - stack_height) // 2)
    y = base_y + slot_index * (_WIDGET_HEIGHT + _SLOT_GAP)
    y = max(top, min(y, bottom - _WIDGET_HEIGHT))
    return x, y


class _LogoImages:
    """Cached PIL → PhotoImage variants for one logo file (circular portrait crop)."""

    def __init__(
        self,
        *,
        master,
        role_label: str = "",
        logo_path: Path | None = None,
        logo_image: Any | None = None,
        source_label: str = "",
    ) -> None:
        self.normal = None
        self.hover = None
        self.pressed = None
        self._logo_path = logo_path
        self._load(master=master, role_label=role_label, logo_path=logo_path, logo_image=logo_image, source_label=source_label)

    def _load(
        self,
        *,
        master,
        role_label: str,
        logo_path: Path | None,
        logo_image: Any | None,
        source_label: str,
    ) -> bool:
        try:
            from PIL import Image, ImageEnhance, ImageTk
        except ImportError:
            logger.warning("PIL not available for floating logo")
            return False
        try:
            if logo_image is not None:
                base_img = logo_image.copy()
            elif logo_path is not None:
                base_img = Image.open(str(logo_path))
                base_img.load()
            else:
                return False

            img_normal = prepare_floating_icon(
                base_img, _ICON_SIZE, logo_path=logo_path, role_label=role_label,
            )
            self.normal = ImageTk.PhotoImage(img_normal, master=master)

            img_hover = prepare_floating_icon(
                base_img, _ICON_SIZE_HOVER, logo_path=logo_path, role_label=role_label,
            )
            img_hover = ImageEnhance.Brightness(img_hover).enhance(1.12)
            self.hover = ImageTk.PhotoImage(img_hover, master=master)

            img_pressed = prepare_floating_icon(
                base_img, _ICON_SIZE_PRESSED, logo_path=logo_path, role_label=role_label,
            )
            img_pressed = ImageEnhance.Brightness(img_pressed).enhance(0.92)
            self.pressed = ImageTk.PhotoImage(img_pressed, master=master)
            return True
        except Exception as exc:
            logger.warning("Failed to load logo %s: %s", source_label or logo_path, exc, exc_info=True)
            return False


class AvatarFloatingWidget:
    """Single avatar buoy (tkinter Toplevel)."""

    def __init__(
        self,
        root,
        *,
        avatar_id: str,
        avatar_name: str,
        role_label: str,
        logo_source: dict[str, Any],
        slot_index: int,
        on_open_main: Callable[[str], None] | None,
        on_hide: Callable[[], None] | None,
        on_assign_task: Callable[[str, str], None] | None = None,
        on_open_reports: Callable[[str], None] | None = None,
    ) -> None:
        import tkinter as tk

        self.avatar_id = avatar_id
        self.avatar_name = avatar_name
        self._role_label = role_label
        self.logo_identity = str(logo_source.get("identity") or "")
        self._on_open_main = on_open_main
        self._on_hide = on_hide
        self._on_assign_task = on_assign_task
        self._on_open_reports = on_open_reports
        self._slot_index = slot_index
        self._user_positioned = False
        self._unread_count = 0
        self._is_busy = False
        self._is_hovered = False
        self._is_pressed = False
        self._drag_data: dict[str, Any] = {"x": 0, "y": 0, "moved": False}
        self._visible = True

        bg_color = "gray15"
        self._root = tk.Toplevel(root)
        self._root.title("")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        if os.name == "nt":
            try:
                self._root.attributes("-transparentcolor", bg_color)
            except Exception:
                pass
        else:
            self._root.attributes("-alpha", 0.95)
        self._root.configure(bg=bg_color)

        x, y = _slot_position(slot_index)
        self._root.geometry(f"{_WIDGET_WIDTH}x{_WIDGET_HEIGHT}+{x}+{y}")

        self._canvas = tk.Canvas(
            self._root,
            width=_WIDGET_WIDTH,
            height=_WIDGET_HEIGHT,
            bg=bg_color,
            highlightthickness=0,
        )
        self._canvas.pack()

        artwork_role_label = "" if logo_source.get("suppress_role_label") else role_label
        self._logo = _LogoImages(
            master=self._root,
            role_label=artwork_role_label,
            logo_path=logo_source.get("path"),
            logo_image=logo_source.get("image"),
            source_label=str(logo_source.get("label") or ""),
        )
        # Strong refs — prevent Tk PhotoImage GC before canvas paints
        self._photo_refs = [self._logo.normal, self._logo.hover, self._logo.pressed]

        self._bind_events()
        self._draw_icon()
        logger.info(
            "Avatar floating widget created: avatar_id=%s name=%s slot=%d logo=%s",
            avatar_id,
            avatar_name,
            slot_index,
            logo_source.get("label"),
        )

    def _bind_events(self) -> None:
        self._canvas.bind("<Enter>", self._on_enter)
        self._canvas.bind("<Leave>", self._on_leave)
        self._canvas.bind("<Button-1>", self._on_drag_start)
        self._canvas.bind("<B1-Motion>", self._on_drag_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_click)
        self._canvas.bind("<Button-3>", self._on_right_click)

    def set_slot(self, slot_index: int) -> None:
        """Update stack index only; do not reset position after user drag."""
        self._slot_index = slot_index
        if self._user_positioned:
            return
        x, y = _slot_position(slot_index)
        self._root.geometry(f"{_WIDGET_WIDTH}x{_WIDGET_HEIGHT}+{x}+{y}")

    def update_artwork(self, *, role_label: str, logo_source: dict[str, Any]) -> None:
        """Refresh icon artwork when the underlying Persona icon changed."""
        identity = str(logo_source.get("identity") or "")
        if identity == self.logo_identity and role_label == self._role_label:
            return
        self.logo_identity = identity
        self._role_label = role_label
        artwork_role_label = "" if logo_source.get("suppress_role_label") else role_label
        self._logo = _LogoImages(
            master=self._root,
            role_label=artwork_role_label,
            logo_path=logo_source.get("path"),
            logo_image=logo_source.get("image"),
            source_label=str(logo_source.get("label") or ""),
        )
        self._photo_refs = [self._logo.normal, self._logo.hover, self._logo.pressed]
        self._draw_icon()

    def _draw_icon(self) -> None:
        self._canvas.delete("all")
        center_x = _WIDGET_WIDTH // 2
        center_y = _ICON_CENTER_Y
        if self._is_pressed and self._logo.pressed:
            photo = self._logo.pressed
        elif self._is_hovered and self._logo.hover:
            photo = self._logo.hover
        else:
            photo = self._logo.normal
        if photo is None:
            r = _ICON_SIZE // 2
            self._canvas.create_oval(
                center_x - r, center_y - r, center_x + r, center_y + r,
                fill="#2C88FF", outline="#FFFFFF", width=2,
            )
        else:
            self._canvas.create_image(center_x, center_y, image=photo, anchor="center")
        self._update_badge_display()

    def _update_badge_display(self) -> None:
        self._canvas.delete("badge")
        if self._unread_count > 0:
            bx, by = _WIDGET_WIDTH - 16, 16
            text = str(self._unread_count) if self._unread_count <= 99 else "99+"
            self._canvas.create_oval(
                bx - _BADGE_RADIUS, by - _BADGE_RADIUS, bx + _BADGE_RADIUS, by + _BADGE_RADIUS,
                fill="#F97316", outline="#FFFFFF", width=2, tags="badge",
            )
            self._canvas.create_text(
                bx, by, text=text, font=("Segoe UI", max(5, int(round(8 * _DPI))), "bold"), fill="white", tags="badge",
            )
        if self._is_busy:
            center_x = _WIDGET_WIDTH // 2
            icon_r = _ICON_SIZE // 2
            bx = center_x + icon_r * _BUSY_DOT_RIM * math.cos(_BUSY_DOT_ANGLE)
            by = _ICON_CENTER_Y + icon_r * _BUSY_DOT_RIM * math.sin(_BUSY_DOT_ANGLE)
            r = _BUSY_DOT_RADIUS
            self._canvas.create_oval(
                bx - r, by - r, bx + r, by + r,
                fill="#EF4444", outline="#FFFFFF", width=3, tags="badge",
            )

    def _on_enter(self, _event) -> None:
        self._is_hovered = True
        self._draw_icon()
        self._canvas.config(cursor="hand2")

    def _on_leave(self, _event) -> None:
        self._is_hovered = False
        self._is_pressed = False
        self._draw_icon()
        self._canvas.config(cursor="")

    def _on_drag_start(self, event) -> None:
        self._drag_data = {"x": event.x, "y": event.y, "moved": False}
        self._is_pressed = True
        self._draw_icon()

    def _on_drag_motion(self, event) -> None:
        dx = event.x - self._drag_data["x"]
        dy = event.y - self._drag_data["y"]
        if dx != 0 or dy != 0:
            self._drag_data["moved"] = True
            self._user_positioned = True
            if self._is_pressed:
                self._is_pressed = False
                self._draw_icon()
        x = self._root.winfo_x() + dx
        y = self._root.winfo_y() + dy
        self._root.geometry(f"+{x}+{y}")

    def _on_click(self, _event) -> None:
        self._is_pressed = False
        self._draw_icon()
        if self._drag_data.get("moved"):
            return
        if self._unread_count > 0 and self._is_click_on_badge(_event.x, _event.y):
            self._handle_open()
            return
        if self._on_open_main:
            threading.Thread(
                target=self._safe_open,
                daemon=True,
                name=f"floating-open-{self.avatar_id[:8]}",
            ).start()

    def _is_click_on_badge(self, cx: int, cy: int) -> bool:
        """判断点击是否落在未读角标圆圈内"""
        bx = _WIDGET_WIDTH - 16
        by = 16
        dx = cx - bx
        dy = cy - by
        return dx * dx + dy * dy <= _BADGE_RADIUS * _BADGE_RADIUS

    def _safe_open(self) -> None:
        try:
            if self._on_open_main:
                self._on_open_main(self.avatar_id)
        except Exception as exc:
            logger.error("open_main for avatar %s failed: %s", self.avatar_id, exc)

    def _on_right_click(self, event) -> None:
        import tkinter as tk

        menu = tk.Menu(self._root, tearoff=0)
        if self._on_assign_task is not None:
            menu.add_command(label="分配任务", command=self._open_assign_task_dialog)
            menu.add_separator()
        menu.add_command(label="查看未读报告", command=self._handle_open)
        menu.add_separator()
        menu.add_command(label="隐藏此浮标", command=self.hide)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_assign_task_dialog(self) -> None:
        if self._on_assign_task is None:
            return
        import tkinter as tk
        from tkinter import ttk

        dialog = tk.Toplevel(self._root)
        dialog.title("")
        dialog.resizable(True, True)
        dialog.minsize(440, 300)
        dialog.attributes("-topmost", True)

        # 使用 Windows 原生主题
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        dialog.configure(bg=style.lookup("TFrame", "background", default="#f0f0f0"))

        # 居中
        dialog.update_idletasks()
        w, h = 500, 340
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")

        # ---- 顶部标题 ----
        tk.Label(
            dialog,
            text=f"向「{self.avatar_name}」分配任务",
            fg="#1a1a1a",
            bg=style.lookup("TFrame", "background", default="#f0f0f0"),
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(18, 4))

        tk.Label(
            dialog,
            text="输入任务描述后点击下发，无需打开主窗口",
            fg="#666",
            bg=style.lookup("TFrame", "background", default="#f0f0f0"),
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 12))

        # ---- 输入区域 ----
        text_frame = tk.Frame(
            dialog,
            bg="white",
            highlightbackground=style.lookup("TEntry", "bordercolor", default="#7a7a7a"),
            highlightthickness=1,
            bd=0,
        )
        text_frame.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        text = tk.Text(
            text_frame,
            wrap="word",
            font=("Microsoft YaHei UI", 11),
            fg="#1a1a1a",
            bg="white",
            insertbackground="#0078d4",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            selectbackground="#cce4f7",
            selectforeground="#1a1a1a",
        )
        text.pack(fill="both", expand=True)
        text.focus_set()

        # ---- 底部按钮 ----
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))

        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side="right", padx=(8, 0))

        def _submit() -> None:
            prompt = text.get("1.0", "end").strip()
            if not prompt:
                return
            dialog.destroy()
            try:
                self._on_assign_task(self.avatar_id, prompt)
            except Exception as exc:
                logger.error("assign task for avatar %s failed: %s", self.avatar_id, exc)

        ttk.Button(btn_frame, text="下发任务", command=_submit).pack(side="right")

        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.bind("<Return>", lambda _e: _submit())

    def _handle_open(self) -> None:
        if self._on_open_reports:
            threading.Thread(
                target=self._safe_open_reports,
                daemon=True,
                name=f"floating-open-reports-{self.avatar_id[:8]}",
            ).start()

    def _safe_open_reports(self) -> None:
        try:
            if self._on_open_reports:
                self._on_open_reports(self.avatar_id)
        except Exception as exc:
            logger.error("open_reports for avatar %s failed: %s", self.avatar_id, exc)

    def set_badge_state(self, *, unread_count: int, busy: bool) -> None:
        self._unread_count = max(0, int(unread_count))
        self._is_busy = busy
        self._root.after(0, self._update_badge_display)

    def set_unread_badge(self, count: int) -> None:
        self.set_badge_state(unread_count=count, busy=self._is_busy)

    def clear_badge(self) -> None:
        self.set_badge_state(unread_count=0, busy=self._is_busy)

    def show(self) -> None:
        if not self._visible:
            self._root.deiconify()
            self._visible = True

    def hide(self) -> None:
        if self._visible:
            self._root.withdraw()
            self._visible = False

    def destroy(self) -> None:
        try:
            self._root.destroy()
        except Exception:
            pass


class FloatingWidgetManager:
    """Manage zero or more avatar floating widgets (one per avatar instance)."""

    def __init__(
        self,
        on_open_main: Callable[[str], None] | None = None,
        on_hide: Callable[[], None] | None = None,
        on_assign_task: Callable[[str, str], None] | None = None,
        unread_provider: Callable[[], tuple[dict[str, int], dict[str, int]]] | None = None,
        on_open_reports: Callable[[str], None] | None = None,
    ) -> None:
        self._on_open_main = on_open_main
        self._on_hide = on_hide
        self._on_assign_task = on_assign_task
        self._on_open_reports = on_open_reports
        self._unread_provider = unread_provider
        self._root = None
        self._widgets: dict[str, AvatarFloatingWidget] = {}
        self._started = False
        self._lock = threading.Lock()
        self._sync_thread: threading.Thread | None = None
        self._sync_running = False
        self._global_visible = True

    def start(self) -> bool:
        with self._lock:
            if self._started:
                return True
            self._started = True
        threading.Thread(target=self._run_tk, name="floating-widget-manager", daemon=True).start()
        self._start_avatar_sync()
        return True

    create_window = start

    def _run_tk(self) -> None:
        try:
            import tkinter as tk

            self._root = tk.Tk()
            self._root.withdraw()
            logger.info("Floating widget manager tk root ready")
            self._root.after(200, lambda: self._poll_avatars_once())
            self._root.mainloop()
        except Exception as exc:
            logger.error("Floating widget manager failed: %s", exc, exc_info=True)
        finally:
            self._started = False
            self._root = None

    def _start_avatar_sync(self) -> None:
        if self._sync_thread is not None:
            return
        self._sync_running = True
        self._sync_thread = threading.Thread(target=self._avatar_sync_loop, name="avatar-floating-sync", daemon=True)
        self._sync_thread.start()

    def _avatar_sync_loop(self) -> None:
        while self._sync_running:
            self._poll_avatars_once()
            time.sleep(_AVATAR_SYNC_INTERVAL)

    def _poll_avatars_once(self) -> None:
        try:
            from jiuwenavatar.server.runtime.persona.manager import PersonaManager

            mgr = PersonaManager.get_instance()
            mgr.reload(log=False)
            avatars = mgr.list_avatars()
            self.sync_avatars(avatars)
        except Exception as exc:
            logger.warning("[floating] avatar sync failed: %s", exc)

    def sync_avatars(self, avatars: list[dict[str, Any]]) -> None:
        if self._root is None:
            return

        def _apply() -> None:
            desired_ids = {str(a.get("id") or "") for a in avatars if a.get("id")}
            for aid in list(self._widgets):
                if aid not in desired_ids:
                    logger.info("[floating] removing widget for deleted avatar %s", aid)
                    self._widgets[aid].destroy()
                    del self._widgets[aid]

            for slot, avatar in enumerate(avatars):
                aid = str(avatar.get("id") or "")
                if not aid:
                    continue
                name = str(avatar.get("name") or aid)
                logo_source = resolve_avatar_logo_source(avatar)
                if logo_source is None:
                    logger.warning("[floating] no logo for avatar %s, skipping buoy", aid)
                    continue
                role_label = resolve_avatar_role_label(avatar)
                if aid in self._widgets:
                    self._widgets[aid].set_slot(slot)
                    self._widgets[aid].update_artwork(role_label=role_label, logo_source=logo_source)
                    continue
                widget = AvatarFloatingWidget(
                    self._root,
                    avatar_id=aid,
                    avatar_name=name,
                    role_label=role_label,
                    logo_source=logo_source,
                    slot_index=slot,
                    on_open_main=self._on_open_main,
                    on_hide=self._on_hide,
                    on_assign_task=self._on_assign_task,
                    on_open_reports=self._on_open_reports,
                )
                if not self._global_visible:
                    widget.hide()
                self._widgets[aid] = widget
                logger.info("[floating] created buoy for avatar %s (%s)", aid, name)

            if not avatars:
                logger.debug("[floating] no avatars — no buoys shown")
            self.refresh_unread_badges()

        try:
            self._root.after(0, _apply)
        except Exception as exc:
            logger.debug("[floating] sync_avatars schedule failed: %s", exc)

    def refresh_unread_badges(self) -> None:
        if self._root is None:
            return

        def _apply() -> None:
            try:
                if self._unread_provider is not None:
                    unread_counts, active_counts = self._unread_provider()
                else:
                    from jiuwenavatar.gateway.report.read_state import (
                        count_active_missions_by_avatar,
                        count_unread_missions_by_avatar,
                    )

                    unread_counts = count_unread_missions_by_avatar()
                    active_counts = count_active_missions_by_avatar()
                for aid, widget in self._widgets.items():
                    widget.set_badge_state(
                        unread_count=unread_counts.get(aid, 0),
                        busy=active_counts.get(aid, 0) > 0,
                    )
            except Exception as exc:
                logger.debug("[floating] unread badge refresh failed: %s", exc)

        try:
            self._root.after(0, _apply)
        except Exception as exc:
            logger.debug("[floating] refresh_unread_badges schedule failed: %s", exc)

    def clear_badge(self, avatar_id: str | None = None) -> None:
        if avatar_id:
            widget = self._widgets.get(avatar_id)
            if widget:
                widget.clear_badge()
            return
        for widget in self._widgets.values():
            widget.clear_badge()

    def show(self) -> None:
        self._global_visible = True
        for widget in self._widgets.values():
            widget.show()

    def hide(self) -> None:
        self._global_visible = False
        for widget in self._widgets.values():
            widget.hide()

    def toggle(self) -> None:
        if self._global_visible:
            self.hide()
        else:
            self.show()

    def destroy(self) -> None:
        self._sync_running = False
        if self._root is not None:
            def _destroy_all() -> None:
                for widget in list(self._widgets.values()):
                    widget.destroy()
                self._widgets.clear()
                try:
                    self._root.quit()
                except Exception:
                    pass

            try:
                self._root.after(0, _destroy_all)
            except Exception:
                pass
        self._started = False
        logger.info("Floating widget manager destroyed")

    @property
    def badge_count(self) -> int:
        return sum(w._success_count + w._failure_count for w in self._widgets.values())
