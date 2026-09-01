"""Codes to markdown conversion utilities for md2doc skill."""
import json
import re

from config import CONFIG
from logger import get_logger

LOG = get_logger("Codes2Mark")

# Default configuration values
md2doc_config = CONFIG.md2doc_config
DEFAULT_IMAGES_CODE_PATTERN = md2doc_config.get('md2doc_images_code_pattern', r"```images\n*({.*?})\n*```")
DEFAULT_IMAGE_MAX_WIDTH = md2doc_config.get('md2doc_image_max_width', 600)
DEFAULT_IMAGE_SCALE_THRESHOLD = md2doc_config.get('md2doc_image_scale_threshold', 360)


def image_code2md(data: dict, markdown_images: list, enable_size_extension: bool = True) -> None:
    """Convert single image code data to markdown image syntax.

    Args:
        data: Image data dict with keys like 'image', 'thumb', 'url', 'width', 'height'
        markdown_images: List to append markdown image strings to
        enable_size_extension: Whether to add size extension to markdown
    """
    url = data.get('image', '') or data.get('thumb', '') or data.get('url', '')

    if not url:
        LOG.warning("No valid image url")
        return

    image_mark = f'![]({url})'

    if enable_size_extension and "width" in data and "height" in data:
        width, height = data['width'], data['height']
        try:
            w, h = int(float(width)), int(float(height))
            if w > DEFAULT_IMAGE_SCALE_THRESHOLD:
                h = int(round(h * DEFAULT_IMAGE_MAX_WIDTH / w, 0))
                w = DEFAULT_IMAGE_MAX_WIDTH
            if w > 0 and h > 0:
                image_mark = f'{image_mark}{{width={w}px height={h}px}}'
        except (ValueError, TypeError):
            pass

    markdown_images.append(image_mark)


def replace_json_with_images(images_json: str, enable_size_extension: bool = True) -> str:
    """Parse images JSON and convert to markdown image syntax.

    Args:
        images_json: JSON string containing image data
        enable_size_extension: Whether to add size extension

    Returns:
        Markdown image strings joined by newlines
    """
    try:
        data = json.loads(images_json)
        markdown_images = []
        for item in data.get('data', []):
            if not item.get('type') == 'image':
                continue
            image_code2md(item, markdown_images, enable_size_extension)
        return '\n\n'.join(markdown_images)
    except json.JSONDecodeError as e:
        LOG.warning(f"Invalid images code JSON: {str(e)}")
        return ''
    except Exception as e:
        LOG.warning(f"Error processing images code: {str(e)}")
        return ''


def images_code2md(markdown_content: str, enable_size_extension: bool = True) -> str:
    """Convert images JSON code blocks to standard Markdown image syntax.

    Args:
        markdown_content: Original markdown content
        enable_size_extension: Whether to add size extension to images

    Returns:
        Converted markdown content
    """
    try:
        return re.sub(DEFAULT_IMAGES_CODE_PATTERN,
                      lambda m: replace_json_with_images(m.group(1), enable_size_extension),
                      markdown_content,
                      flags=re.DOTALL)
    except Exception as e:
        LOG.warning(f"images code to markdown failed: {str(e)}")
        return markdown_content


def has_images_code(content: str, pattern: str = None) -> bool:
    """Check if content contains images code blocks.

    Args:
        content: Content to check
        pattern: Optional custom regex pattern

    Returns:
        True if images code block found
    """
    if pattern is None:
        pattern = DEFAULT_IMAGES_CODE_PATTERN
    match = re.search(pattern, content)
    return match is not None
