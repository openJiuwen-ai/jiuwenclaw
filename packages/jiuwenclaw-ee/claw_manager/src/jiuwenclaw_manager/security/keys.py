# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manager 侧密钥落库与生命周期。

- 签名密钥对：单例持久化于 ``manager_identity``，私钥永不外发。
- 实例加密公钥：Gateway 握手上交，存 ``instance_enc_pubkey``，解绑时删除。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.logger import get_logger
from jiuwenclaw_manager.infrastructure.utils import utc_now
from jiuwenclaw_manager.models.key_models import (
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
)
from jiuwenclaw_manager.security import crypto_primitives as cp

logger = get_logger(__name__)

_IDENTITY_TABLE = MANAGER_IDENTITY_TABLE_DEF.table_name
_ENC_PUBKEY_TABLE = INSTANCE_ENC_PUBKEY_TABLE_DEF.table_name
_IDENTITY_ID = "default"
SIGN_ALG = "Ed25519"
ENC_ALG = "X25519"


@dataclass(frozen=True)
class ManagerSigningKey:
    key_version: str
    private_raw: bytes
    public_raw: bytes
    fingerprint: str


@dataclass(frozen=True)
class GatewayEncPublicKey:
    jiuwenclaw_id: str
    public_raw: bytes
    fingerprint: str


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, "to_dict"):
        return row.to_dict()
    return None


async def get_or_create_manager_signing_key(handler: DBHandler) -> ManagerSigningKey:
    """加载 Manager 签名密钥对；不存在则生成并持久化（单例，幂等）。"""
    row = _row_to_dict(await handler.get(_IDENTITY_TABLE, {"id": _IDENTITY_ID}))
    if row and row.get("private_key") and row.get("public_key"):
        priv = cp.b64d(row["private_key"])
        pub = cp.b64d(row["public_key"])
        return ManagerSigningKey(
            key_version=str(row.get("key_version") or "v1"),
            private_raw=priv,
            public_raw=pub,
            fingerprint=str(row.get("fingerprint") or cp.fingerprint(pub)),
        )

    priv, pub = cp.ed25519_generate()
    fp = cp.fingerprint(pub)
    now = utc_now()
    await handler.create(
        _IDENTITY_TABLE,
        {
            "id": _IDENTITY_ID,
            "sign_alg": SIGN_ALG,
            "private_key": cp.b64e(priv),
            "public_key": cp.b64e(pub),
            "key_version": "v1",
            "fingerprint": fp,
            "created_at": now,
            "updated_at": now,
        },
    )
    logger.info("[keys] generated manager signing key version=v1 fp=%s", fp[:16])
    return ManagerSigningKey("v1", priv, pub, fp)


async def store_instance_enc_pubkey(
    handler: DBHandler,
    jiuwenclaw_id: str,
    public_key_b64: str,
    *,
    enc_alg: str = ENC_ALG,
    fingerprint: str | None = None,
) -> None:
    """落库/更新某实例的 Gateway 加密公钥（握手时调用）。"""
    pub = cp.b64d(public_key_b64)
    fp = fingerprint or cp.fingerprint(pub)
    now = utc_now()
    existing = await handler.get(_ENC_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    data = {
        "enc_alg": enc_alg,
        "public_key": public_key_b64,
        "fingerprint": fp,
        "status": "bound",
        "updated_at": now,
    }
    if existing is not None:
        await handler.update(_ENC_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id}, data)
    else:
        await handler.create(
            _ENC_PUBKEY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "bound_at": now, **data},
        )
    logger.info("[keys] stored gateway enc pubkey jiuwenclaw_id=%s fp=%s", jiuwenclaw_id, fp[:16])


async def load_instance_enc_pubkey(
    handler: DBHandler, jiuwenclaw_id: str
) -> GatewayEncPublicKey | None:
    """读取某实例的 Gateway 加密公钥；无则返回 None。"""
    row = _row_to_dict(await handler.get(_ENC_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id}))
    if not row or not row.get("public_key") or str(row.get("status")) != "bound":
        return None
    pub = cp.b64d(row["public_key"])
    return GatewayEncPublicKey(jiuwenclaw_id, pub, str(row.get("fingerprint") or cp.fingerprint(pub)))


async def delete_instance_enc_pubkey(handler: DBHandler, jiuwenclaw_id: str) -> None:
    """解绑/删除实例时清除其加密公钥。"""
    await handler.delete(_ENC_PUBKEY_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
