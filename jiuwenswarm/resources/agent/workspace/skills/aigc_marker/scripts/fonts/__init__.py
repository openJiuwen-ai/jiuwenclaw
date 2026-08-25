"""Font utilities for aigc_marker skill."""
import os
import platform

system = platform.system()
if system == "Windows":
    FONT_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts")
else:  # Linux
    FONT_DIR = '/usr/share/fonts/HarmonyFont'


def get_font_path(font_file: str = "Harmony-Regular.ttf"):
    """Return path to bundled HarmonyOS Sans SC Bold font, or None if not present."""
    path = os.path.join(FONT_DIR, font_file)
    if os.path.exists(path):
        return path
    return None
