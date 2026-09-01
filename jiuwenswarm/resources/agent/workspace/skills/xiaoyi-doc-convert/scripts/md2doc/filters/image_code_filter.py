"""Image code filter for md2doc skill."""

from filters.filter import Filter
from utils.codes2mark import has_images_code, images_code2md
from logger import get_logger

LOG = get_logger("ImageCodeFilter")


class ImageCodeFilter(Filter):
    """Image code filter - converts images JSON code blocks to markdown."""

    def __init__(self, enable_size_extension: bool = True):
        super().__init__(name="image_code_filter", description="图片代码转换")
        self.enable_size_extension = enable_size_extension

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        if not has_images_code(content):
            return content
        return images_code2md(content, self.enable_size_extension)

