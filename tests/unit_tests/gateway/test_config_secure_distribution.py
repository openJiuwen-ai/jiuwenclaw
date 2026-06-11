# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""配置下发非对称加解密 + 加签验签 端到端单元测试。

模拟「握手交换公钥」后的完整下发链路，用**两端真实代码**跑一次往返：

    Manager: 信封加密(DEK+X25519) + Ed25519 加签  ──▶  Manager 真实模块
    Gateway: Ed25519 验签 + 解包 DEK + 字段解密      ──▶  Gateway 真实模块

- Gateway 扩展源码：沿用现有根测试的「合成包」加载法导入 security 模块。
- Manager 源码：把 claw_manager/src 加入 sys.path，导入其 security 模块
  （仅依赖 cryptography，不会拉起 DB / fastapi 等重依赖）。

注：真实的 WebSocket register/register.ack 握手时序由系统测试
``tests/system_tests/enterprise/test_gateway_runtime_e2e.py`` 覆盖；本 UT 聚焦
「握手换出密钥后，整条加解密/加签验签流水线在两端真实代码上跑通」。
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _ext_root() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "packages/jiuwenclaw-ee/gateway/extensions/manager_ws_client"
    )


def _ensure_package(name: str, path: str) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    sys.modules[name] = pkg


def _load_gateway_security():
    """以合成包方式加载 Gateway 扩展的 security 源码模块。"""
    root = _ext_root()
    base = "jiuwenclaw.loaded_extension.manager_ws_client"
    _ensure_package("jiuwenclaw.loaded_extension", str(root.parent.parent.parent))
    _ensure_package(base, str(root))
    _ensure_package(f"{base}.security", str(root / "security"))
    cp = importlib.import_module(f"{base}.security.crypto_primitives")
    fc = importlib.import_module(f"{base}.security.field_crypto")
    fv = importlib.import_module(f"{base}.security.frame_verifier")
    return cp, fc, fv


def _load_manager_security():
    """把 claw_manager/src 加入 sys.path 后导入 Manager 的 security 源码模块。"""
    src = _ext_root().parents[2] / "claw_manager/src"
    if str(src) not in sys.path:
        # 追加到末尾（而非 insert(0)）：jiuwenclaw_manager 名称唯一，不抢优先级、
        # 避免遮蔽同名模块（G.PSL.03）。
        sys.path.append(str(src))
    mcp = importlib.import_module("jiuwenclaw_manager.security.crypto_primitives")
    mfc = importlib.import_module("jiuwenclaw_manager.security.field_crypto")
    mfs = importlib.import_module("jiuwenclaw_manager.security.frame_signer")
    return mcp, mfc, mfs


_GW_CP, _GW_FC, _GW_FV = _load_gateway_security()
_MGR_CP, _MGR_FC, _MGR_FS = _load_manager_security()

_NOW = 1_780_000_000.0


def _ts(offset: float = 0.0) -> str:
    return (
        datetime.fromtimestamp(_NOW + offset, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _body() -> dict:
    return {
        "op": "create",
        "template": {
            "template_id": "tpl-1",
            "api_key": "sk-TOPSECRET",
            "parameters": {"k": "v", "tok": "T"},
            "enabled": True,
        },
    }


@pytest.fixture()
def handshake_keys():
    """模拟握手交换的密钥：Gateway 加密密钥对 + Manager 签名密钥对。"""
    g_enc_priv, g_enc_pub = _MGR_CP.x25519_generate()      # Gateway 生成、公钥交给 Manager
    m_sign_priv, m_sign_pub = _MGR_CP.ed25519_generate()   # Manager 生成、公钥交给 Gateway
    return {
        "g_enc_priv": g_enc_priv,
        "g_enc_pub": g_enc_pub,
        "m_sign_priv": m_sign_priv,
        "m_sign_pub": m_sign_pub,
    }


def _manager_build_frame(keys, *, section="model_templates", body=None,
                         revision="2026-06-01T10:00:00Z", ts=None, nonce="n1"):
    """Manager 真实代码：信封加密 + Ed25519 加签，产出 config.push 帧。"""
    body = _body() if body is None else body
    dek = _MGR_FC.new_dek()
    enc_body, fields = _MGR_FC.encrypt_sensitive_fields(section, body, dek)
    epk_b64, wrapped_b64 = _MGR_FC.wrap_dek_for_gateway(keys["g_enc_pub"], dek)
    enc_meta = {
        "scheme": _MGR_FC.ENC_SCHEME,
        "version": "v1",
        "dek_alg": _MGR_FC.DEK_ALG,
        "gw_key_fp": _MGR_CP.fingerprint(keys["g_enc_pub"]),
        "epk": epk_b64,
        "wrapped_dek": wrapped_b64,
        "fields": fields,
    }
    frame = {
        "type": "config.push",
        "payload": {
            "revision": revision,
            "jiuwenclaw_id": "gw-1",
            "config": {section: enc_body},
            "enc": enc_meta,
        },
    }
    signer = _MGR_FS.Ed25519Signer(keys["m_sign_priv"], "v1")
    _MGR_FS.attach_signature(frame, signer, "v1", ts=ts or _ts(), nonce=nonce)
    return frame


def _gateway_consume(keys, frame, guard, *, required=True, skew=10 ** 9):
    """Gateway 真实代码：验签 → 解包 DEK → 解密，返回 (ok, err, restored_config)。"""
    payload = frame["payload"]
    sig = payload.get("sig")
    verifier = _GW_FV.Ed25519Verifier(keys["m_sign_pub"])
    ok, err = _GW_FV.verify_frame(
        payload, sig, verifier, guard, skew_seconds=skew, required=required, now=_NOW
    )
    if not ok:
        return ok, err, None
    enc = payload["enc"]
    dek = _GW_FC.unwrap_dek(keys["g_enc_priv"], enc["epk"], enc["wrapped_dek"])
    restored = _GW_FC.decrypt_config(payload["config"], dek)
    return True, None, restored


def test_end_to_end_roundtrip_restores_plaintext(handshake_keys):
    frame = _manager_build_frame(handshake_keys, ts=_ts())
    wire = json.dumps(frame, ensure_ascii=False)
    # 链路上无明文敏感值，且确为密文信封 + 签名
    assert "sk-TOPSECRET" not in wire
    assert "ENC:v1:dek:" in wire and '"sig"' in wire

    ok, err, restored = _gateway_consume(handshake_keys, frame, _GW_FV.ReplayGuard(600))
    assert ok and err is None
    tpl = restored["model_templates"]["template"]
    assert tpl["api_key"] == "sk-TOPSECRET"          # 字符串字段还原
    assert tpl["parameters"] == {"k": "v", "tok": "T"}  # 对象字段 json 往返还原
    assert tpl["enabled"] is True                    # 非敏感字段保持明文


def test_tamper_after_sign_rejected_before_decrypt(handshake_keys):
    frame = _manager_build_frame(handshake_keys, ts=_ts())
    frame["payload"]["config"]["model_templates"]["template"]["enabled"] = False  # 篡改
    ok, err, restored = _gateway_consume(handshake_keys, frame, _GW_FV.ReplayGuard(600))
    assert not ok and err == "bad signature" and restored is None


def test_wrong_gateway_privkey_cannot_decrypt(handshake_keys):
    frame = _manager_build_frame(handshake_keys, ts=_ts())
    # 验签用对的 Manager 公钥通过，但用「别的」Gateway 私钥解包 DEK 应失败
    payload = frame["payload"]
    verifier = _GW_FV.Ed25519Verifier(handshake_keys["m_sign_pub"])
    ok, _ = _GW_FV.verify_frame(
        payload, payload["sig"], verifier, _GW_FV.ReplayGuard(600),
        skew_seconds=10 ** 9, required=True, now=_NOW,
    )
    assert ok
    other_priv, _ = _MGR_CP.x25519_generate()
    with pytest.raises(_GW_FC.DecryptError):
        _GW_FC.unwrap_dek(other_priv, payload["enc"]["epk"], payload["enc"]["wrapped_dek"])


def test_replayed_frame_rejected(handshake_keys):
    frame = _manager_build_frame(handshake_keys, ts=_ts(), nonce="dup")
    guard = _GW_FV.ReplayGuard(600)
    ok1, _, _ = _gateway_consume(handshake_keys, frame, guard)
    assert ok1
    ok2, err2, _ = _gateway_consume(handshake_keys, frame, guard)
    assert not ok2 and err2 == "nonce replayed"


def test_stale_revision_rejected(handshake_keys):
    guard = _GW_FV.ReplayGuard(600)
    newer = _manager_build_frame(handshake_keys, revision="2026-06-01T12:00:00Z", ts=_ts(), nonce="a")
    ok1, _, _ = _gateway_consume(handshake_keys, newer, guard)
    assert ok1
    older = _manager_build_frame(handshake_keys, revision="2026-06-01T11:00:00Z", ts=_ts(), nonce="b")
    ok2, err2, _ = _gateway_consume(handshake_keys, older, guard)
    assert not ok2 and err2 == "stale revision"


def test_timestamp_out_of_window_rejected(handshake_keys):
    frame = _manager_build_frame(handshake_keys, ts=_ts(offset=-10_000), nonce="old")
    ok, err, _ = _gateway_consume(handshake_keys, frame, _GW_FV.ReplayGuard(600), skew=300)
    assert not ok and err == "timestamp out of window"


def test_missing_signature_required_vs_optional(handshake_keys):
    frame = _manager_build_frame(handshake_keys, ts=_ts())
    frame["payload"].pop("sig")
    guard = _GW_FV.ReplayGuard(600)
    ok_req, err = _gateway_consume(handshake_keys, frame, guard, required=True)[:2]
    assert not ok_req and err == "missing signature"
    # 非强制态：无签名放行（随后仍可解密）
    verifier = _GW_FV.Ed25519Verifier(handshake_keys["m_sign_pub"])
    ok_opt, _ = _GW_FV.verify_frame(
        frame["payload"], None, verifier, guard, skew_seconds=10 ** 9, required=False, now=_NOW
    )
    assert ok_opt


def test_decrypt_without_dek_fails_closed(handshake_keys):
    frame = _manager_build_frame(handshake_keys, ts=_ts())
    with pytest.raises(_GW_FC.DecryptError):
        _GW_FC.decrypt_config(frame["payload"]["config"], None)
