# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.tools.multimodal_config import (
    _get_model_section,
    _parse_bool,
)


logger = logging.getLogger(__name__)

_VALID_POSITIONS = frozenset({
    "bottom_right",
    "bottom_left",
    "top_right",
    "top_left",
    "center",
})

_FONT_CANDIDATES_WINDOWS = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
_FONT_CANDIDATES_WINDOWS_CJK = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
_FONT_CANDIDATES_UNIX = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)
_FONT_CANDIDATES_UNIX_CJK = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)


def _text_needs_cjk_font(text: str) -> bool:
    """True when watermark text contains non-ASCII glyphs (e.g. 中文)."""
    return any(ord(ch) > 127 for ch in text)


@dataclass(frozen=True)
class PostWatermarkConfig:
    enabled: bool = True
    text: str = "AI Generated"
    position: str = "bottom_right"
    margin_x: int = 16
    margin_y: int = 16
    opacity: float = 0.55
    font_size: int = 0
    font_path: str = ""


def _clamp_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 10_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _clamp_float(value: Any, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def load_post_watermark_config(config_base: dict[str, Any] | None) -> PostWatermarkConfig:
    """Load ``models.image_gen.post_watermark`` from config with built-in defaults."""
    defaults = PostWatermarkConfig()
    section = _get_model_section(config_base or {}, "image_gen")
    raw = section.get("post_watermark")
    if not isinstance(raw, dict):
        return defaults

    position = str(raw.get("position") or defaults.position).strip().lower()
    if position not in _VALID_POSITIONS:
        position = defaults.position

    font_path = str(raw.get("font_path") or "").strip()
    text = str(raw.get("text") or defaults.text).strip() or defaults.text

    return PostWatermarkConfig(
        enabled=_parse_bool(raw.get("enabled"), default=defaults.enabled),
        text=text,
        position=position,
        margin_x=_clamp_int(raw.get("margin_x"), defaults.margin_x),
        margin_y=_clamp_int(raw.get("margin_y"), defaults.margin_y),
        opacity=_clamp_float(raw.get("opacity"), defaults.opacity),
        font_size=_clamp_int(raw.get("font_size"), defaults.font_size, maximum=512),
        font_path=font_path,
    )


def _load_truetype_font(path: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        # .ttc collections may need an explicit face index on some Pillow builds.
        return ImageFont.truetype(path, size=size, index=0)


def _resolve_font(config: PostWatermarkConfig, image_width: int):
    from PIL import ImageFont

    size = config.font_size or max(14, min(48, int(image_width * 0.035)))

    if config.font_path:
        path = Path(config.font_path)
        if path.is_file():
            return _load_truetype_font(str(path), size)

    if os.name == "nt":
        latin_candidates = _FONT_CANDIDATES_WINDOWS
        cjk_candidates = _FONT_CANDIDATES_WINDOWS_CJK
    else:
        latin_candidates = _FONT_CANDIDATES_UNIX
        cjk_candidates = _FONT_CANDIDATES_UNIX_CJK

    if _text_needs_cjk_font(config.text):
        candidates = (*cjk_candidates, *latin_candidates)
    else:
        candidates = latin_candidates

    for candidate in candidates:
        if Path(candidate).is_file():
            try:
                return _load_truetype_font(candidate, size)
            except OSError:
                logger.debug("[text_to_image] skip unreadable watermark font: %s", candidate)
                continue
    return ImageFont.load_default()


def _text_origin(
    image_size: tuple[int, int],
    text_size: tuple[int, int],
    config: PostWatermarkConfig,
) -> tuple[int, int]:
    width, height = image_size
    text_w, text_h = text_size
    mx, my = config.margin_x, config.margin_y

    if config.position == "bottom_left":
        return mx, height - text_h - my
    if config.position == "top_right":
        return width - text_w - mx, my
    if config.position == "top_left":
        return mx, my
    if config.position == "center":
        return (width - text_w) // 2, (height - text_h) // 2
    return width - text_w - mx, height - text_h - my


def apply_post_watermark_to_file(image_path: Path, config: PostWatermarkConfig) -> None:
    """Overlay semi-transparent watermark text onto a saved image file (in place)."""
    if not config.enabled:
        return

    path = Path(image_path)
    if not path.is_file():
        return

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("[text_to_image] Pillow not available; skip post watermark for %s", path)
        return

    try:
        with Image.open(path) as opened:
            base = opened.convert("RGBA")
    except Exception as exc:
        logger.warning("[text_to_image] cannot open image for watermark %s: %s", path, exc)
        return

    font = _resolve_font(config, base.size[0])
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bbox = draw.textbbox((0, 0), config.text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x, y = _text_origin(base.size, (text_w, text_h), config)
    alpha = int(_clamp_float(config.opacity, 0.55) * 255)
    shadow_alpha = int(alpha * 0.85)
    draw.text((x + 1, y + 1), config.text, font=font, fill=(0, 0, 0, shadow_alpha))
    draw.text((x, y), config.text, font=font, fill=(255, 255, 255, alpha))

    composed = Image.alpha_composite(base, overlay)
    save_kwargs: dict[str, Any] = {"format": "PNG"}
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        composed = composed.convert("RGB")
        save_kwargs["format"] = "JPEG"
        save_kwargs["quality"] = 95
    else:
        composed = composed.convert("RGBA")
    composed.save(path, **save_kwargs)
