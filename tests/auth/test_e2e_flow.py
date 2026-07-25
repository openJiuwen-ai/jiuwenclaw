"""
端到端调测：模拟从 gateway.yaml 加载配置到认证的完整流程
"""
import asyncio
import os
import sys
import yaml

# ── 在导入任何 jiuwenswarm 模块之前，先 mock 掉循环导入 ──
from unittest.mock import MagicMock

# 先 mock 掉 gateway 包，避免循环导入
import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

# 再 mock 掉 web_connect 中导入的 get_auth_handler
# （因为 web_connect → app_gateway → registry 是循环链的关键）
import jiuwenswarm.gateway.channel_manager.web.web_connect
jiuwenswarm.gateway.channel_manager.web.web_connect.get_auth_handler = MagicMock()

# 然后再导入测试目标
from jiuwenswarm.extensions.auth.agentos_authenticator import AgentOSAuthenticator
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
    print("端到端认证流程调测")
    print("=" * 60)

    # ── 步骤 1: 加载配置（模拟 registry.py） ──
    config = load_auth_config()
    auth_config = config.get("auth", {})
    auth_type = auth_config.get("type", "passthrough")
    print(f"[3] 认证类型: {auth_type}")

    if auth_type != "agentos":
        print("[!] 当前不是 agentos 模式，请修改 gateway.yaml")
        return

    agentos_config = auth_config.get("agentos", {})
    auth_service_url = agentos_config.get("auth_service_url", "")
    gateway_secret_key = agentos_config.get("gateway_secret_key", "")

    print(f"[4] auth_service_url: {auth_service_url}")
    print(f"[5] gateway_secret_key: {'***' if gateway_secret_key else '未设置'}")

    # ── 步骤 2: 创建认证器（模拟 registry.py 注册认证器） ──
    auth = AgentOSAuthenticator(
        auth_service_url=auth_service_url,
        gateway_secret_key=gateway_secret_key,
    )
    print("[6] AgentOSAuthenticator 创建成功")

    # ── 步骤 3: 模拟 app_gateway.py 缓存认证器 ──
    _auth_handler = auth
    print("[7] 认证器已缓存到 _auth_handler")

    # ── 步骤 4: 生成测试 JWT Token ──
    from jose import jwt
    import time

    token = jwt.encode(
        {
            "sub": "user-001",
            "username": "testuser",
            "role": "admin",
            "type": "access",
            "exp": int(time.time()) + 3600,
        },
        gateway_secret_key,
        algorithm="HS256",
    )
    print(f"[8] 生成测试 Token: {token[:50]}...")

    # ── 步骤 5: 模拟 web_connect.py 的 _handle_connect ──
    print("\n" + "=" * 60)
    print("模拟 WebSocket 连接认证（web_connect.py → authenticate()）")
    print("=" * 60)

    extracted_token = token
    print(f"[9] extract_token: 提取到 Token")

    extracted_headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Forwarded-For": "192.168.1.100",
    }
    print(f"[10] extract_headers: 提取到 {len(extracted_headers)} 个请求头")

    remote_addr = "192.168.1.100"
    print(f"[11] get_remote_addr: {remote_addr}")

    context = AuthContext(
        channel_type="web",
        credentials={"token": extracted_token},
        headers=extracted_headers,
        remote_addr=remote_addr,
    )
    print(f"[12] AuthContext 构造完成")

    result = await _auth_handler.authenticate(context)

    print(f"\n[13] 认证结果:")
    print(f"    success: {result.success}")
    print(f"    user_id: {result.user_id}")
    print(f"    error: {result.error}")
    print(f"    extensions: {result.extensions}")

    # ── 步骤 6: 测试失败场景 ──
    print("\n" + "=" * 60)
    print("测试失败场景")
    print("=" * 60)

    result2 = await _auth_handler.authenticate(AuthContext(channel_type="web"))
    print(f"[14] 无凭证: success={result2.success}, error={result2.error}")

    expired_token = jwt.encode(
        {"sub": "user-001", "type": "access", "exp": int(time.time()) - 3600},
        gateway_secret_key,
    )
    result3 = await _auth_handler.authenticate(
        AuthContext(channel_type="web", credentials={"token": expired_token})
    )
    print(f"[15] 过期 Token: success={result3.success}, error={result3.error}")

    # ── 步骤 7: 验证结果 ──
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)
    assert result.success is True, f"认证应该成功，但返回: {result.error}"
    assert result.user_id == "user-001", f"user_id 应为 user-001，实际: {result.user_id}"
    assert result2.success is False, "无凭证应该认证失败"
    assert result3.success is False, "过期 Token 应该认证失败"
    print("✅ 所有断言通过！")


if __name__ == "__main__":
    asyncio.run(test_full_flow())