"""Link to base64/image tag conversion utilities for md2doc skill."""
import base64
import re

from logger import get_logger
from utils.base64_image import get_image_dimensions
from utils.mime_type import get_mime_type_from_url

LOG = get_logger("Link2Base64")

# Default patterns (should be configured via config)
IMAGE_LINK_PATTERN = r'!\[[^\]]*\]\(([^)]+)\)\{[^}]*\}'
IMAGE_BASE64_PATTERN = r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+'


def replace_image_link_with_img_tag(image_url: str, width: str, height: str, request_id: str = "") -> str:
    """Replace image link with HTML img tag.

    This is a simplified version that returns a placeholder.
    The skill version doesn't fetch external images.
    """
    try:
        mime_type = get_mime_type_from_url(image_url)
        w = int(float(width)) if width else 0
        h = int(float(height)) if height else 0

        if w > 0 and h > 0:
            return f'<img src="{image_url}" style="width:{w}px; height:{h}px;" />'
        else:
            return f'<img src="{image_url}"/>'
    except Exception as e:
        LOG.warning(f"{request_id}, replace image link with img tag failed: {str(e)}")
        return ''


def image_link2img_tag(markdown_content: str, request_id: str = "") -> str:
    """Convert markdown image links to HTML img tags.

    This is a simplified version that preserves the URLs but converts to img tags.
    """
    try:
        pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        return re.sub(pattern,
                      lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}"/>',
                      markdown_content,
                      flags=re.DOTALL)
    except Exception as e:
        LOG.warning(f"{request_id}, image_link2img_tag failed: {str(e)}")
        return markdown_content


def replace_image_base64_with_img_tag(image_base64: str, width: str, height: str, request_id: str = "") -> str:
    """Replace base64 image with HTML img tag with dimensions.

    Args:
        image_base64: Base64 encoded image (with data URI prefix)
        width: Desired width
        height: Desired height
        request_id: Request ID for logging

    Returns:
        HTML img tag string
    """
    try:
        w = int(width) if width else 0
        h = int(height) if height else 0

        if w == 0 or h == 0:
            w, h = get_image_dimensions(image_base64)

        if w > 0 and h > 0:
            return f'<img src="{image_base64}" style="width:{w}px; height:{h}px;" />'
        else:
            return f'<img src="{image_base64}"/>'
    except Exception as e:
        LOG.warning(f"{request_id}, replace image base64 with img tag failed: {str(e)}")
        return ''


def image_base642img_tag(markdown_content: str, request_id: str = "") -> str:
    """Convert base64 images in markdown to HTML img tags.

    Args:
        markdown_content: Markdown content
        request_id: Request ID for logging

    Returns:
        Modified content with img tags
    """
    try:
        # Match markdown image syntax with base64 data
        pattern = r'!\[([^\]]*)\]\((data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\)'
        return re.sub(pattern,
                      lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}"/>',
                      markdown_content,
                      flags=re.DOTALL)
    except Exception as e:
        LOG.warning(f"{request_id}, image_base642img_tag failed: {str(e)}")
        return markdown_content


def has_image_link(content: str) -> bool:
    """Check if content contains image links."""
    pattern = r'!\[[^\]]*\]\([^)]+\)'
    match = re.search(pattern, content)
    return match is not None


def has_image_base64(content: str) -> bool:
    """Check if content contains base64 images."""
    match = re.search(IMAGE_BASE64_PATTERN, content)
    return match is not None
