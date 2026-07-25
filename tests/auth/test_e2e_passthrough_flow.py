"""
端到端调测：模拟从 gateway.yaml 加载配置到认证的完整流程

服务启动 → registry.py → app_gateway.py → web_connect.py → authenticate()
使用 PassthroughAuthenticator
"""
import asyncio
import os
import sys
import yaml

# ── 在导入任何 jiuwenswarm 模块之前，先 mock 掉循环导入 ──
from unittest.mock import MagicMock

import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

import jiuwenswarm.gateway.channel_manager.web.web_connect
jiuwenswarm.gateway.channel_manager.web.web_connect.get_auth_handler = MagicMock()

# 然后再导入测试目标
from jiuwenswarm.gateway.auth.passthrough_authenticator import PassthroughAuthenticator
from jiuwenswarm.gateway.auth.credential_authenticator import AuthContext


def load_auth_config():
    """模拟 registry.py 加载 gateway.yaml 的过程"""
    _gateway_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "gateway.yaml"
    )
    print(f"[1] 查找 gateway.yaml: {os.path.abspath(_gateway_path)}")
    print(f"    文件存在: {os.path.exists(_gateway_path)}")

    if os.path.exists(_gateway_path):
        with open(_gateway_path, "r", encoding="utf-8") as f:
            _gateway_cfg = yaml.safe_load(f) or {}
        config = _gateway_cfg.get("extensions", {})
        print(f"[2] 加载配置: {config}")
        return config
    return {}


async def test_full_flow():
    print("=" * 60)
    print("端到端认证流程调测（PassthroughAuthenticator）")
    print("=" * 60)

    # ── 步骤 1: 加载配置（模拟 registry.py） ──
    config = load_auth_config()
    auth_config = config.get("auth", {})
    auth_type = auth_config.get("type", "passthrough")
    print(f"[3] 认证类型: {auth_type}")

    # ── 步骤 2: 创建认证器（模拟 registry.py 注册认证器） ──
    auth = PassthroughAuthenticator()
    print("[4] PassthroughAuthenticator 创建成功")

    # ── 步骤 3: 模拟 app_gateway.py 缓存认证器 ──
    _auth_handler = auth
    print("[5] 认证器已缓存到 _auth_handler")

    # ── 步骤 4: 模拟 web_connect.py 的 _handle_connect ──
    print("\n" + "=" * 60)
    print("模拟 WebSocket 连接认证（web_connect.py → authenticate()）")
    print("=" * 60)

    # 模拟 extract_token(ws)
    extracted_token = "test-token-123"
    print(f"[6] extract_token: 提取到 Token")

    # 模拟 extract_headers(ws)
    extracted_headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Forwarded-For": "192.168.1.100",
    }
    print(f"[7] extract_headers: 提取到 {len(extracted_headers)} 个请求头")

    # 模拟 get_remote_addr(ws)
    remote_addr = "192.168.1.100"
    print(f"[8] get_remote_addr: {remote_addr}")

    # 构造 AuthContext
    context = AuthContext(
        channel_type="web",
        credentials={"token": extracted_token},
        headers=extracted_headers,
        remote_addr=remote_addr,
    )
    print(f"[9] AuthContext 构造完成")

    # 调用 authenticate
    result = await _auth_handler.authenticate(context)

    print(f"\n[10] 认证结果:")
    print(f"    success: {result.success}")
    print(f"    user_id: {result.user_id}")
    print(f"    error: {result.error}")
    print(f"    extensions: {result.extensions}")

    # ── 步骤 5: 测试无凭证场景 ──
    print("\n" + "=" * 60)
    print("测试无凭证场景")
    print("=" * 60)

    result2 = await _auth_handler.authenticate(AuthContext(channel_type="web"))
    print(f"[11] 无凭证: success={result2.success}, user_id={result2.user_id}")

    # ── 步骤 6: 测试凭证管理功能 ──
    print("\n" + "=" * 60)
    print("测试凭证管理功能")
    print("=" * 60)

    api_key = auth.generate_api_key()
    print(f"[12] generate_api_key: {api_key[:20]}...")

    keypair = auth.generate_user_keypair()
    print(f"[13] generate_user_keypair: 公钥长度={len(keypair.public_key)}")

    hmac_value = auth.compute_api_key_hmac("test-key", "test-secret")
    print(f"[14] compute_api_key_hmac: {hmac_value[:20]}...")

    # ── 步骤 7: 验证结果 ──
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)
    assert result.success is True, "Passthrough 认证应该总是成功"
    assert result.user_id == "test-token-123", "user_id 应该等于 token 值"
    assert result2.success is True, "无凭证也应该成功（透传模式）"
    assert result2.user_id == "anonymous", "无凭证时 user_id 应为 anonymous"
    assert api_key.startswith("ak-"), "API Key 应以 ak- 开头"
    assert keypair.public_key.startswith("-----BEGIN"), "公钥应为 PEM 格式"
    print("✅ 所有断言通过！")


if __name__ == "__main__":
    asyncio.run(test_full_flow())