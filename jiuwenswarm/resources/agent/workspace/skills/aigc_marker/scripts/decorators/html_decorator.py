"""HTML AIGC decorator."""
import os
import re
import stat


AI_MARK_TEXT = "内容由AI生成"


class HtmlAigcDecorator:
    """HTML AIGC decorator - adds AIGC mark to HTML files."""

    def __init__(self):
        self.name = "html_aigc_decorator"

    def decorate(self, file_path: str, content: str, add_visible_mark: bool = True):
        """Add AIGC mark to HTML file."""
        if not add_visible_mark:
            print("  [HTML] skip_visible is set; no implicit metadata to add, skipping")
            return
        try:
            self._add_aigc_mark(file_path)
            print(f"  [HTML] AIGC mark added successfully")
        except Exception as e:
            print(f"  [HTML] Warning: Failed to add AIGC mark: {str(e)}")

    def _add_aigc_mark(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        visible_mark = (
            '<div style="position:fixed;bottom:8px;left:0;width:100%;'
            'text-align:center;z-index:99999;pointer-events:none;">'
            '<span style="display:inline-block;background:rgba(255,255,255,0.9);'
            'padding:4px 12px;border-radius:4px;font-size:12px;color:#888;">'
            f'{AI_MARK_TEXT}</span></div>'
        )

        # Duplicate detection: check by text content only
        if AI_MARK_TEXT in html_content:
            print(f"  [HTML] AIGC mark already present, skipping")
            return

        # Find last </body> (case-insensitive)
        matches = list(re.finditer(r"(?i)</body>", html_content))
        if matches:
            insert_pos = matches[-1].start()
            new_content = html_content[:insert_pos] + visible_mark + html_content[insert_pos:]
        else:
            new_content = html_content + visible_mark

        file_flags = os.O_WRONLY | os.O_CREAT
        file_mode = stat.S_IWUSR | stat.S_IRUSR
        with os.fdopen(os.open(file_path, file_flags, file_mode), "w", encoding="utf-8") as f:
            f.write(new_content)
