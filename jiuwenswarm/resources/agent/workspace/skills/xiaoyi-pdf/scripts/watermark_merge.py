import os
from typing import List, Optional
from pypdf import PdfReader, PdfWriter

from security import validate_safe_path


def parse_page_ranges(pages_str: Optional[str], total_pages: int) -> List[int]:
    if pages_str is None:
        return list(range(total_pages))
    if pages_str.strip() == "":
        return []
    indices = set()
    parts = [p.strip() for p in pages_str.split(",")]
    for part in parts:
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            try:
                start = int(start_str.strip()) if start_str.strip() else 1
                end = int(end_str.strip()) if end_str.strip() else total_pages
            except ValueError as e:
                raise ValueError(f"Invalid page range: {part}") from e
            for p in range(start, end + 1):
                if 1 <= p <= total_pages:
                    indices.add(p - 1)
        else:
            try:
                p = int(part.strip())
            except ValueError as e:
                raise ValueError(f"Invalid page range: {part}") from e
            if 1 <= p <= total_pages:
                indices.add(p - 1)
    return sorted(indices)


def merge_watermark(input_pdf: str, watermark_pdf: str, output_pdf: str, pages: Optional[str] = None):
    validate_safe_path(input_pdf, allowed_extensions={".pdf"})
    validate_safe_path(watermark_pdf, allowed_extensions={".pdf"})
    if not os.path.exists(input_pdf):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")
    if not os.path.exists(watermark_pdf):
        raise FileNotFoundError(f"Watermark PDF not found: {watermark_pdf}")

    reader = PdfReader(input_pdf)
    watermark_reader = PdfReader(watermark_pdf)
    if len(watermark_reader.pages) == 0:
        raise ValueError(f"Watermark PDF has no pages: {watermark_pdf}")
    watermark_page = watermark_reader.pages[0]
    writer = PdfWriter()

    target_indices = set(parse_page_ranges(pages, len(reader.pages)))

    for i, page in enumerate(reader.pages):
        if i in target_indices:
            page.merge_page(watermark_page)
        writer.add_page(page)

    out_dir = os.path.dirname(os.path.abspath(output_pdf))
    if out_dir and not os.path.isdir(out_dir):
        raise FileNotFoundError(f"Output directory does not exist: {out_dir}")

    with open(output_pdf, "wb") as f:
        writer.write(f)
