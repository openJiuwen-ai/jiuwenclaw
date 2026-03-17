# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
from typing import Any, Iterator, Optional, Union, Dict, List
from dataclasses import dataclass

from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.common.logging import llm_logger, LogEventType


class MarkdownElementType:
    """Markdown element type constants"""
    HEADER = "header"
    CODE_BLOCK = "code_block"
    INLINE_CODE = "inline_code"
    LINK = "link"
    IMAGE = "image"
    TABLE = "table"
    LIST = "list"
    TEXT = "text"


@dataclass
class MarkdownElement:
    """Single Markdown element"""
    type: str  # Element type
    content: Dict[str, Any]  # Element content
    start_pos: int  # Start position in original text
    end_pos: int  # End position in original text
    raw: str  # Raw text


@dataclass
class MarkdownContent:
    """Structured representation of Markdown content"""
    raw_content: str = ""
    elements: List[MarkdownElement] = None  # All elements in original order
    headers: List[Dict[str, str]] = None
    code_blocks: List[Dict[str, str]] = None
    links: List[Dict[str, str]] = None
    images: List[Dict[str, str]] = None
    tables: List[str] = None
    lists: List[str] = None

    def __post_init__(self):
        if self.elements is None:
            self.elements = []
        if self.headers is None:
            self.headers = []
        if self.code_blocks is None:
            self.code_blocks = []
        if self.links is None:
            self.links = []
        if self.images is None:
            self.images = []
        if self.tables is None:
            self.tables = []
        if self.lists is None:
            self.lists = []


class MarkdownOutputParser(BaseOutputParser):

    async def parse(self, llm_output: Union[str, AssistantMessage]) -> Optional[MarkdownContent]:

        model_name = None
        if isinstance(llm_output, AssistantMessage):
            text = llm_output.content
            if llm_output.usage_metadata:
                model_name = llm_output.usage_metadata.model_name
        elif isinstance(llm_output, str):
            text = llm_output
        else:
            if UserConfig.is_sensitive():
                llm_logger.warning(
                    "Unsupported llm_output type for parse.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=model_name
                )
            else:
                llm_logger.warning(
                    "Unsupported llm_output type for parse.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=model_name,
                    metadata={"llm_output_type": str(type(llm_output))}
                )
            return None

        if not text:
            return None

        try:
            markdown_content = MarkdownContent(raw_content=text)

            self._extract_all_elements(text, markdown_content)

            self._populate_categorized_lists(markdown_content)

            return markdown_content

        except Exception as e:
            if UserConfig.is_sensitive():
                llm_logger.error(
                    "An unexpected error during Markdown parsing",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=model_name
                )
            else:
                llm_logger.error(
                    "An unexpected error during Markdown parsing",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=model_name,
                    exception=str(e),
                    metadata={"content": text}
                )
            return None

    async def stream_parse(self, streaming_inputs: Iterator[Union[str, AssistantMessageChunk]]) -> Iterator[
        Optional[MarkdownContent]]:
        buffer = ""
        last_parsed_length = 0
        model_name = None

        for chunk in streaming_inputs:

            if isinstance(chunk, AssistantMessageChunk):
                if chunk.content:
                    buffer += chunk.content
                if chunk.usage_metadata:
                    model_name = chunk.usage_metadata.model_name
            elif isinstance(chunk, str):
                buffer += chunk
            else:
                if UserConfig.is_sensitive():
                    llm_logger.warning(
                        "Unsupported chunk type for stream_parse.",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=model_name
                    )
                else:
                    llm_logger.warning(
                        "Unsupported chunk type for stream_parse.",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=model_name,
                        metadata={"chunk_type": str(type(chunk))}
                    )
                continue

            if len(buffer) > last_parsed_length:
                try:
                    markdown_content = MarkdownContent(raw_content=buffer)

                    self._extract_all_elements(buffer, markdown_content)

                    self._populate_categorized_lists(markdown_content)

                    yield markdown_content
                    last_parsed_length = len(buffer)

                except Exception as e:
                    if UserConfig.is_sensitive():
                        llm_logger.error(
                            "An unexpected error during streaming Markdown parsing",
                            event_type=LogEventType.LLM_CALL_ERROR,
                            model_name=model_name
                        )
                    else:
                        llm_logger.error(
                            "An unexpected error during streaming Markdown parsing",
                            event_type=LogEventType.LLM_CALL_ERROR,
                            model_name=model_name,
                            exception=str(e),
                            metadata={"content": buffer}
                        )
                    continue

        if buffer.strip():
            try:
                markdown_content = MarkdownContent(raw_content=buffer)

                self._extract_all_elements(buffer, markdown_content)

                self._populate_categorized_lists(markdown_content)

                yield markdown_content

            except Exception as e:
                if UserConfig.is_sensitive():
                    llm_logger.error(
                        "An unexpected error during final streaming Markdown parsing",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=model_name
                    )
                else:
                    llm_logger.error(
                        "An unexpected error during final streaming Markdown parsing",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=model_name,
                        exception=str(e),
                        metadata={"content": buffer}
                    )

    def _extract_all_elements(self, text: str, markdown_content: MarkdownContent):
        elements = []

        # Extract headers
        for match in re.finditer(r'^(#{1,6})\s+(.+)$', text, re.MULTILINE):
            level = len(match.group(1))
            title = match.group(2).strip()
            elements.append(MarkdownElement(
                type=MarkdownElementType.HEADER,
                content={"level": str(level), "title": title},
                start_pos=match.start(),
                end_pos=match.end(),
                raw=match.group(0)
            ))

        # Extract code blocks
        for match in re.finditer(r'```(\w*)\n(.*?)\n```', text, re.DOTALL):
            language = match.group(1) or "text"
            code = match.group(2)
            elements.append(MarkdownElement(
                type=MarkdownElementType.CODE_BLOCK,
                content={"language": language, "code": code},
                start_pos=match.start(),
                end_pos=match.end(),
                raw=match.group(0)
            ))

        for match in re.finditer(r'`([^`\n]+)`', text):
            elements.append(MarkdownElement(
                type=MarkdownElementType.INLINE_CODE,
                content={"code": match.group(1)},
                start_pos=match.start(),
                end_pos=match.end(),
                raw=match.group(0)
            ))

        for match in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', text):
            alt_text = match.group(1)
            url = match.group(2)
            elements.append(MarkdownElement(
                type=MarkdownElementType.IMAGE,
                content={"alt": alt_text, "url": url},
                start_pos=match.start(),
                end_pos=match.end(),
                raw=match.group(0)
            ))

        for match in re.finditer(r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)', text):
            text_part = match.group(1)
            url = match.group(2)
            elements.append(MarkdownElement(
                type=MarkdownElementType.LINK,
                content={"text": text_part, "url": url},
                start_pos=match.start(),
                end_pos=match.end(),
                raw=match.group(0)
            ))

        self._extract_multiline_elements(text, elements)

        elements.sort(key=lambda x: x.start_pos)
        markdown_content.elements = elements

    def _extract_multiline_elements(self, text: str, elements: List[MarkdownElement]):
        lines = text.split('\n')
        current_pos = 0

        table_lines = []
        list_lines = []
        table_start_pos = -1
        list_start_pos = -1

        for _, line in enumerate(lines):
            line_start_pos = current_pos
            line_end_pos = current_pos + len(line)
            current_pos = line_end_pos + 1

            if '|' in line.strip() and line.strip():
                if not table_lines:
                    table_start_pos = line_start_pos
                table_lines.append(line)
            else:
                if table_lines:
                    # Table end
                    table_content = '\n'.join(table_lines)
                    elements.append(MarkdownElement(
                        type=MarkdownElementType.TABLE,
                        content={"table": table_content},
                        start_pos=table_start_pos,
                        end_pos=line_start_pos - 1,
                        raw=table_content
                    ))
                    table_lines = []

            if re.match(r'^\s*[-*+]\s+', line) or re.match(r'^\s*\d+\.\s+', line):
                if not list_lines:
                    list_start_pos = line_start_pos
                list_lines.append(line)
            elif re.match(r'^\s*$', line) and list_lines:
                list_lines.append(line)
            else:
                if list_lines:
                    list_content = '\n'.join(list_lines).strip()
                    if list_content:
                        elements.append(MarkdownElement(
                            type=MarkdownElementType.LIST,
                            content={"list": list_content},
                            start_pos=list_start_pos,
                            end_pos=line_start_pos - 1,
                            raw=list_content
                        ))
                    list_lines = []

        if table_lines:
            table_content = '\n'.join(table_lines)
            elements.append(MarkdownElement(
                type=MarkdownElementType.TABLE,
                content={"table": table_content},
                start_pos=table_start_pos,
                end_pos=len(text),
                raw=table_content
            ))

        if list_lines:
            list_content = '\n'.join(list_lines).strip()
            if list_content:
                elements.append(MarkdownElement(
                    type=MarkdownElementType.LIST,
                    content={"list": list_content},
                    start_pos=list_start_pos,
                    end_pos=len(text),
                    raw=list_content
                ))

    def _populate_categorized_lists(self, markdown_content: MarkdownContent):
        for element in markdown_content.elements:
            if element.type == MarkdownElementType.HEADER:
                markdown_content.headers.append({
                    "level": element.content["level"],
                    "title": element.content["title"],
                    "raw": element.raw
                })
            elif element.type == MarkdownElementType.CODE_BLOCK:
                markdown_content.code_blocks.append({
                    "language": element.content["language"],
                    "code": element.content["code"],
                    "raw": element.raw
                })
            elif element.type == MarkdownElementType.INLINE_CODE:
                markdown_content.code_blocks.append({
                    "language": "inline",
                    "code": element.content["code"],
                    "raw": element.raw
                })
            elif element.type == MarkdownElementType.LINK:
                markdown_content.links.append({
                    "text": element.content["text"],
                    "url": element.content["url"],
                    "raw": element.raw
                })
            elif element.type == MarkdownElementType.IMAGE:
                markdown_content.images.append({
                    "alt": element.content["alt"],
                    "url": element.content["url"],
                    "raw": element.raw
                })
            elif element.type == MarkdownElementType.TABLE:
                markdown_content.tables.append(element.content["table"])
            elif element.type == MarkdownElementType.LIST:
                markdown_content.lists.append(element.content["list"])

    def _extract_headers(self, text: str) -> List[Dict[str, str]]:
        headers = []
        pattern = r'^(#{1,6})\s+(.+)$'
        for match in re.finditer(pattern, text, re.MULTILINE):
            level = len(match.group(1))
            title = match.group(2).strip()
            headers.append({
                "level": str(level),
                "title": title,
                "raw": match.group(0)
            })
        return headers

    def _extract_code_blocks(self, text: str) -> List[Dict[str, str]]:
        code_blocks = []
        pattern = r'```(\w*)\n(.*?)\n```'
        for match in re.finditer(pattern, text, re.DOTALL):
            language = match.group(1) or "text"
            code = match.group(2)
            code_blocks.append({
                "language": language,
                "code": code,
                "raw": match.group(0)
            })

        inline_pattern = r'`([^`\n]+)`'
        for match in re.finditer(inline_pattern, text):
            code_blocks.append({
                "language": "inline",
                "code": match.group(1),
                "raw": match.group(0)
            })

        return code_blocks

    def _extract_links(self, text: str) -> List[Dict[str, str]]:
        links = []
        pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        for match in re.finditer(pattern, text):
            text_part = match.group(1)
            url = match.group(2)
            links.append({
                "text": text_part,
                "url": url,
                "raw": match.group(0)
            })
        return links

    def _extract_images(self, text: str) -> List[Dict[str, str]]:
        images = []
        pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        for match in re.finditer(pattern, text):
            alt_text = match.group(1)
            url = match.group(2)
            images.append({
                "alt": alt_text,
                "url": url,
                "raw": match.group(0)
            })
        return images

    def _extract_tables(self, text: str) -> List[str]:
        tables = []
        lines = text.split('\n')
        table_lines = []
        in_table = False

        for line in lines:
            if '|' in line.strip() and line.strip():
                table_lines.append(line)
                in_table = True
            else:
                if in_table and table_lines:
                    tables.append('\n'.join(table_lines))
                    table_lines = []
                in_table = False

        if table_lines:
            tables.append('\n'.join(table_lines))

        return tables

    def _extract_lists(self, text: str) -> List[str]:
        lists = []
        lines = text.split('\n')
        list_lines = []
        in_list = False

        for line in lines:
            if re.match(r'^\s*[-*+]\s+', line) or re.match(r'^\s*\d+\.\s+', line):
                list_lines.append(line)
                in_list = True
            elif in_list and line.strip() == '':
                list_lines.append(line)
            else:
                if in_list and list_lines:
                    lists.append('\n'.join(list_lines).strip())
                    list_lines = []
                in_list = False

        if list_lines:
            lists.append('\n'.join(list_lines).strip())

        return lists
