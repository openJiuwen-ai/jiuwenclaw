# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 侧配置下发字段级解密（信封解密）。

从 ``enc`` 元数据中用本机 X25519 私钥解出一次性 DEK，再用 DEK(AES-256-GCM)
还原各 ``ENC:v1:dek:...`` 信封字段，交由各 section handler 写入本地库。
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.exceptions import InvalidTag

from . import crypto_primitives as cp

# 解密链路可能抛出的异常：base64/格式问题(ValueError，含 binascii.Error)、
# AES-GCM 认证失败(InvalidTag)。统一收敛为 DecryptError。
_DECRYPT_ERRORS = (ValueError, InvalidTag)

ENC_PREFIX = "ENC:v1:"


class DecryptError(ValueError):
    """字段解密/解包失败（统一为 ValueError 子类，便于窄异常捕获、fail-closed）。"""


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def unwrap_dek(private_raw: bytes, epk_b64: str, wrapped_dek_b64: str) -> bytes:
    """用本机加密私钥与临时公钥还原 DEK。"""
    try:
        return cp.unwrap_dek(private_raw, cp.b64d(epk_b64), cp.b64d(wrapped_dek_b64))
    except _DECRYPT_ERRORS as exc:
        raise DecryptError(f"unwrap dek failed: {exc}") from exc


def decrypt_config(config: Any, dek: bytes | None) -> Any:
    """递归还原 config 中所有 ``ENC:`` 信封值；遇密文却无 DEK 则抛 DecryptError。"""

    def restore(value: Any) -> Any:
        if is_encrypted(value):
            if dek is None:
                raise DecryptError("ciphertext received but DEK unavailable")
            _, _, _ref, wrap, b64 = value.split(":", 4)
            try:
                plaintext = cp.aes_gcm_decrypt(dek, b64)
            except _DECRYPT_ERRORS as exc:
                raise DecryptError(f"field decrypt failed: {exc}") from exc
            return json.loads(plaintext) if wrap == "obj" else plaintext
        if isinstance(value, dict):
            return {k: restore(v) for k, v in value.items()}
        if isinstance(value, list):
            return [restore(v) for v in value]
        return value

    return restore(config)
