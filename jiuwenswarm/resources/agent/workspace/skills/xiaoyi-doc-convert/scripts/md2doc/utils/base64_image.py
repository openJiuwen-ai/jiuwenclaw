"""Base64 image utilities for md2doc skill."""
import base64
import io
from io import BytesIO

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from logger import get_logger

LOG = get_logger("Base64Image")


def get_image_dimensions(base64_str: str) -> tuple[int, int]:
    """Get image dimensions from base64 string.

    Args:
        base64_str: Base64 encoded image data (with or without data URI prefix)

    Returns:
        Tuple of (width, height), returns (0, 0) on error
    """
    if not PIL_AVAILABLE:
        return 0, 0

    try:
        if 'base64,' in base64_str:
            base64_data = base64_str.split('base64,')[1]
        else:
            base64_data = base64_str

        image_data = base64.b64decode(base64_data)
        with Image.open(BytesIO(image_data)) as img:
            return img.size
    except Exception as e:
        LOG.error(f"Failed to retrieve image dimensions: {str(e)}")
        return 0, 0


def convert_to_jpg(base64_data: str, mime_type: str, quality: int = 75) -> tuple[str, str]:
    """Convert image to JPEG format.

    Args:
        base64_data: Base64 encoded image data
        mime_type: Original MIME type
        quality: JPEG quality (1-100)

    Returns:
        Tuple of (mime_type, base64_data)
    """
    if mime_type == 'image/jpeg' or not PIL_AVAILABLE:
        return mime_type, base64_data

    try:
        buffered = io.BytesIO()
        image_data = base64.b64decode(base64_data)
        image = Image.open(io.BytesIO(image_data))
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        image.save(buffered, format="JPEG", quality=quality)
        return 'image/jpeg', base64.b64encode(buffered.getvalue()).decode("utf-8")
    except Exception as e:
        LOG.error(f"Failed to convert to JPG: {str(e)}")
        return mime_type, base64_data
