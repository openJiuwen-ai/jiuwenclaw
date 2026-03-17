# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.captioner import ImageCaptioner


@register_parser([".png", ".jpg", ".jpeg", ".webp", ".gif", "jfif"])
class ImageParser(Parser):
    """Parser for image files to generate captions."""

    def __init__(self, **kwargs: Any):
        pass

    async def _parse(self, image_path: str, llm_client: Optional[Model] = None) -> Optional[str]:
        """Parse image file and generate captions."""
        try:
            image_captioner = ImageCaptioner(llm_client=llm_client)
            image_captioner.cp_image(image_path)
            captions = await image_captioner.caption_images([image_path])
            return "\n".join([caption for caption in captions if caption]) if captions else None
        except Exception as e:
            logger.error(f"Failed to parse image {image_path}: {e}")
            return None
