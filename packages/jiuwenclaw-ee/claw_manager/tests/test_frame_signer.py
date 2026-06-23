# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""配置下发加签（Ed25519）单元测试。"""

from __future__ import annotations

import pytest

from jiuwenclaw_manager.security import crypto_primitives as cp
from jiuwenclaw_manager.security.frame_signer import (
    ALG_ED25519,
    Ed25519Signer,
    attach_signature,
    build_signing_string,
    canonical_json,
)


def _frame() -> dict:
    return {
        "type": "config.push",
        "payload": {
            "revision": "2026-06-01T10:00:00Z",
            "jiuwenclaw_id": "gw-1",
            "config": {"model_templates": {"op": "create", "template": {"api_key": "sk"}}},
        },
    }


def _verify(frame, public_raw) -> bool:
    sig = frame["payload"]["sig"]
    signing = build_signing_string(
        revision=frame["payload"]["revision"],
        jiuwenclaw_id=frame["payload"]["jiuwenclaw_id"],
        config=frame["payload"]["config"],
        alg=sig["alg"],
        key_id=sig["key_id"],
        ts=sig["ts"],
        nonce=sig["nonce"],
    )
    return cp.ed25519_verify(public_raw, signing.encode("utf-8"), cp.b64d(sig["value"]))


def test_canonical_json_is_deterministic():
    a = canonical_json({"b": 1, "a": {"y": 2, "x": 1}})
    b = canonical_json({"a": {"x": 1, "y": 2}, "b": 1})
    assert a == b == '{"a":{"x":1,"y":2},"b":1}'


def test_attach_signature_fields_and_alg():
    priv, _ = cp.ed25519_generate()
    frame = attach_signature(_frame(), Ed25519Signer(priv, "v1"), "v1")
    sig = frame["payload"]["sig"]
    assert sig["alg"] == ALG_ED25519
    assert sig["key_id"] == "v1"
    assert sig["ts"] and sig["nonce"] and sig["value"]


def test_signature_roundtrip_valid():
    priv, pub = cp.ed25519_generate()
    frame = attach_signature(_frame(), Ed25519Signer(priv, "v1"), "v1")
    assert _verify(frame, pub) is True


def test_tamper_breaks_signature():
    priv, pub = cp.ed25519_generate()
    frame = attach_signature(_frame(), Ed25519Signer(priv, "v1"), "v1")
    frame["payload"]["config"]["model_templates"]["template"]["api_key"] = "EVIL"
    assert _verify(frame, pub) is False


def test_wrong_key_fails():
    priv, _ = cp.ed25519_generate()
    _, other_pub = cp.ed25519_generate()
    frame = attach_signature(_frame(), Ed25519Signer(priv, "v1"), "v1")
    assert _verify(frame, other_pub) is False


def test_key_id_must_not_contain_colon():
    priv, _ = cp.ed25519_generate()
    with pytest.raises(ValueError):
        attach_signature(_frame(), Ed25519Signer(priv, "v1"), "bad:id")
