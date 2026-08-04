# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""配置下发信封加密（DEK + X25519 包裹）单元测试。"""

from __future__ import annotations

import json

import pytest

from jiuwenclaw_manager.security import crypto_primitives as cp
from jiuwenclaw_manager.security.field_crypto import (
    ENC_PREFIX,
    encrypt_sensitive_fields,
    has_sensitive,
    is_encrypted,
    new_dek,
    wrap_dek_for_gateway,
)


def _model_body() -> dict:
    return {
        "op": "create",
        "template": {
            "template_id": "tpl-1",
            "api_key": "sk-SECRET",
            "parameters": {"k": "v", "tok": "T"},
            "enabled": True,
        },
    }


def _decrypt_with_dek(value, dek):
    if isinstance(value, str) and value.startswith(ENC_PREFIX):
        _, _, _ref, wrap, b64 = value.split(":", 4)
        pt = cp.aes_gcm_decrypt(dek, b64)
        return json.loads(pt) if wrap == "obj" else pt
    if isinstance(value, dict):
        return {k: _decrypt_with_dek(v, dek) for k, v in value.items()}
    return value


def test_roundtrip_with_dek_and_envelope_wrap():
    dek = new_dek()
    enc_body, fields = encrypt_sensitive_fields("model_templates", _model_body(), dek)
    assert set(fields) == {
        "model_templates.template.api_key",
        "model_templates.template.parameters",
    }
    assert enc_body["template"]["api_key"].startswith(ENC_PREFIX)
    assert is_encrypted(enc_body["template"]["parameters"])
    assert enc_body["template"]["enabled"] is True  # 非敏感保持明文

    # DEK 用 Gateway 公钥包裹 → 用对应私钥解包 → 还原同一 DEK
    priv, pub = cp.x25519_generate()
    epk_b64, wrapped_b64 = wrap_dek_for_gateway(pub, dek)
    dek2 = cp.unwrap_dek(priv, cp.b64d(epk_b64), cp.b64d(wrapped_b64))
    assert dek2 == dek

    restored = _decrypt_with_dek(enc_body, dek2)
    assert restored["template"]["api_key"] == "sk-SECRET"
    assert restored["template"]["parameters"] == {"k": "v", "tok": "T"}


def test_encrypt_is_idempotent():
    dek = new_dek()
    enc_body, _ = encrypt_sensitive_fields("model_templates", _model_body(), dek)
    enc_again, fields = encrypt_sensitive_fields("model_templates", enc_body, dek)
    assert fields == []
    assert enc_again["template"]["api_key"] == enc_body["template"]["api_key"]


def test_has_sensitive():
    dek = new_dek()
    body = _model_body()
    assert has_sensitive("model_templates", body) is True
    enc_body, _ = encrypt_sensitive_fields("model_templates", body, dek)
    assert has_sensitive("model_templates", enc_body) is False
    assert has_sensitive("unknown_section", body) is False


@pytest.mark.parametrize("container", ["template", "templates", "updates"])
def test_embedding_template_sensitive_fields(container):
    values = {
        "api_key": "sk-embedding",
        "parameters": {"dimensions": 1024},
        "client_config": {"timeout": 60},
    }
    body = {"op": "sync" if container == "templates" else "update"}
    body[container] = [values] if container == "templates" else values

    encrypted, fields = encrypt_sensitive_fields(
        "embedding_templates", body, new_dek()
    )
    target = encrypted[container][0] if container == "templates" else encrypted[container]
    assert len(fields) == 1
    assert is_encrypted(target["api_key"])
    assert target["parameters"] == values["parameters"]
    assert target["client_config"] == values["client_config"]


def test_unregistered_section_passthrough():
    body = {"op": "create", "mapping": {"scope_type": "user", "scope_id": "u1"}}
    enc_body, fields = encrypt_sensitive_fields(
        "config_default_template_mappings", body, new_dek()
    )
    assert fields == []
    assert enc_body == body


def test_wrong_key_cannot_unwrap_dek():
    dek = new_dek()
    _, pub = cp.x25519_generate()
    epk_b64, wrapped_b64 = wrap_dek_for_gateway(pub, dek)
    wrong_priv, _ = cp.x25519_generate()
    with pytest.raises(Exception):
        cp.unwrap_dek(wrong_priv, cp.b64d(epk_b64), cp.b64d(wrapped_b64))


def test_ed25519_sign_verify_roundtrip():
    priv, pub = cp.ed25519_generate()
    sig = cp.ed25519_sign(priv, b"hello")
    assert cp.ed25519_verify(pub, b"hello", sig) is True
    assert cp.ed25519_verify(pub, b"tampered", sig) is False
    _, other_pub = cp.ed25519_generate()
    assert cp.ed25519_verify(other_pub, b"hello", sig) is False
