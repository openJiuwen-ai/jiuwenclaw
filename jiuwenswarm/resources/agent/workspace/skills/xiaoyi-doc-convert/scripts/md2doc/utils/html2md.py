"""HTML to Markdown conversion utility."""
try:
    import html2text
    HTML2TEXT_AVAILABLE = True
except ImportError:
    HTML2TEXT_AVAILABLE = False


def html_to_markdown(html_content: str) -> str:
    """Convert HTML content to Markdown.

    Args:
        html_content: Input HTML content

    Returns:
        Markdown formatted string
    """
    if not HTML2TEXT_AVAILABLE:
        return html_content

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0
    return h.handle(html_content)

