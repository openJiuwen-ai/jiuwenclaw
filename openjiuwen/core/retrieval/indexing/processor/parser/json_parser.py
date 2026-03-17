# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
from typing import Optional

import aiofiles

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser


@register_parser([".json", ".JSON"])
class JSONParser(Parser):
    """Local file parser for JSON format"""

    def __init__(self, **kwargs):
        pass

    async def _parse(self, file_path: str, *args, **kwargs) -> Optional[str]:
        """Parse JSON file"""
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_content = await f.read()

            def _format_json() -> str:
                try:
                    json_data = json.loads(raw_content)
                    return json.dumps(json_data, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    logger.error(f"JSON format error: {file_path}")
                    return raw_content

            return await asyncio.to_thread(_format_json)
        except Exception as e:
            logger.error(f"Failed to parse JSON {file_path}: {e}")
            return None
