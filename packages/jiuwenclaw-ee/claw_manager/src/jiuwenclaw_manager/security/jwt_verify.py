"""资源服务器侧 JWT 验签：从认证服务拉 RS256 公钥(缓存),本地校验 access JWT。

claw_manager 不再签发 token,只验证 jiuwenclaw_identity 签发的 JWT 并读取 claims
（sub / is_admin / groups）。公钥首次使用时拉取并缓存；拉取走 httpx trust_env=False，
不读环境代理（与本仓库其它本机调用一致）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import jwt

from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.infrastructure.logger import get_logger

_log = get_logger(__name__)
_ALG = "RS256"

_public_pem: bytes | None = None
_lock = asyncio.Lock()


async def _fetch_public_pem() -> bytes:
    last_exc: Exception | None = None
    async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
        for attempt in range(1, 6):
            try:
                resp = await client.get(settings.identity_public_key_url)
                resp.raise_for_status()
                return resp.content
            except Exception as e:  # noqa: BLE001
                last_exc = e
                _log.warning("[jwt] fetch public key failed", attempt=attempt, err=str(e))
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(f"cannot fetch identity public key: {last_exc}")


async def get_public_pem(force: bool = False) -> bytes:
    global _public_pem
    if _public_pem is not None and not force:
        return _public_pem
    async with _lock:
        if _public_pem is None or force:
            _public_pem = await _fetch_public_pem()
            _log.info("[jwt] loaded identity public key", url=settings.identity_public_key_url)
    return _public_pem


async def decode_token(token: str) -> dict[str, Any]:
    """验签 + 校验 iss/aud/exp；失败抛 jwt.PyJWTError。返回 claims。

    若用 缓存公钥 解码失败（可能认证服务轮换了密钥），强制刷新一次再试。
    """
    pem = await get_public_pem()
    try:
        return jwt.decode(token, pem, algorithms=[_ALG], audience=settings.jwt_audience, issuer=settings.jwt_issuer)
    except jwt.InvalidSignatureError:
        pem = await get_public_pem(force=True)
        return jwt.decode(token, pem, algorithms=[_ALG], audience=settings.jwt_audience, issuer=settings.jwt_issuer)
