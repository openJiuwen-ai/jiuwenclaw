import re

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

CJK_PDF_TO_WORD_NOTE = (
    "文档含中文，当前环境不支持生成中文 PDF，已自动改用 Word 格式输出。"
)
CJK_WATERMARK_BLOCKED_MESSAGE = "水印文本含中文，当前环境不支持在 PDF 中渲染中文水印，请使用纯英文水印文本。"



def contains_cjk(text: str) -> bool:
    return bool(text and _CJK_RE.search(text))


def collect_structured_content_text(content: dict) -> str:
    parts: list[str] = []
    for key in ("title", "subtitle"):
        value = content.get(key, "")
        if value:
            parts.append(str(value))
    for para in content.get("paragraphs", []):
        text = para if isinstance(para, str) else para.get("text", "")
        if text:
            parts.append(str(text))
    for table in content.get("tables", []):
        data = table if isinstance(table, list) else table.get("data", [])
        for row in data:
            for cell in row:
                parts.append(str(cell))
    for slide in content.get("slides", []):
        for key in ("title", "body"):
            value = slide.get(key, "")
            if value:
                parts.append(str(value))
    for sheet in content.get("sheets", []):
        sheet_name = sheet.get("sheet_name", "")
        if sheet_name:
            parts.append(str(sheet_name))
        for row in sheet.get("rows", []):
            for cell in row:
                parts.append(str(cell))
    for row in content.get("rows", []):
        for cell in row:
            parts.append(str(cell))
    return "\n".join(parts)


def collect_docx_text(doc) -> str:
    parts = [para.text for para in doc.paragraphs if para.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def collect_presentation_text(prs) -> str:
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    parts.append(text)
    return "\n".join(parts)
