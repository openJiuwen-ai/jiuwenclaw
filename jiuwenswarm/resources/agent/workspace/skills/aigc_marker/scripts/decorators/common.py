"""Common utilities for AIGC decorators."""
import hashlib
import json
from struct import iter_unpack
from typing import Union, cast

from pypdf._utils import StreamType
from pypdf.generic import TextStringObject

# Default fmtid for custom properties
DEFAULT_CUSTOM_PROPERTY_FMTID = "D5CDD505-2E9C-101B-9397-08002B2CF9AE"


def generate_sha256(text: str) -> str:
    """计算字符串的SHA256哈希值"""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(text.encode('utf-8'))
    return sha256_hash.hexdigest()


def get_aigc_signature(content: str) -> str:
    """
    生成AIGC隐式标识

    Args:
        content: 输入内容字符串

    Returns:
        JSON格式的AIGC标识字符串
    """
    # 计算内容SHA256哈希的前16位作为内容ID
    content_id = generate_sha256(content)[:16]
    produce_id = f"voiceassistant-{content_id}"
    propagate_id = f"voiceassistant-{content_id}"

    aigc_metadata = {
        "Label": "1",
        "ContentProducer": "001191320114777023172010000",
        "ProduceID": produce_id,
        "ReservedCode1": "",
        "ContentPropagator": "001191320114777023172010000",
        "PropagateID": propagate_id
    }

    return json.dumps(aigc_metadata, ensure_ascii=False)


class RawTextStringObject(TextStringObject):
    """Custom TextStringObject that writes raw text without escaping."""

    def write_to_stream(
            self, stream: StreamType, encryption_key: Union[None, str, bytes] = None
    ) -> None:
        bytearr = self.get_encoded_bytes()
        stream.write(b"(")
        for c_ in iter_unpack("c", bytearr):
            c = cast(bytes, c_[0])
            stream.write(c)
        stream.write(b")")


AIGC_REQUIRED_FIELDS = [
    "Label",
    "ContentProducer",
    "ProduceID",
    "ContentPropagator",
    "PropagateID",
]


def is_aigc_complete(data: dict) -> bool:
    """Return True if all required AIGC fields are present and non-empty."""
    if not isinstance(data, dict):
        return False
    for field in AIGC_REQUIRED_FIELDS:
        if field not in data:
            return False
        value = data[field]
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return False
    return True


def parse_aigc_json(raw: str) -> dict | None:
    """Parse an AIGC JSON string.

    Handles both flat format:
        {"Label": "1", "ProduceID": "...", ...}
    and wrapped format (used in image EXIF):
        {"AIGC": {"Label": "1", "ProduceID": "...", ...}}

    Returns None if parsing fails or input is not a string.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # Handle wrapped format
            if "AIGC" in parsed and isinstance(parsed["AIGC"], dict):
                return parsed["AIGC"]
            return parsed
        return None
    except (json.JSONDecodeError, TypeError):
        return None
