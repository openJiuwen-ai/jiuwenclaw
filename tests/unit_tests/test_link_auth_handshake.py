# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""控制链路握手鉴权（link-auth）单元测试 —— 聚焦 manager_ws_server 的握手校验胶水
``check_handshake`` 与 connection.ack 反向令牌逻辑（非对称 Ed25519 + TOFU 指纹固定）。

Runtime 的 ``foundation/tests/unit_tests/test_link_auth.py`` 已覆盖纯密码学（验签/nonce/
mode/指纹）；本 UT 在其之上补两块**集成胶水**的真实代码：

1. ``check_handshake``：从 websockets 的 ``process_request`` 入参里取头、用
   ``verify_and_pin`` 按 ``CLAW_LINK_AUTH_MODE`` 决定放行/拒绝（含 TOFU 指纹固定），并
   兼容 legacy ``(path, headers)`` 与 new ``(connection, request)`` 两种入参形态。
2. **反向令牌**：Manager 在 connection.ack 里出示、由对端 ``verify_token`` 反向核验的
   往返（防 MITM/冒充）。

加载方式：``handshake_auth`` 自包含（仅依赖 link_auth + websockets，无包内相对
导入），故用 importlib 按文件直接加载，绕开 ``jiuwenclaw_manager`` 包的重依赖
（fastapi / sqlalchemy / DB）。
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDSHAKE_AUTH_FILE = (
    REPO_ROOT
    / "packages/jiuwenclaw-ee/claw_manager/src/jiuwenclaw_manager"
    / "manager_ws_server/handshake_auth.py"
)

# link-auth 已收敛进 runtime（foundation 层）。用 import_module 而非 import 语句：
# 模块级动态导入，既不触发 E402，也不引入函数内嵌套 import。
cla = importlib.import_module("openjiuwen_runtime.foundation.security.link_auth")


def _load_handshake_auth():
    """按文件路径直接加载 handshake_auth，避免触发 jiuwenclaw_manager 包的重依赖。"""
    spec = importlib.util.spec_from_file_location(
        "_link_auth_handshake_under_test", _HANDSHAKE_AUTH_FILE
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


ha = _load_handshake_auth()

# 测试用身份密钥对：gateway（连接发起方）、manager（反向令牌签发方）、attacker（冒充方）。
GW_PRIV, GW_PUB = cla.generate_keypair()
MGR_PRIV, MGR_PUB = cla.generate_keypair()
ATK_PRIV, ATK_PUB = cla.generate_keypair()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例从干净的 link-auth 环境起步。"""
    for var in (
        "CLAW_LINK_AUTH_MODE",
        "CLAW_LINK_TOKEN_TTL",
    ):
        monkeypatch.delenv(var, raising=False)


def _legacy_args(headers: dict | None) -> tuple:
    """websockets legacy 形态：``(path, headers)``。"""
    return ("/ws", headers or {})


def _new_args(headers: dict | None) -> tuple:
    """websockets new 形态：``(connection, request)``，request 暴露 ``.headers``。"""
    request = SimpleNamespace(headers=headers or {})
    connection = SimpleNamespace()
    return (connection, request)


def _gateway_header(
    monkeypatch, *, mode="enforce", priv=GW_PRIV, pub=GW_PUB, service_id="gw-1"
) -> dict:
    """以 gateway 身份现签一枚握手头（mode=off 时返回 {}）。"""
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", mode)
    return cla.build_token_header(
        service_id=service_id, service_type="gateway", private_b64=priv, public_b64=pub
    )


# --------------------------------------------------------------------------
# check_handshake —— 开关短路
# --------------------------------------------------------------------------

def test_off_mode_allows_without_token(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "off")
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    assert ha.check_handshake(_legacy_args({}), cache, store) is None


def test_observe_mode_allows_without_token_but_not_ok(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "observe")
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    # observe：无令牌也放行（check_handshake 返回 None），仅记日志。
    assert ha.check_handshake(_legacy_args({}), cache, store) is None
    # 底层 verify_and_pin 应给出 ok=False（放行但未真正通过）。
    res = cla.verify_and_pin(store, None, expect_type="gateway", nonce_cache=cache)
    assert res.allowed is True and res.ok is False


def test_observe_mode_allows_bad_token_but_not_ok(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "observe")
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    bad = {cla.LINK_TOKEN_HEADER: "not-a-real-token"}
    assert ha.check_handshake(_legacy_args(bad), cache, store) is None


# --------------------------------------------------------------------------
# check_handshake —— enforce 放行 / 拒绝
# --------------------------------------------------------------------------

def test_enforce_valid_gateway_token_legacy_args(monkeypatch):
    headers = _gateway_header(monkeypatch)
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    assert ha.check_handshake(_legacy_args(headers), cache, store) is None


def test_enforce_valid_gateway_token_new_args(monkeypatch):
    headers = _gateway_header(monkeypatch)
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    assert ha.check_handshake(_new_args(headers), cache, store) is None


def test_enforce_missing_token_rejected(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "enforce")
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    rejected = ha.check_handshake(_legacy_args({}), cache, store)
    assert rejected is not None
    response, reason = rejected
    assert response is not None
    assert "missing" in reason.lower()


def test_enforce_bad_signature_rejected(monkeypatch):
    # 用 attacker 私钥签、却把 gateway 的公钥塞进令牌 → 验签不过（持令牌者不握有该公钥私钥）。
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "enforce")
    tampered = cla.sign_token(
        service_id="gw-1", service_type="gateway", private_b64=ATK_PRIV, public_b64=GW_PUB
    )
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    rejected = ha.check_handshake(
        _legacy_args({cla.LINK_TOKEN_HEADER: tampered}), cache, store
    )
    assert rejected is not None
    assert "signature" in rejected[1].lower()


def test_enforce_wrong_service_type_rejected(monkeypatch):
    # 令牌 typ=agent_server，但本链路要求 gateway → 拒（防跨链路令牌复用）。
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "enforce")
    tok = cla.sign_token(
        service_id="as-1",
        service_type="agent_server",
        private_b64=ATK_PRIV,
        public_b64=ATK_PUB,
    )
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    rejected = ha.check_handshake(_legacy_args({cla.LINK_TOKEN_HEADER: tok}), cache, store)
    assert rejected is not None


def test_enforce_nonce_replay_rejected(monkeypatch):
    # 同一令牌第二次出现 = 重放 → 拒。
    headers = _gateway_header(monkeypatch)
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()
    assert ha.check_handshake(_legacy_args(headers), cache, store) is None  # 首次放行
    rejected = ha.check_handshake(_legacy_args(headers), cache, store)  # 重放
    assert rejected is not None
    assert "replay" in rejected[1].lower()


def test_enforce_tofu_fingerprint_mismatch_rejected(monkeypatch):
    # TOFU：同一 iss 第一次用 gateway 密钥对（被记录指纹），第二次换 attacker 密钥对冒充
    # → 指纹不匹配，拒。
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "enforce")
    cache = cla.NonceCache()
    store = cla.InMemoryPinStore()

    first = _gateway_header(monkeypatch)  # gw-1 / GW_PUB
    assert ha.check_handshake(_legacy_args(first), cache, store) is None

    # 同 iss=gw-1，但换成 attacker 密钥对（冒充）。
    impostor = cla.build_token_header(
        service_id="gw-1", service_type="gateway", private_b64=ATK_PRIV, public_b64=ATK_PUB
    )
    rejected = ha.check_handshake(_legacy_args(impostor), cache, store)
    assert rejected is not None
    assert "fingerprint" in rejected[1].lower()


# --------------------------------------------------------------------------
# 反向令牌（connection.ack）：Manager 出示、对端反向核验
# --------------------------------------------------------------------------

def test_reverse_manager_token_roundtrip(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "enforce")
    # Manager 在 connection.ack 里附自己签的令牌。
    tok = cla.build_token(
        service_id="mgr-1", service_type="manager", private_b64=MGR_PRIV, public_b64=MGR_PUB
    )
    assert tok is not None
    # Gateway 反向核验：期望对端类型为 manager。
    res = cla.verify_token(tok, expect_type="manager")
    assert res.allowed is True and res.ok is True


def test_reverse_token_wrong_peer_type_rejected(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "enforce")
    # 对端自称 gateway，但我方期望 manager → 反向核验不过（防冒充对端身份）。
    tok = cla.build_token(
        service_id="x", service_type="gateway", private_b64=MGR_PRIV, public_b64=MGR_PUB
    )
    res = cla.verify_token(tok, expect_type="manager")
    assert res.allowed is False
