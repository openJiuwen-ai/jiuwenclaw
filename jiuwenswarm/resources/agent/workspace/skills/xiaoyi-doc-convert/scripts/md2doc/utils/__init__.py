"""Utils package for md2doc skill."""
from logger import get_logger
from utils.aigc_mark import get_aigc_signature
from utils.codes2mark import has_images_code, images_code2md
from utils.formula_utils import has_formula, protect_formulas
from utils.html2md import html_to_markdown
from utils.html_utils import md2html
from utils.link2base64 import (
    has_image_link,
    has_image_base64,
    image_link2img_tag,
    image_base642img_tag
)

__all__ = [
    'get_logger',
    'get_aigc_signature',
    'has_formula',
    'protect_formulas',
    'md2html',
    'html_to_markdown',
    'has_image_link',
    'has_image_base64',
    'image_link2img_tag',
    'image_base642img_tag',
    'has_images_code',
    'images_code2md'
]