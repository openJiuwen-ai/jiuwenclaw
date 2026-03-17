# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional

import aiofiles
from charset_normalizer import detect

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser


@register_parser([".txt", ".TXT", ".md", ".MD", ".markdown", ".MARKDOWN"])
class TxtMdParser(Parser):
    """Local file parser for TXT/MD format"""

    def __init__(self, **kwargs):
        pass

    async def _parse(self, file_path: str, *args, **kwargs) -> Optional[str]:
        """Parse TXT/MD file"""
        try:
            async with aiofiles.open(file_path, "rb") as f:
                raw_data = await f.read()
                # Use charset-normalizer to detect encoding
                detected = detect(raw_data)
                # Handle different return types: CharsetMatch object, dict, or None
                if detected is None:
                    encoding = "utf-8"
                elif isinstance(detected, dict):
                    # If dict is returned, try to get encoding field
                    encoding = detected.get("encoding", "utf-8") or "utf-8"
                elif hasattr(detected, "encoding"):
                    # If CharsetMatch object, get encoding attribute
                    encoding = detected.encoding if detected.encoding else "utf-8"
                else:
                    encoding = "utf-8"

            async with aiofiles.open(file_path, "r", encoding=encoding, errors="ignore") as f:
                content = await f.read()

            return content.strip() if content else None
        except Exception as e:
            logger.error(f"Failed to parse TXT/MD {file_path}: {e}")
            return None
