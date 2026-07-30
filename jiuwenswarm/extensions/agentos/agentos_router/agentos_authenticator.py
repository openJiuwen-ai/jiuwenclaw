import httpx

from jiuwenswarm.gateway.auth.credential_authenticator import (
    CredentialAuthenticator,
    AuthContext,
    AuthResult,
)


class AuthServiceClient:
    pass

class AgentOSAuthenticator(CredentialAuthenticator):

    def __init__(self, auth_service_url: str,  # agent-os 后端地址，例如 "http://localhost:8000"
                 timeout: float = 10.0, ):  # HTTP 请求超时秒数，默认 10
        self._auth_service_url = auth_service_url.rstrip("/")
        self._timeout = timeout
        self._auth_client = httpx.AsyncClient(timeout=timeout)

    async def _authenticate_token(self, token: str, extra_headers: dict | None = None) -> AuthResult:

        """验证 JWT access_token。
        支持从 extra_headers 中提取 Authorization header 覆盖 token 参数。
 	    如果配置了 gateway_secret_key，优先本地解码（零 IO）；
 	    否则调用 agent-os 的 /api/v1/auth/verify 接口验证。
        """

        # 如果 extra_headers 中有 Authorization header，优先从中提取 token
        if extra_headers:
            auth_header = extra_headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]  # 覆盖 token 参数

        # HTTP 验证：合并自定义 header
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = await self._auth_client.post(
                f"{self._auth_service_url}/api/v1/auth/verify",
                json={"token": token, "resource_id": "", "action_id": ""},
                headers=headers,
                timeout=self._timeout,
            )

        except Exception as e:
            return AuthResult(success=False, user_id="", error=str(e))

        # 解析业务响应
        try:
            body = resp.json()
        except ValueError:
            return AuthResult(
                success=False,
                user_id="",
                error="认证服务返回了非法的响应格式",
                extensions={"error_code": "INVALID_RESPONSE"},
            )

        data = body.get("data", {})
        if data.get("valid"):
            return AuthResult(
                success=True,
                user_id=data.get("user_id", ""),
                extensions={
                    "username": data.get("username"),
                    "role": data.get("role"),
                    "auth_method": "token",
                },
            )

        return AuthResult(
            success=False,
            user_id="",
            error=data.get("error", "Token 无效或已过期"),
        )

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """根据 context.credentials 中的凭证类型选择认证方式。

        支持以下凭证类型（按优先级）：
          - token: Bearer JWT access_token
        """

        """支持Token、API-KEY、SSH证书等多种认证方式"""
        credentials = context.credentials or {}
        extra_headers = getattr(context, 'headers', None) or {}  # 从 context 取自定义 header

        # 1. Token认证（Web/TUI Channel）
        if "token" in credentials:
            return await self._authenticate_token(credentials["token"], extra_headers)

        return AuthResult(success=False, error="No valid credentials")






