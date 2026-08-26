"""Pandoc tool for md2doc skill."""
from time import perf_counter
import pypandoc

from logger import get_logger

LOG = get_logger("PandocTool")


def create_docx(request_id: str, markdown_content: str, target_path: str, extra_args: list = None):
    """Convert markdown to docx using pypandoc."""
    if extra_args is None:
        extra_args = []

    try:
        t0 = perf_counter()
        pypandoc.convert_text(
            markdown_content,
            "docx",
            encoding='utf-8',
            format="markdown",
            outputfile=target_path,
            extra_args=extra_args,
            sandbox=False
        )
        LOG.debug(f"request_id={request_id}, md2docx cost {perf_counter() - t0: .2f}s.")
    except Exception as e:
        message = f"md2doc failed: {str(e)}"
        LOG.error(f"request_id=[{request_id}], message={message}")
        raise ValueError(message) from e
    return markdown_content
