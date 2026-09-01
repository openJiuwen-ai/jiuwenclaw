import os
import sys
import tempfile
import warnings
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from security import validate_safe_path

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".svg"}


def _prepare_watermark_image(image_path: str, target_width: float, opacity: float) -> str:
    validate_safe_path(image_path, allowed_extensions=SUPPORTED_IMAGE_EXTS)
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in SUPPORTED_IMAGE_EXTS:
        raise ValueError(
            f"Unsupported image format '{ext}'. Supported: {', '.join(sorted(SUPPORTED_IMAGE_EXTS))}"
        )
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    svg_tmp = None
    if ext == ".svg":
        try:
            import cairosvg
        except ImportError as e:
            raise RuntimeError(
                "SVG support requires cairosvg. Install with: pip install cairosvg"
            ) from e
        fd, svg_tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        cairosvg.svg2png(url=image_path, write_to=svg_tmp)
        path_to_open = svg_tmp
    else:
        path_to_open = image_path

    try:
        img = Image.open(path_to_open)
    except Exception as e:
        raise ValueError(f"Failed to open image {image_path}: {e}") from e

    img_width, img_height = img.size
    if img_width == 0 or img_height == 0:
        raise ValueError(f"Image has zero dimensions: {image_path}")

    if img.mode != "RGBA":
        img = img.convert("RGBA")

    scale = target_width / img_width
    new_height = int(img_height * scale)
    # Pillow 9+ uses Image.Resampling.LANCZOS, older versions use Image.LANCZOS or Image.ANTIALIAS
    resample_filter = getattr(Image, "Resampling", Image)
    if hasattr(resample_filter, "LANCZOS"):
        resample = resample_filter.LANCZOS
    elif hasattr(Image, "LANCZOS"):
        resample = Image.LANCZOS
    else:
        resample = Image.ANTIALIAS
    img = img.resize((int(target_width), new_height), resample)

    if opacity < 1.0:
        alpha = img.split()[-1]
        lut = [int(p * opacity) for p in range(256)]
        alpha = alpha.point(lut)
        img.putalpha(alpha)

    fd, processed_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(processed_path, format="PNG")
    except Exception:
        try:
            os.remove(processed_path)
        except OSError:
            pass
        raise
    finally:
        if svg_tmp and os.path.exists(svg_tmp):
            os.remove(svg_tmp)

    return processed_path


def create_image_watermark(output_path: str, image_path: str, opacity: float = 0.3, scale: float = 0.3):
    if not 0 <= opacity <= 1:
        warnings.warn(f"Opacity {opacity} clamped to [0, 1]", UserWarning)
        opacity = max(0.0, min(1.0, opacity))
    if not 0 < scale <= 1:
        warnings.warn(f"Scale {scale} clamped to (0, 1]", UserWarning)
        scale = max(0.01, min(1.0, scale))

    page_width, page_height = A4
    target_width = page_width * scale

    processed_path = _prepare_watermark_image(image_path, target_width, opacity)

    try:
        img = Image.open(processed_path)
        new_width, new_height = img.size
        x = (page_width - new_width) / 2
        y = (page_height - new_height) / 2

        output_dir = os.path.dirname(os.path.abspath(output_path))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        c = canvas.Canvas(output_path, pagesize=A4)
        c.drawImage(processed_path, x, y, width=new_width, height=new_height, mask="auto")
        c.showPage()
        c.save()
    finally:
        try:
            os.remove(processed_path)
        except OSError:
            pass
