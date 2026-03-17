# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import io
import os
from typing import List, Optional, Union

from docx import Document
from docx.oxml.ns import qn
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.captioner import ImageCaptioner


def _table_to_markdown(table: Table) -> str:
    """Format a python-docx Table as a markdown table."""
    rows = [[cell.text.strip().replace("|", "\\|") for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    lines = []
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")  # Markdown table need a one-line separation
    return "\n".join(lines)


def _paragraph_to_markdown(paragraph: Paragraph) -> str:
    """Format a paragraph as markdown, using heading syntax for Title/Heading 1–9 styles."""
    text = paragraph.text.strip()
    if not text:
        return ""
    style_name = None
    try:
        if paragraph.style is not None:
            style_name = getattr(paragraph.style, "name", None)
    except Exception:
        style_name = None
    if style_name is None:
        return text
    style_name = str(style_name).strip()
    if style_name == "Title":
        return "# " + text
    if style_name.startswith("Heading "):
        try:
            level = int(style_name.split()[1])
            if 1 <= level <= 9:
                return "#" * (level + 1) + " " + text  # Title=#, H1=##, H2=###, ...
        except (IndexError, ValueError) as e:
            logger.error("Error while parsing docx paragraph with style %s: %r", style_name, e)
    return text


@register_parser([".docx", ".DOCX"])
class WordParser(Parser):
    """Local file parser for DOCX format"""

    def __init__(self, **kwargs):
        pass

    @staticmethod
    def _extract_images_from_paragraph(
        element: CT_P, doc: Document, paragraph_num: int, filename: str, output_dir: str = "images"
    ) -> List[str]:
        """Extract images from a DOCX paragraph and save them to the output directory.

        Args:
            element (CT_P): The paragraph element to extract images from.
            doc (docx.Document): Docx document
            paragraph_num (int): Rhe paragraph index (for image naming).
            filename (str): The docx file name
            output_dir (str, optional): Output directory. Defaults to "images".

        Returns:
            List[str]: Parsed result
        """
        images = []
        images_per_paragraph = 0
        elem_prefix = ".//"  # avoid code check rule thinking this is a path
        for blip in element.findall(elem_prefix + qn("a:blip")):
            r_embed = blip.get(qn("r:embed"))
            if not r_embed:
                continue

            image_part = doc.part.related_parts.get(r_embed)
            if image_part:
                tmp_image_blob = image_part.blob
                with Image.open(io.BytesIO(tmp_image_blob)) as image:
                    if image.mode in ("RGBA", "P"):
                        image = image.convert("RGB")
                    os.makedirs(output_dir, exist_ok=True)

                    image_path = os.path.join(
                        output_dir, f"{filename}__para_{paragraph_num}__img_{images_per_paragraph}.png"
                    )
                    image.save(image_path)
                    images.append(image_path)
                    images_per_paragraph += 1
        return images

    async def _parse(self, file_path: str, llm_client: Optional[Model] = None) -> Optional[str]:
        """Parse DOCX file"""
        image_captioner = ImageCaptioner(llm_client=llm_client)
        try:
            doc = await asyncio.to_thread(Document, file_path)
            content = []
            block_items = await asyncio.to_thread(lambda: list(doc.iter_inner_content()))
            for paragraph_num, block in enumerate(block_items):
                elem_text = await self._parse_block(
                    block,
                    doc,
                    image_captioner=image_captioner,
                    paragraph_num=paragraph_num,
                    filename=os.path.basename(file_path),
                )
                if elem_text:
                    content.extend(elem_text)
            result = "\n".join([line for line in content if line.strip()])
            return result if result else None
        except Exception as e:
            logger.error(f"Failed to parse DOCX {file_path}: {e}")
            return None

    async def _parse_block(
        self,
        block: Union[Paragraph, Table],
        doc: Document,
        paragraph_num: int,
        filename: str,
        image_captioner: ImageCaptioner | None,
    ) -> List[str]:
        """Parse a document block (paragraph or table).

        Args:
            block (Paragraph | Table): A Paragraph or Table from doc.iter_inner_content().
            doc (docx.Document): The document containing the block.
            paragraph_num (int): The block index (for image naming).
            filename (str): The name of the file being parsed.
            image_captioner (ImageCaptioner | None): The image captioner instance (if available).

        Returns:
            List of content lines (paragraph text, image captions, or markdown table).
        """
        if isinstance(block, Paragraph):
            para_text = []
            elem_text = _paragraph_to_markdown(block)
            if elem_text:
                para_text.append(elem_text)
            extracted_images = self._extract_images_from_paragraph(
                element=getattr(block, "_element"), doc=doc, paragraph_num=paragraph_num, filename=filename
            )
            if image_captioner and extracted_images:
                captions = await image_captioner.caption_images(extracted_images)
                for caption in captions:
                    if caption:
                        para_text.append(caption)
            return para_text

        if isinstance(block, Table):
            md = _table_to_markdown(block)
            return [md] if md else []
        return []
