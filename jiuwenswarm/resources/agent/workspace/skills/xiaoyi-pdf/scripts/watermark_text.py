import os
from typing import Optional
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from font_resolver import resolve_reportlab_fonts
from security import validate_safe_path


def has_cjk(text: str) -> bool:
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
        if "\u3040" <= ch <= "\u309f" or "\u30a0" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af":
            return True
    return False


def resolve_font_path(explicit_font: Optional[str], text: str = "") -> Optional[str]:
    if not has_cjk(text):
        if explicit_font:
            validate_safe_path(explicit_font, allowed_extensions={".ttf", ".otf"})
            if not os.path.isfile(explicit_font):
                raise FileNotFoundError(f"Font file not found: {explicit_font}")
            return explicit_font
        return None
    # CJK 文本强制使用 HarmonyHeiTi，忽略外部指定字体
    fonts = resolve_reportlab_fonts("HarmonyHeiTi")
    if fonts and fonts.get("regular") and os.path.isfile(fonts["regular"]):
        return fonts["regular"]
    raise RuntimeError(
        f"CJK font required for text '{text}' but HarmonyHeiTi not found on system.\n"
        f"Please install HarmonyHeiTi or specify one with --font."
    )


def hex_to_rgb(color_hex: str):
    original_color_hex = color_hex
    color_hex = color_hex.lstrip("#")
    if len(color_hex) not in (3, 6):
        raise ValueError(f"Invalid color hex: {original_color_hex}")
    if not all(c in "0123456789abcdefABCDEF" for c in color_hex):
        raise ValueError(f"Invalid color hex: {original_color_hex}")
    if len(color_hex) == 3:
        color_hex = "".join(c * 2 for c in color_hex)
    r = int(color_hex[0:2], 16) / 255.0
    g = int(color_hex[2:4], 16) / 255.0
    b = int(color_hex[4:6], 16) / 255.0
    return r, g, b


def create_text_watermark(
    output_path: str,
    text: str,
    font_size: int = 48,
    rotate: int = 45,
    opacity: float = 0.3,
    color: str = "#888888",
    font_path: Optional[str] = None,
):
    font_path = resolve_font_path(font_path, text)
    width, height = A4
    c = canvas.Canvas(output_path, pagesize=A4)
    c.saveState()
    r, g, b = hex_to_rgb(color)
    c.setFillColorRGB(r, g, b, alpha=opacity)
    c.translate(width / 2, height / 2)
    c.rotate(rotate)

    if font_path:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        font_name = f"CustomCJKFont_{id(c)}"
        pdfmetrics.registerFont(TTFont(font_name, font_path))
        c.setFont(font_name, font_size)
    else:
        c.setFont("Helvetica", font_size)

    text_width = c.stringWidth(text, c._fontname, font_size)
    c.drawString(-text_width / 2, 0, text)
    c.restoreState()
    c.showPage()
    c.save()
