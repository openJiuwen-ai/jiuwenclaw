import re

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

CJK_PDF_TO_WORD_NOTE = (
    "文档含中文，当前环境不支持生成中文 PDF，已自动改用 Word 格式输出。"
)
CJK_PDF_BLOCKED_MESSAGE = (
    "文档含中文，当前环境不支持生成中文 PDF，请保留 Word 格式或"
    "使用 document_generator 直接生成 Word。"
)
CJK_WATERMARK_BLOCKED_MESSAGE = "水印文本含中文，当前环境不支持在 PDF 中渲染中文水印，请使用纯英文水印文本。"


def contains_cjk(text: str) -> bool:
    return bool(text and _CJK_RE.search(text))


def _item_text(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text", "") or "")
    return str(item) if item is not None else ""


def _join_text_items(value) -> str:
    items = value if isinstance(value, list) else [value]
    return "\n".join(text for text in (_item_text(item) for item in items) if text)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return _join_text_items(value)
    return str(value).strip()


def _coerce_table(table) -> list:
    if isinstance(table, list):
        return table
    if not isinstance(table, dict):
        return []
    data = table.get("data")
    if isinstance(data, list) and data:
        return data
    headers = table.get("headers") or table.get("columns") or []
    rows = table.get("rows") or []
    if not headers and not rows:
        return []
    grid = []
    if headers:
        grid.append([str(cell) for cell in headers])
    for row in rows:
        grid.append([str(cell) for cell in row] if isinstance(row, list) else [str(row)])
    return grid


def _coerce_tables(tables: list) -> list:
    return [coerced for coerced in (_coerce_table(table) for table in tables) if coerced]


def _merge_slide_body(slide: dict) -> str:
    parts: list[str] = []
    for key in ("body", "subtitle", "bullets", "paragraphs"):
        text = _as_text(slide.get(key))
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def normalize_generator_content(content: dict) -> dict:
    normalized = dict(content)
    bullets = normalized.get("bullets")
    if bullets:
        paragraphs = list(normalized.get("paragraphs") or [])
        paragraphs.extend(bullets if isinstance(bullets, list) else [bullets])
        normalized["paragraphs"] = paragraphs
    tables = list(normalized.get("tables") or [])
    table = normalized.get("table")
    if table is not None:
        tables.append(table)
    if tables:
        normalized["tables"] = _coerce_tables(tables)
    slides = normalized.get("slides")
    if not isinstance(slides, list):
        return normalized
    normalized_slides = []
    for slide in slides:
        if not isinstance(slide, dict):
            normalized_slides.append(slide)
            continue
        slide = dict(slide)
        merged_body = _merge_slide_body(slide)
        if merged_body:
            slide["body"] = merged_body
        slide_tables = list(slide.get("tables") or [])
        slide_table = slide.get("table")
        if slide_table is not None:
            slide_tables.append(slide_table)
        if slide_tables:
            slide["tables"] = _coerce_tables(slide_tables)
        normalized_slides.append(slide)
    normalized["slides"] = normalized_slides
    return normalized


def validate_generator_content(content: dict, fmt: str) -> str | None:
    if fmt != "ppt":
        return None
    slides = content.get("slides")
    if not isinstance(slides, list):
        return None
    for index, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue
        title = _as_text(slide.get("title"))
        body = _as_text(slide.get("body"))
        tables = slide.get("tables") or []
        if title and not body and not tables:
            return (
                f"第 {index} 页「{title}」缺少正文，请提供 body、bullets、"
                "paragraphs、subtitle 或 tables"
            )
    return None


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
        if not isinstance(slide, dict):
            continue
        for key in ("title", "body", "subtitle"):
            value = slide.get(key, "")
            if value:
                parts.append(str(value))
        for table in slide.get("tables", []):
            data = table if isinstance(table, list) else table.get("data", [])
            for row in data:
                for cell in row:
                    parts.append(str(cell))
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
