# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 侧配置下发验签（Ed25519）与防重放。

校验 config.push 帧 payload 中的 ``sig`` 块：算法白名单 → 时间窗 → nonce 去重
→ revision 单调 → Ed25519 验签（用握手分发的 Manager 公钥）。任一不过即拒绝
（fail-closed）。验签 Provider 由调用方按 ``sig.key_id``（版本）从库中取公钥构造。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from . import crypto_primitives as cp

ALG_ED25519 = "Ed25519"
ALLOWED_ALGS = frozenset({ALG_ED25519})


class Ed25519Verifier:
    """验签 Provider：持 Manager 签名公钥。"""

    alg = ALG_ED25519

    def __init__(self, public_raw: bytes) -> None:
        self._pub = public_raw

    def verify(self, data: bytes, signature: bytes) -> bool:
        return cp.ed25519_verify(self._pub, data, signature)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _build_signing_string(payload: dict[str, Any], sig: dict[str, Any]) -> str:
    return canonical_json(
        {
            "revision": payload.get("revision"),
            "jiuwenclaw_id": payload.get("jiuwenclaw_id"),
            "config": payload.get("config"),
            "alg": sig.get("alg"),
            "key_id": sig.get("key_id"),
            "ts": sig.get("ts"),
            "nonce": sig.get("nonce"),
        }
    )


def _parse_ts(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class ReplayGuard:
    """防重放状态：nonce TTL 去重集合 + 每实例 revision 单调。"""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}
        self._last_revision: dict[str, str] = {}

    def _evict(self, now: float) -> None:
        expired = [n for n, exp in self._seen.items() if exp <= now]
        for n in expired:
            self._seen.pop(n, None)

    def nonce_seen(self, nonce: str, now: float) -> bool:
        self._evict(now)
        return nonce in self._seen

    def revision_is_stale(self, jiuwenclaw_id: str, revision: str) -> bool:
        last = self._last_revision.get(jiuwenclaw_id)
        return last is not None and revision <= last

    def commit(self, jiuwenclaw_id: str, revision: str, nonce: str, now: float) -> None:
        self._seen[nonce] = now + self._ttl
        self._last_revision[jiuwenclaw_id] = revision


def verify_frame(
    payload: dict[str, Any],
    sig: dict[str, Any] | None,
    verifier: Any,
    guard: ReplayGuard,
    *,
    skew_seconds: float,
    required: bool,
    now: float | None = None,
) -> tuple[bool, str | None]:
    """校验 config.push 帧；返回 ``(ok, error)``。校验通过才提交防重放状态。"""
    if sig is None:
        return (False, "missing signature") if required else (True, None)
    if not isinstance(sig, dict):
        return False, "invalid signature block"
    if verifier is None:
        return False, "no manager signing pubkey"

    now = time.time() if now is None else now
    jiuwenclaw_id = str(payload.get("jiuwenclaw_id") or "")
    revision = str(payload.get("revision") or "")

    # 1. 算法白名单
    if sig.get("alg") not in ALLOWED_ALGS:
        return False, f"alg not allowed: {sig.get('alg')!r}"
    # 2. 时间窗
    try:
        ts = _parse_ts(str(sig.get("ts") or ""))
    except (ValueError, TypeError):
        return False, "invalid timestamp"
    if abs(now - ts) > skew_seconds:
        return False, "timestamp out of window"
    # 3. nonce 去重
    nonce = str(sig.get("nonce") or "")
    if not nonce:
        return False, "missing nonce"
    if guard.nonce_seen(nonce, now):
        return False, "nonce replayed"
    # 4. revision 单调
    if guard.revision_is_stale(jiuwenclaw_id, revision):
        return False, "stale revision"
    # 5. 密码学验签（最后做）
    signing = _build_signing_string(payload, sig)
    try:
        signature = cp.b64d(str(sig.get("value") or ""))
    except ValueError:
        return False, "invalid signature encoding"
    if not verifier.verify(signing.encode("utf-8"), signature):
        return False, "bad signature"

    guard.commit(jiuwenclaw_id, revision, nonce, now)
    return True, None
