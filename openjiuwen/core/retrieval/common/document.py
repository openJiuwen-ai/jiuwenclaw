# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Document Data Models

Contains Document and TextChunk data models.
"""

__all__ = ["Document", "TextChunk", "MultimodalDocument"]

import base64
import mimetypes
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Self, overload

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError
from pydantic_core import PydanticCustomError

from openjiuwen.core.foundation.store.base_reranker import Document

NOT_SET = None

# Adding missing MIME type for .jfif files mapping them to "image/jpeg"
mimetypes.add_type("image/jpeg", ".jfif", strict=True)


class TextChunk(BaseModel):
    """Text chunk data model"""

    id_: str = Field(..., description="Chunk ID")
    text: str = Field(..., description="Chunk text content")
    doc_id: str = Field(..., description="Parent document ID")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Chunk metadata")
    embedding: list[float] | None = Field(default=None, description="Chunk embedding vector")

    @classmethod
    def from_document(cls, doc: Document, chunk_text: str, id_: str = "") -> "TextChunk":
        """Create TextChunk from Document"""
        return cls(
            id_=id_ if id_ else str(uuid.uuid4()),
            text=chunk_text,
            doc_id=doc.id_,
            metadata=doc.metadata,
        )


class MultimodalDocument(Document):
    """Multimodal Document data model for handling documents with multiple content types.

    A MultimodalDocument extends the base Document class to support multiple content
    modalities (text, image, audio, video) within a single document. Unlike the base
    Document class which only supports text, MultimodalDocument can contain a mix of
    different content types that can be used together for multimodal embedding and
    retrieval tasks.

    The `text` field serves as a fallback for text-only services that don't support
    multimodal content. Multimodal content should be added via the `add_field()` method,
    which stores the content in the internal `_data` structure.

    Supported Modalities (via add_field method):
        - text: Plain text content
        - image: Image files (supports common formats like jpg, png, etc.)
        - audio: Audio files (supports various audio formats)
        - video: Video files (supports various video formats)

    Examples:
        Create a multimodal document with text and an image:

            doc = MultimodalDocument()
            doc.add_field("text", "This is a description")
            doc.add_field("image", file_path=Path("image.jpg"))

            # Or using method chaining:
            doc = (MultimodalDocument()
                   .add_field("text", "Hello world")
                   .add_field("image", file_path=Path("photo.png")))

        Add image or video via url:

            doc = MultimodalDocument()
            doc.add_field("image", data="https://openjiuwen.com/img/jiuwen_logo.png")

        Add base64-encoded data directly:

            doc = MultimodalDocument()
            doc.add_field("audio", data="data:audio/wav;base64,...")

        Access structured content for embedding:

            content = doc.content  # Returns list of dicts in embedding-ready format
            input = doc.dashscope_input  # Returns list of dicts in dashscope format
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    text: str = Field(
        default="",
        description="Document text content. This field serves as a fallback for text-only services that don't support multimodal content. For multimodal content, use add_field() method instead.",
    )
    _data: list[tuple[Literal["text", "image", "audio", "video"], str, str]] = PrivateAttr(
        default_factory=list, init=False
    )
    _content_cache: list = PrivateAttr(default_factory=list, init=False)
    _dashscope_cache: dict = PrivateAttr(default_factory=dict, init=False)

    @property
    def content(self) -> list[dict[str, Any]]:
        """Get the content field"""
        if self._content_cache:
            return deepcopy(self._content_cache)
        content = []
        for kind, data, data_id in self._data:
            match kind:
                case "text":
                    content.append({"type": "text", "text": data})
                case "image" | "video":
                    content.append(
                        {
                            "type": f"{kind}_url",
                            f"{kind}_url": {"url": data},
                        }
                    )
                case "audio":
                    file_format = re.match(r"data:audio/(.+?);base64,", data).group(1)
                    content.append(
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data, "format": file_format},
                        }
                    )
            if data_id:
                content[-1]["uuid"] = data_id
        self._content_cache = deepcopy(content)
        return content

    @property
    def dashscope_input(self) -> dict[str, Any]:
        """Get dashscope format of input dict"""
        if self._dashscope_cache:
            return deepcopy(self._dashscope_cache)
        content = {}
        images = []
        has_field = set()
        for kind, data, data_id in self._data:
            if kind in has_field:
                _raise_validation_error_with_info(
                    error_type=f"multiple_{kind}_fields_present",
                    message="Dashscope format does not support multiple fields of same modality",
                    context=dict(fields=self._data),
                )
            match kind:
                case "text":
                    has_field.add(kind)
                    content[kind] = data
                case "image":
                    images.append(data)
                case "video":
                    if data.startswith("data:video/"):
                        _raise_validation_error_with_info(
                            error_type="unsupported_format",
                            message="Dashscope format only support video in url format, not base64",
                            context=dict(kind=kind, data_id=data_id, data=data),
                        )
                    has_field.add(kind)
                    content[kind] = data
                case _:
                    _raise_validation_error_with_info(
                        error_type="unsupported_format",
                        message=f"Dashscope format does not support modality {kind}",
                        context=dict(kind=kind, data_id=data_id, data=data),
                    )
        if images:
            if len(images) == 1:
                content["image"] = images[0]
            else:
                content["multi_images"] = images
        self._dashscope_cache = deepcopy(content)
        return content

    @overload
    def add_field(self, kind: Literal["text"], data: str) -> Self:
        """Add a text field to current multimodal document, you can chain add_field calls together, for example:

        `doc = MultimodalDocument().add_field("text", "hello world").add_field("image", ...)`

        Args:
            kind (Literal["text", "image", "audio", "video"]): modality of the new field.
            data (str, optional): Base64-encoded str of the data.

        Returns:
            Self: the current MultimodalDocument instance
        """

    @overload
    def add_field(
        self,
        kind: Literal["image", "audio", "video"],
        data: str = NOT_SET,
        file_path: Path = NOT_SET,
        data_id: str = "",
    ) -> Self: ...

    def add_field(
        self,
        kind: Literal["text", "image", "audio", "video"],
        data: str = NOT_SET,
        file_path: Path = NOT_SET,
        data_id: str = "",
    ) -> Self:
        """Add a data field to current multimodal document, you can chain add_field calls together, for example:

        `doc = MultimodalDocument().add_field("text", "hello world").add_field("image", ...)`

        Args:
            kind (Literal["text", "image", "audio", "video"]): modality of the new field.
            data (str, optional): url or Base64-encoded str of the data.
            file_path (Path, optional): a valid file path to a multimodal file.
            data_id (str, optional): uuid for multimodal caching, leave blank if unsure.

        Returns:
            Self: the current MultimodalDocument instance
        """
        self._content_cache.clear()
        self._dashscope_cache.clear()
        kind, data = _load_multimodal_data(kind, data, file_path)
        if data_id:
            if not (isinstance(data_id, str) and len(data_id) <= 32):
                _raise_validation_error_with_info(
                    "invalid_uuid_provided",
                    'MultimodalDocument.add_field received invalid "data_id", uuid is a string of length 32',
                    {"data_id": data_id},
                )
        elif kind != "text":
            data_id = uuid.uuid4().hex
        else:
            data_id = ""
        self._data.append((kind, data, data_id))
        return self

    def strip(self) -> Optional[Self]:
        """Compatibility layer for str-like operation"""
        return self if self._data else None


def _load_multimodal_data(
    kind: Literal["text", "image", "audio", "video"],
    data: str = NOT_SET,
    file_path: Path = NOT_SET,
) -> tuple[Literal["text", "image", "audio", "video"], str]:
    if kind not in ["text", "image", "audio", "video"]:
        _raise_validation_error_with_info(
            "unknown_kind",
            f'Unknown kind of multimodal file: {kind}, supported option: ["text", "image", "audio", "video"]',
            dict(kind=kind),
        )
    if file_path is None and data is None:
        _raise_validation_error_with_info(
            f"no_{kind}_source_provided",
            f"MultimodalDocument.add_field received no data of {kind} type",
            dict(data=data, file_path=file_path),
        )
    if file_path is not None and data is not None:
        _raise_validation_error_with_info(
            f"too_many_{kind}_source_provided",
            'MultimodalDocument.add_field cannot accept both "file_path" and "data", please only set one',
            dict(data=data, file_path=file_path),
        )
    if isinstance(data, str):
        if kind == "text" or re.match(f"data:{kind}/(.+?);base64,", data):
            return kind, data
        if kind != "audio" and data.startswith("http") and "://" in data:
            return kind, data  # Accept url as valid data
        _raise_validation_error_with_info(
            f"invalid_{kind}_data_provided",
            f'MultimodalDocument.add_field received invalid "data", this can be url or start with "data:{kind}/"',
            dict(data=data),
        )
    if not isinstance(file_path, Path):
        _raise_validation_error_with_info(
            f"invalid_{kind}_file_path_provided",
            'MultimodalDocument.add_field received invalid "file_path", this value should be a Path',
            dict(file_path=file_path),
        )
    if not file_path.is_file():
        _raise_validation_error_with_info(
            f"{kind}_path_invalid",
            f"Unable to open {kind} file at {file_path}",
            dict(kind=kind, file_path=file_path),
        )
    # mimetypes.guess_file_type was introduced in python 3.13 to make mimetypes.guess_type url-only
    guess_mime_type = getattr(mimetypes, "guess_file_type", mimetypes.guess_type)
    mime_type = guess_mime_type(str(file_path), strict=False)[0]
    if not (isinstance(mime_type, str) and "/" in mime_type):
        _raise_validation_error_with_info(
            "cannot_determine_mimetype",
            f"Unable to determine mimetype for {kind} file: {file_path}",
            dict(kind=kind, file_path=file_path),
        )
    if not mime_type.startswith(kind):
        mime_type = "/".join([kind] + mime_type.split("/")[1:])
    b64_prefix = f"data:{mime_type};base64,"
    try:
        if kind == "text":
            return kind, file_path.read_text(encoding="utf-8")
        return kind, b64_prefix + base64.b64encode(file_path.read_bytes()).decode()
    except Exception as e:
        _raise_validation_error_with_info(
            f"error_loading_{kind}",
            f"Unable to load {kind} file into base64: {e}",
            dict(kind=kind, file_path=file_path),
        )
    return kind, "Unreachable code in _load_multimodal_data"


def _raise_validation_error_with_info(error_type: str, message: str, context: dict, title: str = "MultimodalDocument"):
    """Raise pydantic validation error with sufficient information to user"""
    err = PydanticCustomError(error_type, message, context)
    raise ValidationError.from_exception_data(title, [dict(type=err, input=context)])
