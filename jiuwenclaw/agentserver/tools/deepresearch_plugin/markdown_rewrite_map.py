# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""UTF-8 byte boundary helpers for Markdown rewrite selections."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


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
