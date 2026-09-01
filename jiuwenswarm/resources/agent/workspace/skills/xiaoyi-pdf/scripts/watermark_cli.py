import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from security import validate_safe_path
from watermark_merge import merge_watermark
from watermark_text import create_text_watermark
from watermark_image import create_image_watermark


def build_default_output(input_path: str) -> str:
    base, ext = os.path.splitext(input_path)
    return f"{base}_watermarked{ext}"


def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Add watermark to a PDF document.")
    parser.add_argument("--input", required=True, help="Input PDF path")
    parser.add_argument("--output", help="Output PDF path (default: *_watermarked.pdf)")
    parser.add_argument(
        "--type", required=True, choices=["text", "image", "pdf"], help="Watermark type"
    )
    parser.add_argument("--text", help="Watermark text (required for text type)")
    parser.add_argument(
        "--font-size", type=int, default=48, help="Font size for text watermark"
    )
    parser.add_argument(
        "--rotate", type=int, default=45, help="Rotation angle for text watermark"
    )
    parser.add_argument(
        "--opacity", type=float, default=0.3, help="Opacity for watermark (0-1)"
    )
    parser.add_argument(
        "--color", default="#888888", help="Color for text watermark (hex)"
    )
    parser.add_argument(
        "--image", help="Image or PDF watermark file path (required for image/pdf type)"
    )
    parser.add_argument("--pages", help="Page ranges to apply watermark, e.g. 1,3,5-10")
    parser.add_argument("--font", help="Explicit font file path for text watermark")
    parser.add_argument(
        "--scale",
        type=float,
        default=0.3,
        help="Scale for image watermark as ratio of page width (0-1]",
    )
    return parser.parse_args(args)


def main(args=None) -> int:
    try:
        parsed = parse_args(args)

        validate_safe_path(parsed.input, allowed_extensions={".pdf"})

        if not os.path.exists(parsed.input):
            print(f"Error: Input file not found: {parsed.input}", file=sys.stderr)
            return 1

        output = parsed.output or build_default_output(parsed.input)
        out_dir = os.path.dirname(os.path.abspath(output))
        if out_dir and not os.path.isdir(out_dir):
            print(f"Error: Output directory does not exist: {out_dir}", file=sys.stderr)
            return 1

        watermark_pdf_path = None
        if parsed.type == "text":
            if not parsed.text:
                print("Error: --text is required when --type is text", file=sys.stderr)
                return 1
            if parsed.font:
                validate_safe_path(parsed.font, allowed_extensions={".ttf", ".otf"})
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                watermark_pdf_path = tmp.name
            create_text_watermark(
                watermark_pdf_path,
                parsed.text,
                font_size=parsed.font_size,
                rotate=parsed.rotate,
                opacity=parsed.opacity,
                color=parsed.color,
                font_path=parsed.font,
            )
        elif parsed.type == "image":
            if not parsed.image:
                print(
                    "Error: --image is required when --type is image", file=sys.stderr
                )
                return 1
            validate_safe_path(
                parsed.image,
                allowed_extensions={".png", ".jpg", ".jpeg", ".bmp", ".webp", ".svg"},
            )
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                watermark_pdf_path = tmp.name
            create_image_watermark(
                watermark_pdf_path,
                parsed.image,
                opacity=parsed.opacity,
                scale=parsed.scale,
            )
        elif parsed.type == "pdf":
            if not parsed.image:
                print("Error: --image is required when --type is pdf", file=sys.stderr)
                return 1
            validate_safe_path(parsed.image, allowed_extensions={".pdf"})
            watermark_pdf_path = parsed.image
        else:
            print(f"Error: Unknown watermark type: {parsed.type}", file=sys.stderr)
            return 1

        merge_watermark(parsed.input, watermark_pdf_path, output, pages=parsed.pages)
        print(f"Watermarked PDF saved to: {output}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            if watermark_pdf_path and parsed.type in ("text", "image"):
                try:
                    os.remove(watermark_pdf_path)
                except OSError:
                    pass
        except NameError:
            pass


if __name__ == "__main__":
    sys.exit(main())
