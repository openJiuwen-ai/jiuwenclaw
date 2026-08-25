"""Image AIGC decorator."""
import os.path

from PIL import Image, ImageDraw, ImageFont
import piexif
import pillow_heif

from decorators.common import get_aigc_signature, is_aigc_complete, parse_aigc_json


class ImageAigcDecorator:
    """Image AIGC decorator - adds AIGC mark to image files (jpg, jpeg, png, webp, heic, heif)."""

    def __init__(self):
        self.name = "image_aigc_decorator"
        self.text = "AI生成"
        self.bg_alpha = 40
        self.text_alpha = 100
        self.padding_ratio = 0.25
        self.min_font_size = 10
        self.jpeg_quality = 95

    def decorate(self, file_path: str, content: str, add_visible_mark: bool = True):
        """Add AIGC mark to image file."""
        existing = self._get_aigc_data(file_path)
        if existing and is_aigc_complete(existing):
            ext = os.path.splitext(file_path)[1].lower()
            print(f"  [Image/{ext}] AIGC mark complete, skipping")
            return
        try:
            aigc_signature = get_aigc_signature(content)
            self._add_aigc_mark(file_path, aigc_signature, add_visible_mark)
            ext = os.path.splitext(file_path)[1].lower()
            print(f"  [Image/{ext}] AIGC mark added successfully")
        except Exception as e:
            ext = os.path.splitext(file_path)[1].lower()
            print(f"  [Image/{ext}] Warning: Failed to add AIGC mark: {str(e)}")

    def _get_aigc_data(self, file_path: str) -> dict | None:
        """Read and parse AIGC metadata from image EXIF or PNG info."""
        try:
            pillow_heif.register_heif_opener()
            with Image.open(file_path) as img:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in (".png",):
                    if "AIGC" in img.info:
                        return parse_aigc_json(img.info["AIGC"])
                    return None
                elif ext in (".jpg", ".jpeg", ".webp", ".heic", ".heif"):
                    exif_dict = piexif.load(img.info.get("exif", b""))
                    user_comment = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment, b"")
                    if not user_comment:
                        return None
                    # Strip ASCII prefix and null bytes if present
                    comment_str = user_comment.decode("utf-8", errors="ignore")
                    comment_str = comment_str.lstrip("ASCII").lstrip("\x00")
                    return parse_aigc_json(comment_str)
                else:
                    return None
        except Exception:
            return None

    def _add_aigc_mark(self, file_path: str, signature: str, add_visible_mark: bool = True):
        """Process image: add visible watermark and implicit metadata."""
        # Register HEIF opener (idempotent)
        pillow_heif.register_heif_opener()

        img = Image.open(file_path)

        if add_visible_mark:
            img = self._add_visible_mark(img)

        # Determine save parameters based on format
        ext = os.path.splitext(file_path)[1].lower()

        if ext in (".png",):
            pnginfo = self._write_png_text(signature)
            save_kwargs = {"pnginfo": pnginfo}
        elif ext in (".jpg", ".jpeg", ".webp", ".heic", ".heif"):
            exif_bytes = self._write_exif_user_comment(signature)
            save_kwargs = {"quality": self.jpeg_quality, "exif": exif_bytes}
            # Convert RGBA to RGB if needed for formats that don't support alpha
            if img.mode in ("RGBA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[3])  # Use alpha channel as mask
                img = background
        else:
            raise ValueError(f"Unsupported image format: {ext}")

        img.save(file_path, **save_kwargs)

    def _add_visible_mark(self, img: Image.Image) -> Image.Image:
        """Add 'AI生成' watermark at bottom-right with semi-transparent background."""
        width, height = img.size
        short_edge = min(width, height)
        target_height = int(short_edge * 0.05)
        font_size = max(target_height, self.min_font_size)

        # Load font and measure actual text height, adjusting downward if needed
        font = self._load_font(font_size)
        temp_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        temp_draw = ImageDraw.Draw(temp_img)
        bbox = temp_draw.textbbox((0, 0), self.text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        while text_height > target_height and font_size > self.min_font_size:
            font_size -= 1
            font = self._load_font(font_size)
            bbox = temp_draw.textbbox((0, 0), self.text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

        padding = int(font_size * self.padding_ratio)
        bg_width = text_width + padding * 2
        bg_height = text_height + padding * 2

        margin = min(int(font_size * 0.3), 10)
        x = width - bg_width - margin
        y = height - bg_height - margin
        if x < 0:
            x = 0
        if y < 0:
            y = 0

        # Convert image to RGBA if needed for overlay
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Sample background color near watermark area for adaptive coloring
        sample_box = (
            max(0, x - margin), max(0, y - margin),
            min(width, x + bg_width + margin), min(height, y + bg_height + margin)
        )
        sample_region = img.crop(sample_box)
        pixels = list(sample_region.getdata())
        # Extract RGB (ignore alpha)
        rgb_pixels = [(p[0], p[1], p[2]) for p in pixels if len(p) >= 3]
        if rgb_pixels:
            avg_r = sum(p[0] for p in rgb_pixels) // len(rgb_pixels)
            avg_g = sum(p[1] for p in rgb_pixels) // len(rgb_pixels)
            avg_b = sum(p[2] for p in rgb_pixels) // len(rgb_pixels)
            brightness = 0.299 * avg_r + 0.587 * avg_g + 0.114 * avg_b
        else:
            brightness = 128

        if brightness < 128:
            # Dark background: light gray text + dark semi-transparent background
            text_color = (220, 220, 220, self.text_alpha)
            bg_color = (0, 0, 0, self.bg_alpha)
        else:
            # Light background: dark gray text + light semi-transparent background
            text_color = (80, 80, 80, self.text_alpha)
            bg_color = (255, 255, 255, self.bg_alpha)

        # Create overlay layer
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Draw semi-transparent background bar
        draw.rectangle(
            [x, y, x + bg_width, y + bg_height],
            fill=bg_color
        )

        # Draw text
        text_x = x + padding
        text_y = y + padding - bbox[1]  # Adjust for bbox top offset
        draw.text((text_x, text_y), self.text, font=font, fill=text_color)

        # Composite overlay onto original image
        img = Image.alpha_composite(img, overlay)
        return img

    def _load_font(self, font_size: int):
        """Load bundled HarmonyOS font, or fallback to default."""
        try:
            from fonts import get_font_path
            font_path = get_font_path("")
            if font_path:
                return ImageFont.truetype(font_path, font_size)
        except Exception as e:
            print(f"  [Image] Warning: load font failed: {str(e)}")
        print("  [Image] Warning: No CJK font found, watermark may display incorrectly.")
        return ImageFont.load_default()

    def _wrap_aigc_signature(self, inner_signature: str) -> str:
        """Wrap inner AIGC signature with outer AIGC layer."""
        import json
        inner = json.loads(inner_signature)
        wrapped = {
            "AIGC": inner,
        }
        return json.dumps(wrapped, ensure_ascii=False)

    def _write_exif_user_comment(self, signature: str) -> bytes:
        """Generate EXIF bytes with UserComment (ASCII/UTF-8 prefixed)."""
        prefix = b'ASCII\x00\x00\x00'
        signature_wrap = self._wrap_aigc_signature(signature)
        comment_bytes = prefix + signature_wrap.encode('utf-8')
        exif_dict = {"Exif": {piexif.ExifIFD.UserComment: comment_bytes}}
        return piexif.dump(exif_dict)

    def _write_png_text(self, signature: str):
        """Generate PngInfo with tEXt chunk for AIGC metadata."""
        from PIL.PngImagePlugin import PngInfo
        pnginfo = PngInfo()
        pnginfo.add_text("AIGC", signature)
        return pnginfo
