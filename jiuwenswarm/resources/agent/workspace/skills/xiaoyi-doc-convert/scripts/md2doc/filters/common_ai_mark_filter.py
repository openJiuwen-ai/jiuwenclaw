"""Common AI mark filter for md2doc skill."""
from filters.filter import Filter
from logger import get_logger

LOG = get_logger("CommonAiMarkFilter")


class CommonAiMarkFilter(Filter):
    """AI mark filter for text format."""

    def __init__(self, ai_mark_content: str = "内容由AI生成"):
        super().__init__(name="common_ai_mark_filter", description="添加显示AI标记")
        self.ai_mark_content = ai_mark_content

    def apply(self, content: str, request_id: str = "") -> str:
        """Apply filter to add AI mark."""
        if not self.ai_mark_content:
            return content
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        return f"{content}\n\n**{self.ai_mark_content}**\n\n"


class MdImplicitAiMarkFilter(Filter):
    """Implicit AI mark filter for markdown."""

    def __init__(self, signature: str = ""):
        super().__init__(name="md_implicit_ai_mark_filter", description="添加隐式AI标记")
        self.signature = signature

    def apply(self, content: str, request_id: str = "") -> str:
        if not self.signature:
            return content
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        return f"{content}\n\n<!-- AIGC {self.signature} -->\n\n"
