# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
from typing import List, Optional

import pdfplumber

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.captioner import ImageCaptioner


@register_parser([".pdf", ".PDF"])
class PDFParser(Parser):
    """Local file parser for PDF format"""

    def __init__(self, **kwargs):
        pass

    @staticmethod
    async def _extract_images_from_pdf_page(
        pdf_page,
        pdf_page_num: int,
        filename: str,
        output_dir: str = "images",
    ) -> List[str]:
        """Extract images from a PDF page and save them to the output directory.

        Args:
            pdf_page (_type_): _description_
            pdf_page_num (int): _description_
            filename (str): _description_
            output_dir (str, optional): _description_. Defaults to "images".

        Returns:
            List[str]: _description_
        """
        images = []
        for img_index, img in enumerate(pdf_page.images):
            # Get bounding box
            x0 = img["x0"]
            top = img["top"]
            x1 = img["x1"]
            bottom = img["bottom"]

            # Crop page to the image region
            cropped_page = pdf_page.crop((x0, top, x1, bottom))

            # Convert cropped region to a PIL image
            pil_image = cropped_page.to_image(resolution=300).original
            os.makedirs(output_dir, exist_ok=True)
            image_path = os.path.join(output_dir, f"{filename}__page_{pdf_page_num}__img_{img_index}.png")
            images.append(image_path)
            pil_image.save(image_path)
        return images

    async def _parse(self, file_path: str, llm_client: Optional[Model] = None) -> Optional[str]:
        """Parse PDF file"""
        try:
            image_captioner = ImageCaptioner(llm_client=llm_client)

            async def _async_parse_pdf():
                captions = []
                content = []
                with pdfplumber.open(file_path) as pdf:
                    for page_num, page in enumerate(pdf.pages, 1):
                        page_text = page.extract_text() or ""
                        if page_text:
                            content.append(page_text)
                        extracted_images = await self._extract_images_from_pdf_page(
                            pdf_page=page,
                            filename=os.path.basename(file_path),
                            pdf_page_num=page_num,
                        )
                        if image_captioner:
                            captions = await image_captioner.caption_images(extracted_images)
                        for caption in captions:
                            if caption:
                                content.append(caption)
                return "\n".join([line for line in content if line.strip()])

            result = await _async_parse_pdf()
            return result if result else None
        except Exception as e:
            logger.error(f"Failed to parse PDF {file_path}: {e}")
            return None
