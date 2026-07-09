# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Brand asset paths for desktop (tray, floating widget, etc.)."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("jiuwenavatar.channels.desktop.brand_assets")

DEFAULT_BRAND_LOGO = "jiuwen_avatar.png"

# persona_id → public/*.png stem (without .png)
PERSONA_LOGO_ALIASES: dict[str, str] = {
    "committer": "committer",
    "tester": "tester",
    "jiuwen-project-qa": "qa",
    "developer": "developer",
    "programmer": "programmer",
    "project-manager": "programmer",
    "one-person-company": "company",
    "se": "se",
    "jiuwen-community-ops": "jiuwen_avatar",
}


def _channel_roots() -> list[Path]:
    """Return candidate base dirs under jiuwenavatar/channels."""
    roots: list[Path] = []

    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        roots.append(meipass / "jiuwenavatar" / "channels")

    module_root = Path(__file__).resolve().parent.parent
    if module_root not in roots:
        roots.append(module_root)

    return roots


def find_brand_asset(filename: str) -> Path | None:
    """Find a named asset under web/frontend/public or dist."""
    for root in _channel_roots():
        for folder in ("public", "dist"):
            path = root / "web" / "frontend" / folder / filename
            if path.is_file():
                logger.debug("Found brand asset: %s", path)
                return path
    return None


def iter_logo_paths() -> list[Path]:
    """Ordered logo file candidates (jiuwen_avatar preferred, then legacy names)."""
    names = (DEFAULT_BRAND_LOGO, "jiuwen-avatar.png", "logo.png", "logo.ico")
    paths: list[Path] = []

    for root in _channel_roots():
        for folder in ("public", "dist"):
            for name in names:
                paths.append(root / "web" / "frontend" / folder / name)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        key = path.resolve() if path.exists() else path
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def find_logo_path() -> Path | None:
    """Find the default application logo (jiuwen_avatar.png)."""
    for path in iter_logo_paths():
        if path.is_file():
            logger.info("Found brand logo: %s", path)
            return path
    logger.warning("Brand logo not found. Checked: %s", iter_logo_paths())
    return None


def resolve_persona_logo_path(persona_id: str, icon: str | None = None) -> Path | None:
    """Map persona id / icon key to a role-specific PNG in public/."""
    candidates: list[str] = []
    if icon:
        candidates.append(f"{icon}.png")
    alias = PERSONA_LOGO_ALIASES.get(persona_id or "")
    if alias:
        candidates.append(f"{alias}.png")
    if persona_id:
        candidates.append(f"{persona_id}.png")

    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        path = find_brand_asset(name)
        if path is not None:
            logger.info("Resolved persona logo persona_id=%s icon=%s -> %s", persona_id, icon, path)
            return path
    return find_logo_path()


def _decode_data_image(icon: str) -> Any | None:
    """Decode a data:image URL stored on a custom Persona."""
    if not icon.startswith("data:image/"):
        return None
    try:
        header, payload = icon.split(",", 1)
        if ";base64" not in header:
            return None
        Image, _, _ = _require_pil()
        raw = base64.b64decode(payload, validate=True)
        img = Image.open(io.BytesIO(raw))
        img.load()
        return img.convert("RGBA")
    except Exception as exc:
        logger.warning("Failed to decode custom persona icon: %s", exc)
        return None


def resolve_avatar_logo_source(avatar: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve floating-widget artwork for an avatar.

    Built-in personas still map to packaged PNG assets. Custom personas may store
    uploaded icons as data:image URLs; those are decoded directly so the desktop
    buoy exactly matches the template/card icon.
    """
    persona_id = str(avatar.get("persona_id") or "").strip()
    icon: str | None = None
    if persona_id:
        try:
            from jiuwenavatar.server.runtime.persona.manager import PersonaManager

            mgr = PersonaManager.get_instance()
            mgr.ensure_loaded()
            persona = mgr.get_persona(persona_id)
            if persona:
                icon = str(persona.get("icon") or "") or None
        except Exception as exc:
            logger.debug("resolve_avatar_logo_source persona lookup failed: %s", exc)

    if icon:
        data_img = _decode_data_image(icon)
        if data_img is not None:
            digest = hashlib.sha256(icon.encode("utf-8", errors="ignore")).hexdigest()[:16]
            return {
                "image": data_img,
                "path": None,
                "identity": f"data:{persona_id}:{digest}",
                "label": f"custom-icon:{persona_id}:{digest}",
                "suppress_role_label": True,
            }

    path = resolve_persona_logo_path(persona_id, icon)
    if path is None:
        return None
    try:
        stat = path.stat()
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        stamp = ""
    return {
        "image": None,
        "path": path,
        "identity": f"path:{path}:{stamp}",
        "label": str(path),
        "suppress_role_label": False,
    }


def resolve_avatar_logo_path(avatar: dict[str, Any]) -> Path | None:
    """Resolve floating-widget logo for an avatar instance."""
    source = resolve_avatar_logo_source(avatar)
    return source.get("path") if source else None


# Short labels shown under the circular floating icon (readable, not from PNG crop).
PERSONA_FLOAT_LABELS: dict[str, str] = {
    "committer": "Committer",
    "tester": "测试",
    "jiuwen-project-qa": "问题接口",
    "developer": "开发",
    "programmer": "程序员",
    "project-manager": "项目经理",
    "one-person-company": "一人公司",
    "se": "SE",
    "jiuwen-community-ops": "社区运营",
}


def resolve_avatar_role_label(avatar: dict[str, Any]) -> str:
    """Readable caption under the floating buoy (independent of PNG artwork)."""
    persona_id = str(avatar.get("persona_id") or "").strip()
    if persona_id in PERSONA_FLOAT_LABELS:
        return PERSONA_FLOAT_LABELS[persona_id]

    if persona_id:
        try:
            from jiuwenavatar.server.runtime.persona.manager import PersonaManager

            mgr = PersonaManager.get_instance()
            mgr.ensure_loaded()
            persona = mgr.get_persona(persona_id)
            if persona:
                display = str(persona.get("display_name") or "").strip()
                if display:
                    # Drop trailing "分身" for compact label
                    if display.endswith("分身"):
                        display = display[:-2].strip()
                    return display[:8]
        except Exception as exc:
            logger.debug("resolve_avatar_role_label failed: %s", exc)

    name = str(avatar.get("name") or "").strip()
    return (name[:8] if name else "分身")


# ---------------------------------------------------------------------------
# Image preparation (preserve aspect ratio — no squashing wide logos)
# ---------------------------------------------------------------------------


def _require_pil():
    from PIL import Image, ImageChops, ImageDraw

    return Image, ImageChops, ImageDraw


def trim_near_white_borders(img: Any) -> Any:
    """Remove excess white margins from brand PNG exports."""
    Image, ImageChops, _ = _require_pil()
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    bbox = ImageChops.difference(rgba, bg).getbbox()
    if bbox:
        return rgba.crop(bbox)
    return rgba


def remove_near_white_background(img: Any, *, threshold: int = 235) -> Any:
    """Make near-white matte pixels transparent (desktop/tray ICO sources)."""
    Image, _, _ = _require_pil()
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a and r >= threshold and g >= threshold and b >= threshold:
                pixels[x, y] = (r, g, b, 0)
    return rgba


def prepare_brand_logo_rgba(img: Any) -> Any:
    """Trim margins and remove white matte for brand PNG / ICO."""
    return remove_near_white_background(trim_near_white_borders(img))


def fit_image_contain(img: Any, size: int) -> Any:
    """Scale image to fit inside size×size, preserving aspect ratio."""
    Image, _, _ = _require_pil()
    rgba = img.convert("RGBA")
    width, height = rgba.size
    if width <= 0 or height <= 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scale = min(size / width, size / height)
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    resized = rgba.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - new_w) // 2, (size - new_h) // 2), resized)
    return canvas


def fit_image_cover_square(img: Any, size: int, *, fill: float = 1.0) -> Any:
    """Scale image to cover a size×size square (center crop), for tray icons."""
    Image, _, _ = _require_pil()
    rgba = img.convert("RGBA")
    width, height = rgba.size
    if width <= 0 or height <= 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scale = max(size / width, size / height) * fill
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    resized = rgba.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - size) // 2)
    top = max(0, (new_h - size) // 2)
    return resized.crop((left, top, left + size, top + size))


def apply_circular_mask(img: Any, size: int, *, ring: bool = True) -> Any:
    """Clip a size×size image to a circle (optional white ring)."""
    Image, _, ImageDraw = _require_pil()
    if img.size != (size, size):
        img = fit_image_contain(img, size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    circular = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    circular.paste(img, (0, 0), mask)
    if ring:
        ring_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(ring_layer).ellipse(
            (1, 1, size - 2, size - 2),
            outline=(255, 255, 255, 230),
            width=max(2, size // 28),
        )
        circular = Image.alpha_composite(circular, ring_layer)
    return circular


def _crop_portrait_card_square(img: Any) -> Any:
    """Card-style persona PNG: top square (avatar), drop bottom caption."""
    rgba = img.convert("RGBA")
    width, height = rgba.size
    if height > width * 1.02:
        return rgba.crop((0, 0, width, width))
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return rgba.crop((left, top, left + side, top + side))


def _load_ui_font(size: int) -> Any:
    from PIL import ImageFont

    for name in ("msyhbd.ttc", "msyh.ttc", "Microsoft YaHei UI Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_floating_role_label(img: Any, text: str, size: int) -> Any:
    """Draw role caption inside the circle bottom — transparent bg, white text + stroke."""
    _, _, ImageDraw = _require_pil()
    if not text:
        return img

    result = img.copy()
    draw = ImageDraw.Draw(result)
    font_size = max(10, size // 7)
    font = _load_ui_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = size - th - max(6, size // 14) - bbox[1]

    stroke = max(1, size // 44)
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=(15, 23, 42, 210))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    return result


def prepare_floating_icon(
    img: Any,
    size: int,
    *,
    logo_path: Path | None = None,
    role_label: str = "",
) -> Any:
    """Prepare buoy artwork without squashing non-square source files."""
    Image, _, _ = _require_pil()
    width, height = img.size
    filename = logo_path.name.lower() if logo_path else ""
    is_brand_logo = filename in {DEFAULT_BRAND_LOGO, "jiuwen-avatar.png", "logo.png"}
    is_landscape = width > height * 1.08
    is_portrait_card = height > width * 1.02 and not is_brand_logo

    if is_brand_logo or is_landscape:
        fitted = fit_image_contain(prepare_brand_logo_rgba(img), size)
        circular = apply_circular_mask(fitted, size)
    elif is_portrait_card:
        square = _crop_portrait_card_square(img)
        square = square.resize((size, size), Image.Resampling.LANCZOS)
        circular = apply_circular_mask(square, size)
    else:
        fitted = fit_image_contain(trim_near_white_borders(img), size)
        circular = apply_circular_mask(fitted, size)

    if role_label:
        circular = _draw_floating_role_label(circular, role_label, size)
    return circular


def prepare_tray_icon_image(img: Any, size: int = 64) -> Any:
    """Prepare system-tray icon — cover-fill so logo matches peer icon size."""
    return fit_image_cover_square(prepare_brand_logo_rgba(img), size, fill=0.96)
