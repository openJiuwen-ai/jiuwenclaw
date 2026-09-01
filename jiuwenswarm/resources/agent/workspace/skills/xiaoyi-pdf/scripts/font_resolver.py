import os
import platform


def _get_system_font_dirs() -> list:
    """Return list of system font directories for current platform."""
    system = platform.system()
    paths = []

    if system == "Windows":
        paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"),
        ]
    else:  # Linux
        paths = [
            "/usr/share/fonts",
            os.path.expanduser("~/.local/share/fonts"),
            "/usr/local/share/fonts",
        ]

    return [p for p in paths if os.path.exists(p)]


_HARMONY_FONTS = {
    "HarmonyHeiTi": {
        "regular": ["Harmony-Regular.ttf"],
        "bold":    ["Harmony-Bold.ttf"],
    }
}

def _find_font_file(candidates: list) -> str | None:
    """Search for font file in system font directories."""
    font_dirs = _get_system_font_dirs()
    for font_dir in font_dirs:
        for root, _dirs, files in os.walk(font_dir):
            for filename in files:
                if filename.lower() in [c.lower() for c in candidates]:
                    return os.path.join(root, filename)
    return None


def resolve_reportlab_fonts(target_name: str = "HarmonyHeiTi") -> dict | None:
    """Find regular+bold font files for ReportLab registration.

    Returns {"regular": str, "bold": str, "resolved_name": str} or None.
    """
    # 固定使用 HarmonyHeiTi，不再 fallback 到其他 CJK 字体
    harmony = _HARMONY_FONTS.get(target_name)
    if harmony:
        regular_path = _find_font_file(harmony["regular"])
        bold_path = _find_font_file(harmony["bold"])
        if regular_path and bold_path:
            return {"regular": regular_path, "bold": bold_path, "resolved_name": target_name}

    return None


def get_css_font_stack(primary: str = "HarmonyHeiTi") -> str:
    """Return CSS font-family string. 强制使用鸿蒙黑体，忽略外部指定的 primary."""
    return "HarmonyHeiTi, sans-serif"
