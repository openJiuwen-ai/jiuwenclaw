"""JWT 签名密钥（RS256）：落库自举,生成一次→存身份库→所有副本读同一行。

k8s 多副本天然一致(DB 即共享密钥库,无需 Secret 同步)。私钥仅本服务持有(签发
access JWT);公钥经 ``/v1/auth/public_key`` 暴露,供资源服务器本地验签。

> 落库范式参考 claw_manager 既有的 `get_or_create_manager_signing_key`,但**表/库/
> 算法均独立**(本表 `identity_jwt_signing_key`、RSA PEM 文本),函数名 `get_or_create_
> jwt_signing_key` 以示区分。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_identity.infrastructure.logger import get_logger
from jiuwenclaw_identity.infrastructure.utils import utc_now
from jiuwenclaw_identity.models.identity_models import IDENTITY_JWT_SIGNING_KEY_TABLE_DEF

_log = get_logger(__name__)

_TABLE = IDENTITY_JWT_SIGNING_KEY_TABLE_DEF.table_name
_SINGLETON_ID = "default"
_ALG = "RS256"
_VERSION = "v1"

# 进程内缓存(启动时由 load_signing_key 填充)。
_PRIVATE_PEM: bytes | None = None
_PUBLIC_PEM: bytes | None = None


@dataclass(frozen=True)
class JwtSigningKey:
    key_version: str
    private_pem: bytes
    public_pem: bytes
    fingerprint: str


def _generate_keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def _row_to_key(row: object) -> JwtSigningKey:
    return JwtSigningKey(
        key_version=str(getattr(row, "key_version", _VERSION)),
        private_pem=str(getattr(row, "private_key", "")).encode("utf-8"),
        public_pem=str(getattr(row, "public_key", "")).encode("utf-8"),
        fingerprint=str(getattr(row, "fingerprint", "")),
    )


async def get_or_create_jwt_signing_key(handler: DBHandler) -> JwtSigningKey:
    """读取 JWT 签名密钥;不存在则生成 RSA-2048 并落库(单例,幂等,并发安全)。"""
    existing = await handler.get(_TABLE, {"id": _SINGLETON_ID})
    if existing is not None:
        return _row_to_key(existing)

    priv_pem, pub_pem = _generate_keypair()
    fingerprint = hashlib.sha256(pub_pem).hexdigest()
    now = utc_now()
    try:
        await handler.create(
            _TABLE,
            {
                "id": _SINGLETON_ID,
                "sign_alg": _ALG,
                "private_key": priv_pem.decode("utf-8"),
                "public_key": pub_pem.decode("utf-8"),
                "key_version": _VERSION,
                "fingerprint": fingerprint,
                "created_at": now,
                "updated_at": now,
            },
        )
        _log.info("[jwt] generated & persisted RS256 signing key", version=_VERSION, fp=fingerprint[:16])
    except Exception:  # noqa: BLE001
        # 并发下另一副本可能抢先建好 → 回读返回那一行(保证全副本同一密钥)。
        again = await handler.get(_TABLE, {"id": _SINGLETON_ID})
        if again is not None:
            return _row_to_key(again)
        raise
    return JwtSigningKey(_VERSION, priv_pem, pub_pem, fingerprint)


async def load_signing_key(handler: DBHandler) -> JwtSigningKey:
    """启动时调用:落库自举 + 填充进程缓存。"""
    global _PRIVATE_PEM, _PUBLIC_PEM
    key = await get_or_create_jwt_signing_key(handler)
    _PRIVATE_PEM, _PUBLIC_PEM = key.private_pem, key.public_pem
    _log.info("[jwt] loaded signing key from DB", version=key.key_version, fp=key.fingerprint[:16])
    return key


def private_pem() -> bytes:
    if _PRIVATE_PEM is None:
        raise RuntimeError("JWT signing key not loaded; call load_signing_key(handler) at startup")
    return _PRIVATE_PEM


def public_pem() -> bytes:
    if _PUBLIC_PEM is None:
        raise RuntimeError("JWT signing key not loaded; call load_signing_key(handler) at startup")
    return _PUBLIC_PEM
