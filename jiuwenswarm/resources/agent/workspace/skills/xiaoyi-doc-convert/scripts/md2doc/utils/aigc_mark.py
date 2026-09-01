"""AIGC mark utilities for md2doc skill.

Provides local AIGC signature generation without HTTP dependencies.
"""
import hashlib
import json


def generate_sha256(text: str) -> str:
    """计算字符串的SHA256哈希值"""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(text.encode('utf-8'))
    return sha256_hash.hexdigest()


def get_aigc_signature(content: str, request_id: str = "") -> str:
    """
    生成AIGC隐式标识

    Args:
        content: 内容文本
        request_id: 请求ID（可选）

    Returns:
        JSON格式的AIGC标识字符串
    """
    # 使用内容生成唯一ID
    content_hash = generate_sha256(content)[:16]
    produce_id = f"voiceassistant-{content_hash}"
    propagate_id = f"voiceassistant-{content_hash}"

    aigc_metadata = {
        "Label": "1",
        "ContentProducer": "001191320114777023172010000",
        "ProduceID": produce_id,
        "ReservedCode1": "",
        "ContentPropagator": "001191320114777023172010000",
        "PropagateID": propagate_id
    }

    return json.dumps(aigc_metadata, ensure_ascii=False)
