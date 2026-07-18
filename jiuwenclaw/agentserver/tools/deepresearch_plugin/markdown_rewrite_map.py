# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""UTF-8 byte boundary helpers for Markdown rewrite selections."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token


UnitType = Literal["heading", "paragraph", "list_item"]


@dataclass(frozen=True, slots=True)
class RewriteUnit:
    """A supported source block that can contain rewrite slots."""

    unit_id: str
    unit_type: UnitType
    start_byte: int
    end_byte: int
    level: int | None
    list_depth: int | None
    list_marker: str | None
    slots: tuple["RewriteSlot", ...] = ()
    protected: tuple["ProtectedAnchor", ...] = ()


@dataclass(frozen=True, slots=True)
class UnsupportedRegion:
    """A source block that must not be treated as rewritable prose."""

    kind: str
    start_byte: int
    end_byte: int


@dataclass(frozen=True, slots=True)
class MarkdownRewriteMap:
    """Immutable block-level classification of a Markdown source document."""

    source: str
    units: tuple[RewriteUnit, ...]
    unsupported_regions: tuple[UnsupportedRegion, ...]


class RewriteMapError(ValueError):
    """A stable selection-to-source mapping failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Utf8BoundaryTable:
    """Map Python codepoint indexes to UTF-8 byte offsets and back."""

    text: str
    codepoint_to_byte: tuple[int, ...] = field(init=False)
    _byte_to_codepoint: dict[int, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        offsets = [0]
        byte_offset = 0
        for character in self.text:
            byte_offset += len(character.encode("utf-8"))
            offsets.append(byte_offset)
        codepoint_to_byte = tuple(offsets)
        object.__setattr__(self, "codepoint_to_byte", codepoint_to_byte)
        object.__setattr__(
            self,
            "_byte_to_codepoint",
            {offset: index for index, offset in enumerate(codepoint_to_byte)},
        )

    def require_byte_boundary(self, byte_offset: int) -> int:
        """Return the codepoint index at ``byte_offset`` or reject the offset."""
        if type(byte_offset) is not int:
            raise RewriteMapError(
                "SELECTION_MAPPING_CONFLICT",
                f"byte offset {byte_offset!r} must be an integer",
            )
        try:
            return self._byte_to_codepoint[byte_offset]
        except (KeyError, TypeError) as exc:
            raise RewriteMapError(
                "SELECTION_MAPPING_CONFLICT",
                f"byte offset {byte_offset!r} is outside the text or splits a UTF-8 codepoint",
            ) from exc


def sha256_byte_range(text: str, start_byte: int, end_byte: int) -> str:
    """Hash a valid half-open UTF-8 byte range, including an empty range.

    This byte-level helper permits ``start_byte == end_byte``. Higher-level
    rewrite preparation is responsible for requiring a non-empty user selection.
    """
    table = Utf8BoundaryTable(text)
    table.require_byte_boundary(start_byte)
    table.require_byte_boundary(end_byte)
    if start_byte > end_byte:
        raise RewriteMapError(
            "SELECTION_MAPPING_CONFLICT",
            "start byte offset must not exceed end byte offset",
        )
    return hashlib.sha256(text.encode("utf-8")[start_byte:end_byte]).hexdigest()


class _SourceLines:
    """Translate markdown-it line maps to exact UTF-8 source byte ranges."""

    def __init__(self, source: str):
        lines = []
        line_start = 0
        index = 0
        while index < len(source):
            character = source[index]
            if character == "\n":
                index += 1
                lines.append(source[line_start:index])
                line_start = index
            elif character == "\r":
                index += 2 if source[index + 1 : index + 2] == "\n" else 1
                lines.append(source[line_start:index])
                line_start = index
            else:
                index += 1
        if line_start < len(source):
            lines.append(source[line_start:])
        self.lines = lines
        starts = [0]
        for line in self.lines:
            starts.append(starts[-1] + len(line.encode("utf-8")))
        self.starts = tuple(starts)

    @staticmethod
    def _content(line: str) -> str:
        if line.endswith("\r\n"):
            return line[:-2]
        if line.endswith(("\n", "\r")):
            return line[:-1]
        return line

    def byte_range(self, line_map: list[int] | None) -> tuple[int, int] | None:
        if (
            line_map is None
            or len(line_map) != 2
            or line_map[0] < 0
            or line_map[0] >= line_map[1]
            or line_map[1] > len(self.lines)
        ):
            return None
        start_line, end_line = line_map
        while end_line > start_line and not self._content(
            self.lines[end_line - 1]
        ).strip(" \t"):
            end_line -= 1
        if end_line == start_line:
            return None
        start_byte = self.starts[start_line]
        end_byte = self.starts[end_line]
        final_line = self.lines[end_line - 1]
        if final_line.endswith("\r\n"):
            end_byte -= 2
        elif final_line.endswith(("\n", "\r")):
            end_byte -= 1
        return start_byte, end_byte

    def line(self, line_number: int) -> str | None:
        if not 0 <= line_number < len(self.lines):
            return None
        return self.lines[line_number]


_LIST_OPEN_TYPES = {"bullet_list_open", "ordered_list_open"}
_LIST_CLOSE_TYPES = {"bullet_list_close", "ordered_list_close"}
_UNSUPPORTED_BLOCK_KINDS = {
    "blockquote_open": "blockquote",
    "table_open": "table",
    "fence": "fenced_code",
    "code_block": "indented_code",
    "html_block": "html_block",
}
_LIST_MARKER = re.compile(r"^[ \t]*(?P<marker>[-+*]|\d+[.)])(?=[ \t])")


def _matching_close(tokens: list[Token], open_index: int) -> int | None:
    nesting = 0
    for index in range(open_index, len(tokens)):
        nesting += tokens[index].nesting
        if nesting == 0:
            return index
    return None


def _is_image_only(inline: Token) -> bool:
    children = inline.children or []
    has_image = any(child.type == "image" for child in children)
    non_text_topology = {
        "image",
        "link_open",
        "link_close",
        "softbreak",
        "hardbreak",
    }
    return has_image and all(
        child.type in non_text_topology
        or (child.type == "text" and not child.content.strip())
        for child in children
    )


def _list_item_kind(tokens: list[Token], open_index: int, close_index: int) -> str | None:
    contents = tokens[open_index + 1 : close_index]
    if any(token.type in _LIST_OPEN_TYPES for token in contents):
        return "nested_list"
    paragraph_count = sum(token.type == "paragraph_open" for token in contents)
    allowed = {"paragraph_open", "inline", "paragraph_close"}
    if paragraph_count != 1 or any(token.type not in allowed for token in contents):
        return "compound_list_item"
    inline = next((token for token in contents if token.type == "inline"), None)
    if inline is None or _is_image_only(inline):
        return "image_only"
    return None


def build_rewrite_map(markdown: str) -> MarkdownRewriteMap:
    """Classify supported Markdown blocks without deriving inline rewrite slots."""
    parser = MarkdownIt("commonmark", {"html": True}).enable("table")
    tokens = parser.parse(markdown)
    source_lines = _SourceLines(markdown)
    units: list[RewriteUnit] = []
    unsupported: list[UnsupportedRegion] = []
    list_stack: list[str] = []

    def add_unit(
        unit_type: UnitType,
        byte_range: tuple[int, int],
        *,
        level: int | None = None,
        list_depth: int | None = None,
        list_marker: str | None = None,
    ) -> None:
        start_byte, end_byte = byte_range
        ordinal = len(units)
        units.append(
            RewriteUnit(
                unit_id=f"{unit_type}_{ordinal}_{start_byte}_{end_byte}",
                unit_type=unit_type,
                start_byte=start_byte,
                end_byte=end_byte,
                level=level,
                list_depth=list_depth,
                list_marker=list_marker,
            )
        )

    def add_unsupported(kind: str, byte_range: tuple[int, int] | None) -> None:
        if byte_range is not None:
            unsupported.append(UnsupportedRegion(kind, *byte_range))

    index = 0
    while index < len(tokens):
        token = tokens[index]

        if token.type in _LIST_OPEN_TYPES:
            list_stack.append(token.type)
            index += 1
            continue
        if token.type in _LIST_CLOSE_TYPES:
            if list_stack:
                list_stack.pop()
            index += 1
            continue

        if token.type == "list_item_open":
            close_index = _matching_close(tokens, index)
            byte_range = source_lines.byte_range(token.map)
            if close_index is None or byte_range is None:
                add_unsupported("ambiguous_list_item", byte_range)
                index += 1
                continue
            depth = len(list_stack) - 1
            unsupported_kind = _list_item_kind(tokens, index, close_index)
            if depth > 0:
                unsupported_kind = "nested_list"
            if unsupported_kind is not None:
                add_unsupported(unsupported_kind, byte_range)
            else:
                source_line = source_lines.line(token.map[0]) if token.map else None
                marker_match = _LIST_MARKER.match(source_line or "")
                if marker_match is None:
                    add_unsupported("ambiguous_list_item", byte_range)
                else:
                    add_unit(
                        "list_item",
                        byte_range,
                        list_depth=depth,
                        list_marker=marker_match.group("marker"),
                    )
            index = close_index + 1
            continue

        if token.level != 0:
            index += 1
            continue

        byte_range = source_lines.byte_range(token.map)
        if token.type == "heading_open":
            close_index = _matching_close(tokens, index)
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            valid_shape = (
                close_index == index + 2
                and inline is not None
                and inline.type == "inline"
                and tokens[close_index].type == "heading_close"
            )
            if (
                byte_range is None
                or not valid_shape
                or not token.tag.startswith("h")
                or not token.tag[1:].isdigit()
            ):
                add_unsupported("ambiguous_heading", byte_range)
            elif _is_image_only(inline):
                add_unsupported("image_only", byte_range)
            else:
                add_unit("heading", byte_range, level=int(token.tag[1:]))
            index = close_index + 1 if close_index is not None else index + 1
            continue

        if token.type == "paragraph_open":
            close_index = _matching_close(tokens, index)
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            if (
                close_index is None
                or byte_range is None
                or inline is None
                or inline.type != "inline"
            ):
                add_unsupported("ambiguous_paragraph", byte_range)
            elif _is_image_only(inline):
                add_unsupported("image_only", byte_range)
            else:
                add_unit("paragraph", byte_range)
            index = close_index + 1 if close_index is not None else index + 1
            continue

        unsupported_kind = _UNSUPPORTED_BLOCK_KINDS.get(token.type)
        if unsupported_kind is not None:
            add_unsupported(unsupported_kind, byte_range)
            close_index = _matching_close(tokens, index) if token.nesting == 1 else None
            index = close_index + 1 if close_index is not None else index + 1
            continue

        if token.map is not None and token.nesting >= 0:
            add_unsupported(token.type, byte_range)
        index += 1

    return MarkdownRewriteMap(markdown, tuple(units), tuple(unsupported))
