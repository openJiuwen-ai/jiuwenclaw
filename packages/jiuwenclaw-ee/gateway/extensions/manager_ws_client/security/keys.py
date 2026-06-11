# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 侧密钥落库与生命周期。

- 加密密钥对：单例持久化于 ``gateway_enc_keypair``，私钥永不外发；首启生成。
- Manager 签名公钥：握手分发，存 ``manager_sign_pubkey``，按 jiuwenclaw_id 关联。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..infrastructure.utils import utc_now
from ..models.key_models import (
    GATEWAY_ENC_KEYPAIR_TABLE_DEF,
    MANAGER_SIGN_PUBKEY_TABLE_DEF,
)
from . import crypto_primitives as cp

logger = logging.getLogger(__name__)

_KEYPAIR_TABLE = GATEWAY_ENC_KEYPAIR_TABLE_DEF.table_name
_SIGN_PUBKEY_TABLE = MANAGER_SIGN_PUBKEY_TABLE_DEF.table_name
_KEYPAIR_ID = "default"
ENC_ALG = "X25519"


@dataclass(frozen=True)
class GatewayEncKeypair:
    private_raw: bytes
    public_raw: bytes
    fingerprint: str


@dataclass(frozen=True)
class ManagerSignPublicKey:
    public_raw: bytes
    key_version: str
    fingerprint: str


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, "to_dict"):
        return row.to_dict()
    return None


async def _handler() -> DBHandler:
    from ..core.enterprise_config.gateway_db import GatewayDb

    return await GatewayDb.current().ensure_ready(log_prefix="security_keys")


async def get_or_create_gateway_enc_keypair() -> GatewayEncKeypair:
    """加载 Gateway 加密密钥对；不存在则生成并持久化（单例，幂等）。"""
    handler = await _handler()
    row = _row_to_dict(await handler.get(_KEYPAIR_TABLE, {"id": _KEYPAIR_ID}))
    if row and row.get("private_key") and row.get("public_key"):
        priv = cp.b64d(row["private_key"])
        pub = cp.b64d(row["public_key"])
        return GatewayEncKeypair(priv, pub, str(row.get("fingerprint") or cp.fingerprint(pub)))

    priv, pub = cp.x25519_generate()
    fp = cp.fingerprint(pub)
    now = utc_now()
    await handler.create(
        _KEYPAIR_TABLE,
        {
            "id": _KEYPAIR_ID,
            "enc_alg": ENC_ALG,
            "private_key": cp.b64e(priv),
            "public_key": cp.b64e(pub),
            "fingerprint": fp,
            "created_at": now,
            "updated_at": now,
        },
    )
    logger.info("[keys] generated gateway enc keypair fp=%s", fp[:16])
    return GatewayEncKeypair(priv, pub, fp)


async def load_gateway_enc_privkey_by_fp(fingerprint: str | None) -> bytes | None:
    """按指纹返回本机加密私钥；当前为单例，指纹不匹配则返回 None。"""
    kp = await get_or_create_gateway_enc_keypair()
    if fingerprint and fingerprint != kp.fingerprint:
        return None
    return kp.private_raw


async def store_manager_sign_pubkey(
    jiuwenclaw_id: str,
    public_key_b64: str,
    *,
    key_version: str,
    manager_id: str = "default",
    sign_alg: str = "Ed25519",
    fingerprint: str | None = None,
) -> None:
    """落库/更新 Manager 签名公钥（握手 register.ack 时调用，即“确认配对”）。"""
    handler = await _handler()
    pub = cp.b64d(public_key_b64)
    fp = fingerprint or cp.fingerprint(pub)
    now = utc_now()
    existing = await handler.get(_SIGN_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    data = {
        "manager_id": manager_id,
        "sign_alg": sign_alg,
        "public_key": public_key_b64,
        "key_version": key_version,
        "fingerprint": fp,
        "status": "bound",
        "updated_at": now,
    }
    if existing is not None:
        await handler.update(_SIGN_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id}, data)
    else:
        await handler.create(
            _SIGN_PUBKEY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "bound_at": now, **data},
        )
    logger.info(
        "[keys] bound manager sign pubkey jiuwenclaw_id=%s version=%s fp=%s",
        jiuwenclaw_id,
        key_version,
        fp[:16],
    )


async def load_manager_sign_pubkey(
    jiuwenclaw_id: str, key_version: str | None = None
) -> ManagerSignPublicKey | None:
    """读取 Manager 签名公钥；指定版本时需匹配，否则返回 None。"""
    handler = await _handler()
    row = _row_to_dict(await handler.get(_SIGN_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id}))
    if not row or not row.get("public_key") or str(row.get("status")) != "bound":
        return None
    stored_version = str(row.get("key_version") or "")
    if key_version and key_version != stored_version:
        return None
    pub = cp.b64d(row["public_key"])
    return ManagerSignPublicKey(pub, stored_version, str(row.get("fingerprint") or cp.fingerprint(pub)))


async def delete_manager_sign_pubkey(jiuwenclaw_id: str) -> None:
    """解绑时清除该实例的 Manager 签名公钥。"""
    handler = await _handler()
    await handler.delete(_SIGN_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
