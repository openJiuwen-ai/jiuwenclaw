"""HTML utilities for md2doc skill."""
try:
    import markdown2
    MARKDOWN2_AVAILABLE = True
except ImportError:
    MARKDOWN2_AVAILABLE = False

from logger import get_logger

LOG = get_logger("HtmlUtils")


def md2html(markdown_text: str, request_id: str = "", extras: list = None, safe_mode: bool = True) -> str:
    """Convert markdown to HTML.

    Args:
        markdown_text: Input markdown content
        request_id: Request ID for logging
        extras: List of markdown2 extras (default: ['tables'])
        safe_mode: Whether to enable safe mode

    Returns:
        HTML string
    """
    if not MARKDOWN2_AVAILABLE:
        LOG.warning(f"{request_id}, markdown2 not available, returning original content")
        return markdown_text

    if extras is None:
        extras = ['tables']

    try:
        return markdown2.markdown(markdown_text, extras=extras, safe_mode=safe_mode)
    except Exception as e:
        LOG.error(f"{request_id}, md2html failed: {str(e)}")
        return markdown_text

