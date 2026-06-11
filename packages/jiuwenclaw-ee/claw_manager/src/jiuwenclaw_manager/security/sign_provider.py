# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manager 签名 Provider：持有启动时从 ``manager_identity`` 加载的 Ed25519 私钥。"""

from __future__ import annotations

from jiuwenclaw_manager.security.frame_signer import Ed25519Signer, SignatureProvider
from jiuwenclaw_manager.security.keys import ManagerSigningKey

_key: ManagerSigningKey | None = None
_signer: SignatureProvider | None = None


def set_manager_signing_key(key: ManagerSigningKey | None) -> None:
    """启动时（加载/生成密钥后）注入签名 Provider。"""
    global _key, _signer
    _key = key
    _signer = None if key is None else Ed25519Signer(key.private_raw, key.key_version)


def get_config_signature_provider() -> SignatureProvider | None:
    """返回当前签名 Provider；未初始化时为 None。"""
    return _signer


def get_manager_signing_key() -> ManagerSigningKey | None:
    """返回当前 Manager 签名密钥（含公钥/版本/指纹），用于握手下发公钥。"""
    return _key
