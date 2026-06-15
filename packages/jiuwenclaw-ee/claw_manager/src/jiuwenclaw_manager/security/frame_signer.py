# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""配置下发加签：为 config.push 帧附加 Ed25519 签名。

签名覆盖 ``revision``、``jiuwenclaw_id``、``config`` 及签名元数据
（``alg``/``key_id``/``ts``/``nonce``），经确定性 JSON 规范化后由 Manager
的 Ed25519 私钥计算；Gateway 用握手分发的 Manager 公钥验签。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from jiuwenclaw_manager.security import crypto_primitives as cp

ALG_ED25519 = "Ed25519"


@runtime_checkable
class SignatureProvider(Protocol):
    alg: str
    key_version: str

    def sign(self, data: bytes) -> bytes:
        ...


class Ed25519Signer:
    """默认签名 Provider：Ed25519（持 Manager 私钥）。"""

    alg = ALG_ED25519

    def __init__(self, private_raw: bytes, key_version: str = "v1") -> None:
        self._priv = private_raw
        self.key_version = key_version

    def sign(self, data: bytes) -> bytes:
        return cp.ed25519_sign(self._priv, data)


def canonical_json(obj: Any) -> str:
    """确定性 JSON 序列化：键排序、无多余空白、保留非 ASCII。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_signing_string(
    *,
    revision: str,
    jiuwenclaw_id: str,
    config: Any,
    alg: str,
    key_id: str,
    ts: str,
    nonce: str,
) -> str:
    """构造待签名字符串（签名与验签两侧必须逐字节一致）。"""
    return canonical_json(
        {
            "revision": revision,
            "jiuwenclaw_id": jiuwenclaw_id,
            "config": config,
            "alg": alg,
            "key_id": key_id,
            "ts": ts,
            "nonce": nonce,
        }
    )


def _utc_now_iso_ms() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def attach_signature(
    frame: dict[str, Any],
    signer: SignatureProvider,
    key_id: str,
    *,
    ts: str | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """就地为 config.push 帧的 payload 附加 ``sig`` 块并返回该帧。``key_id`` 为公钥版本。"""
    if ":" in key_id:
        raise ValueError("key_id must not contain ':'")
    payload = frame["payload"]
    ts = ts or _utc_now_iso_ms()
    nonce = nonce or secrets.token_hex(16)
    signing = build_signing_string(
        revision=payload["revision"],
        jiuwenclaw_id=payload["jiuwenclaw_id"],
        config=payload["config"],
        alg=signer.alg,
        key_id=key_id,
        ts=ts,
        nonce=nonce,
    )
    payload["sig"] = {
        "alg": signer.alg,
        "key_id": key_id,
        "ts": ts,
        "nonce": nonce,
        "value": cp.b64e(signer.sign(signing.encode("utf-8"))),
    }
    return frame
