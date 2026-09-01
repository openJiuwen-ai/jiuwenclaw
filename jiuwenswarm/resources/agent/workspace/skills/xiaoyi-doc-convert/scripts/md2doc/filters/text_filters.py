"""Text and formula filters for md2doc skill."""
import re

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    from tabulate import tabulate
    TABULATE_AVAILABLE = True
except ImportError:
    TABULATE_AVAILABLE = False

from filters.filter import Filter
from logger import get_logger
from utils.formula_utils import has_formula
from utils.html_utils import md2html
from utils.html2md import html_to_markdown
from utils.link2base64 import (
    has_image_link,
    has_image_base64,
    image_link2img_tag,
    image_base642img_tag
)

LOG = get_logger("TextFilters")


class FormulaProtectFilter(Filter):
    """Formula protect filter - protects formulas from processing."""

    # Default formula pattern
    FORMULA_PATTERN = r'(\$[^\$]{1,200}\$)'

    def __init__(self, filters: list = None):
        super().__init__(name="formula_protect_filter", description="公式保护")
        self.filters = filters or []

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        if not has_formula(content):
            return content
        # Simplified version - in full implementation would apply filters only to non-formula parts
        return content


class PdfFormulaProtectFilter(Filter):
    """PDF formula protect filter."""

    def __init__(self, filters: list = None):
        super().__init__(name="pdf_formula_protect_filter", description="PDF公式保护")
        self.filters = filters or []

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        if not has_formula(content):
            return content
        return content


class Md2HtmlFilter(Filter):
    """Markdown to HTML filter."""

    def __init__(self):
        super().__init__(name="md2html_filter", description="markdown文本转html文本")

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        return md2html(content, request_id, ['tables'], safe_mode=True)


class HtmlTableFilter(Filter):
    """HTML table filter - converts HTML tables to Markdown tables."""

    # Default HTML table pattern
    HTML_TABLE_PATTERN = r'<table[^>]*>.*?</table>'

    def __init__(self):
        super().__init__(name="html_table_filter", description="html表格转markdown表格")

    def _convert_html_table_to_markdown(self, html: str) -> str:
        """Convert HTML table to markdown table."""
        if not BS4_AVAILABLE or not TABULATE_AVAILABLE:
            LOG.warning("BeautifulSoup or tabulate not available, skipping table conversion")
            return html

        try:
            markup = html.replace("\n", "<br/>")
            soup = BeautifulSoup(markup, 'html.parser')
            table = soup.find('table')

            if table is None:
                return ''

            data = []
            for tr in table.find_all('tr'):
                row = [td.get_text(strip=True) for td in tr.find_all(['th', 'td'])]
                data.append(row)

            if len(data) > 1:
                headers = data[0]
                rows = data[1:]
                return tabulate(rows, headers=headers, tablefmt='pipe')
            else:
                return ''
        except Exception as e:
            LOG.warning(f"HTML table conversion failed: {str(e)}")
            return html

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        if not BS4_AVAILABLE or not TABULATE_AVAILABLE:
            return content

        try:
            return re.sub(self.HTML_TABLE_PATTERN,
                          lambda m: self._convert_html_table_to_markdown(m.group(0)),
                          content,
                          flags=re.DOTALL | re.IGNORECASE)
        except Exception as e:
            LOG.warning(f"{request_id}, html_table_filter failed: {str(e)}")
            return content


class HtmlCodeFilter(Filter):
    """HTML code filter - converts HTML blocks to Markdown."""

    HTML_CODE_PATTERN = r'(<html>.*?</html>)'

    def __init__(self):
        super().__init__(name="html_code_filter", description="html代码替换为markdown")

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        if "<html>" not in content:
            return content

        try:
            return re.sub(self.HTML_CODE_PATTERN,
                          lambda m: html_to_markdown(m.group(1)),
                          content,
                          flags=re.DOTALL)
        except Exception as e:
            LOG.warning(f"{request_id}, html_code_filter failed: {str(e)}")
            return content


class LocalPathFilter(Filter):
    """Local path filter - detects and removes local file path references."""

    # Patterns for detecting various path types
    REFERENCE_LINK_PATTERN = r'^\[[^\]]+\]:\s*(.*?)\s*$'
    HTML_LINK_PATTERN = r'<(?:source|a|img|link)[^>]*?(?:src|href)=\\?(["\']([^"\'>\s]+)["\']|([^"\'>\s]+))[^>]*>'
    MARKDOWN_LINK_PATTERN = r'\[[^\]]*\]\(([^)]*)\)'

    # File extensions that indicate local files
    FILE_EXTENSIONS = [
        "certs", ".py", ".cpp", ".java", ".c", ".js", ".html", ".pdf",
        ".docx", ".xlsx", ".json", ".xml", ".yaml", ".yml",
        ".log", ".lua", ".css", ".ini"
    ]

    ALLOWED_PROTOCOLS = ['https', 'superlink']

    def __init__(self):
        super().__init__(name="local_path_filter", description="检查是否引用本地文件")

    def _strip_quote_and_space(self, path: str) -> str:
        """Clean up path by removing quotes and extra spaces."""
        if not path:
            return ""
        if ' | ' in path:
            splits = path.rsplit(' | ', 1)
            path = splits[-1]
        path = path.strip(' "\'')
        return path.rstrip('/\\')

    def _is_local_reference(self, path: str) -> bool:
        """Check if path is a local file reference."""
        if not path:
            return False

        # Check for file:// protocol
        if path.startswith('file://'):
            return True

        # Check for allowed web protocols
        for protocol in self.ALLOWED_PROTOCOLS:
            if path.startswith(f'{protocol}://'):
                return False

        # Check for data URI
        if path.startswith('data:'):
            return False

        # Check for anchor links
        if path.startswith('#'):
            return False

        # Check for file extensions
        lower_path = path.lower()
        for ext in self.FILE_EXTENSIONS:
            if lower_path.endswith(ext):
                return True

        # Check for path separators
        if '/' in path or '\\' in path:
            return True

        # Check for relative paths
        if path.startswith('./') or path.startswith('../'):
            return True

        # Check for Windows absolute paths
        if re.match(r'^[a-zA-Z]:\\', path) or re.match(r'^\\\\', path):
            return True

        # Check for Unix absolute paths
        if path.startswith('/'):
            return True

        return False

    def _find_markdown_links(self, content: str) -> list:
        """Find markdown links in content."""
        refs = []
        for match in re.finditer(self.MARKDOWN_LINK_PATTERN, content):
            path = self._strip_quote_and_space(match.group(1))
            if self._is_local_reference(path):
                refs.append({'type': 'markdown_link', 'path': path})
        return refs

    def _find_html_links(self, content: str) -> list:
        """Find HTML links in content."""
        refs = []
        for match in re.finditer(self.HTML_LINK_PATTERN, content, re.IGNORECASE):
            path = self._strip_quote_and_space(match.group(1))
            if self._is_local_reference(path):
                refs.append({'type': 'html_link', 'path': path})
        return refs

    def _find_reference_links(self, content: str) -> list:
        """Find reference-style links in content."""
        refs = []
        for match in re.finditer(self.REFERENCE_LINK_PATTERN, content, re.MULTILINE):
            path = self._strip_quote_and_space(match.group(1))
            if self._is_local_reference(path):
                refs.append({'type': 'reference_link', 'path': path})
        return refs

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")

        # Find all local references
        local_refs = []
        local_refs.extend(self._find_markdown_links(content))
        local_refs.extend(self._find_html_links(content))
        local_refs.extend(self._find_reference_links(content))

        if local_refs:
            for ref in local_refs:
                LOG.warning(f"{request_id}, 引用文件 - 类型: {ref['type']}, 路径: {ref['path'][-30:]}")
            LOG.error(f"{request_id}, local path check failed")
            # In skill version, we remove local paths rather than raising exception
            # Remove file:// references
            content = re.sub(r'file://[^\s\)\]]+', '', content)

        return content


class ImageLink2ImgTagFilter(Filter):
    """Convert image links to HTML img tags."""

    def __init__(self):
        super().__init__(name="image_link2img_tag_filter", description="image link转html image标签")

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        if not has_image_link(content):
            return content
        return image_link2img_tag(content, request_id)


class ImgBase64ToImgTagFilter(Filter):
    """Convert base64 images to HTML img tags."""

    def __init__(self):
        super().__init__(name="img_base64_to_img_tag_filter", description="Base64图片转img标签")

    def apply(self, content: str, request_id: str = "") -> str:
        LOG.debug(f"{request_id}, apply content filter: {self.description}")
        if not has_image_base64(content):
            return content
        return image_base642img_tag(content, request_id)
