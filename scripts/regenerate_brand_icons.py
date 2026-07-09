#!/usr/bin/env python3
"""Regenerate jiuwen_avatar.png (transparent) and logo.ico from the brand PNG."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC = PROJECT_ROOT / "jiuwenavatar" / "channels" / "web" / "frontend" / "public"
BRAND_PNG = PUBLIC / "jiuwen_avatar.png"
ICO_PATH = PUBLIC / "logo.ico"


def main() -> int:
    if not BRAND_PNG.is_file():
        print(f"ERROR: missing {BRAND_PNG}")
        return 1

    sys.path.insert(0, str(PROJECT_ROOT))
    from PIL import Image

    from jiuwenavatar.channels.desktop.brand_assets import prepare_brand_logo_rgba

    src = Image.open(BRAND_PNG)
    rgba = prepare_brand_logo_rgba(src)
    rgba.save(BRAND_PNG, format="PNG")
    print(f"Updated transparent PNG: {BRAND_PNG} ({rgba.size[0]}x{rgba.size[1]})")

    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    rgba.save(ICO_PATH, format="ICO", sizes=ico_sizes)
    print(f"Updated ICO: {ICO_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
