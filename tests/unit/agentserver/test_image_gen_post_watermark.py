from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenclaw.agentserver.tools.image_gen_post_watermark import (
    PostWatermarkConfig,
    _text_needs_cjk_font,
    apply_post_watermark_to_file,
    load_post_watermark_config,
)


def test_load_post_watermark_config_defaults() -> None:
    cfg = load_post_watermark_config({})
    assert cfg.enabled is True
    assert cfg.text == "AI Generated"
    assert cfg.position == "bottom_right"
    assert cfg.margin_x == 16
    assert cfg.margin_y == 16


def test_load_post_watermark_config_from_yaml_block() -> None:
    cfg = load_post_watermark_config(
        {
            "models": {
                "image_gen": {
                    "post_watermark": {
                        "enabled": False,
                        "text": "Test Mark",
                        "position": "top_left",
                        "margin_x": 8,
                        "opacity": 0.4,
                    }
                }
            }
        }
    )
    assert cfg.enabled is False
    assert cfg.text == "Test Mark"
    assert cfg.position == "top_left"
    assert cfg.margin_x == 8
    assert cfg.opacity == 0.4


def test_apply_post_watermark_bottom_right(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (200, 120), color=(30, 80, 160)).save(image_path)

    before = image_path.read_bytes()
    apply_post_watermark_to_file(
        image_path,
        PostWatermarkConfig(enabled=True, text="AI Generated", position="bottom_right"),
    )
    after = image_path.read_bytes()
    assert after != before
    assert image_path.stat().st_size > 0


def test_text_needs_cjk_font() -> None:
    assert _text_needs_cjk_font("AI生成") is True
    assert _text_needs_cjk_font("AI Generated") is False


def test_apply_post_watermark_cjk_text(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    cjk_font = Path("C:/Windows/Fonts/msyh.ttc")
    if not cjk_font.is_file():
        pytest.skip("CJK system font not available")

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (200, 120), color=(30, 80, 160)).save(image_path)
    before = image_path.read_bytes()
    apply_post_watermark_to_file(
        image_path,
        PostWatermarkConfig(
            enabled=True,
            text="AI生成",
            position="bottom_right",
            font_path=str(cjk_font),
        ),
    )
    assert image_path.read_bytes() != before


def test_apply_post_watermark_disabled_is_noop(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (64, 64), color=(255, 0, 0)).save(image_path)
    before = image_path.read_bytes()
    apply_post_watermark_to_file(image_path, PostWatermarkConfig(enabled=False))
    assert image_path.read_bytes() == before
