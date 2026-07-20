# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""配置下发字段级加密（信封加密）。

每帧随机生成一次性数据密钥 DEK，用 AES-256-GCM 加密登记的敏感字段为信封
``ENC:v1:dek:<wrap>:<base64>``；DEK 再用 Gateway 的 X25519 加密公钥包裹后随
``enc`` 元数据下发。``op``/``template_id`` 等非敏感元数据保持明文。
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from jiuwenclaw_manager.security import crypto_primitives as cp

ENC_PREFIX = "ENC:v1:"
ENC_VERSION = "v1"
ENC_SCHEME = "hybrid"
DEK_ALG = "X25519-HKDF-SHA256+AES-256-GCM"

# section 名 -> 敏感字段路径列表（支持 ``a.b`` 嵌套与 ``a[].b`` 数组通配）。
SENSITIVE_FIELDS: dict[str, list[str]] = {
    "model_templates": ["template.api_key", "template.parameters"],
    "embedding_templates": [
        "template.api_key",
        "templates[].api_key",
        "updates.api_key",
    ],
    "service_config_templates": ["template.kubeconfig"],
    "extension_config_templates": ["template.hook_config", "template.custom_config"],
    "channel_config": ["channel.config"],
    "skill_whitelist_templates": ["template.skill_source"],
}


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def new_dek() -> bytes:
    """生成一次性数据密钥（AES-256）。"""
    return os.urandom(32)


def _walk(node: Any, parts: list[str]) -> list[tuple[dict, str]]:
    """解析字段路径，返回可寻址叶子的 ``(holder_dict, key)`` 列表。"""
    if not parts or not isinstance(node, dict):
        return []
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        key = head[:-2]
        arr = node.get(key)
        out: list[tuple[dict, str]] = []
        if isinstance(arr, list):
            for item in arr:
                if rest and isinstance(item, dict):
                    out.extend(_walk(item, rest))
        return out
    if not rest:
        return [(node, head)] if head in node else []
    child = node.get(head)
    return _walk(child, rest) if isinstance(child, dict) else []


def has_sensitive(section: str, body: dict[str, Any]) -> bool:
    """body 中是否存在登记的、尚未加密的敏感字段。"""
    for path in SENSITIVE_FIELDS.get(section, []):
        for holder, key in _walk(body, path.split(".")):
            value = holder.get(key)
            if value is not None and not is_encrypted(value):
                return True
    return False


def encrypt_sensitive_fields(
    section: str,
    body: dict[str, Any],
    dek: bytes,
) -> tuple[dict[str, Any], list[str]]:
    """用本帧 DEK 对 section body 中登记的敏感字段加密，返回（密文 body, 已加密字段路径）。"""
    paths = SENSITIVE_FIELDS.get(section, [])
    if not paths:
        return body, []
    body = copy.deepcopy(body)
    encrypted: list[str] = []
    for path in paths:
        for holder, key in _walk(body, path.split(".")):
            value = holder.get(key)
            if value is None or is_encrypted(value):
                continue
            if isinstance(value, str):
                wrap, plaintext = "str", value
            else:
                wrap, plaintext = "obj", json.dumps(value, ensure_ascii=False)
            cipher = cp.aes_gcm_encrypt(dek, plaintext)
            holder[key] = f"{ENC_PREFIX}dek:{wrap}:{cipher}"
            encrypted.append(f"{section}.{path}")
    return body, encrypted


def wrap_dek_for_gateway(gateway_public_raw: bytes, dek: bytes) -> tuple[str, str]:
    """用 Gateway 加密公钥包裹 DEK，返回（epk_b64, wrapped_dek_b64）。"""
    epk, wrapped = cp.wrap_dek(gateway_public_raw, dek)
    return cp.b64e(epk), cp.b64e(wrapped)
