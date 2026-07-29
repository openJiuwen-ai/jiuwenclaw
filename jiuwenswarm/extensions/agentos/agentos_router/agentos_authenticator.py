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
                 gateway_secret_key: str,  # jwt_secret_key
                 jwt_algorithm: str = "HS256",
                 timeout: float = 10.0, ):  # HTTP 请求超时秒数，默认 10
        self._auth_service_url = auth_service_url.rstrip("/")
        self._timeout = timeout
        self._gateway_algorithm = jwt_algorithm
        self._gateway_secret_key = gateway_secret_key
        self._auth_client = httpx.AsyncClient(timeout=timeout)
        self._ca_public_key: str | None = None  # CA 公钥（OpenSSH 格式）
        self._cert_revocation_list: set[int] | None = None  # 已吊销证书的序列号集合
        self._public_key_map: dict[str, str] | None = None  # public_key -> agent_id 映射

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


    def _lookup_agent_by_api_key_hash(self, api_key_hash) -> str | None:
        """通过 API Key 的 HMAC 哈希值查找 agent_id
            """
        # 接入数据库存储 api_key_hash -> agent_id 映射
        # 当前简化实现：从 self._api_key_map 中查找
        if hasattr(self, '_api_key_map') and self._api_key_map:
            return self._api_key_map.get(api_key_hash)
        return None

    def _verify_ssh_certificate(self, certificate):
        pass

    def _lookup_agent_by_public_key(self, public_key):
        # 接入数据库/配置中心存储公钥与 agent_id 的映射关系
        # 当前简化实现：从 self._public_key_map 中查找
        pass
        if hasattr(self, '_public_key_map') and self._public_key_map:
            return self._public_key_map.get(public_key)
        return None

    def _authenticate_api_key(self, api_key: str) -> AuthResult:
        """验证通过后返回 agent_id"""
        if not self._gateway_secret_key:
            return AuthResult(
                success=False, user_id="",
                error="API Key 认证未配置 gateway_secret_key",
                extensions={"error_code": "CONFIG_ERROR"},
            )

        api_key_hash = self.compute_api_key_hmac(api_key, self._gateway_secret_key)
        agent_id = self._lookup_agent_by_api_key_hash(api_key_hash)
        if agent_id:
            return AuthResult(
                success=True,
                user_id=agent_id,
                extensions={"auth_method": "api_key"},
            )
        return AuthResult(
            success=False, user_id="",
            error="Invalid API-KEY",
            extensions={"error_code": "AUTH_FAILED"},
        )

    def _authenticate_certificate(self, certificate: str) -> AuthResult:
        """SSH证书认证"""
        result = self._verify_ssh_certificate(certificate)
        if result:
            valid, user_id = result
            if valid:
                return AuthResult(success=True, user_id=user_id)
        return AuthResult(success=False, error="Invalid certificate")

    def _authenticate_public_key(self, public_key: str) -> AuthResult:
        """Public Key认证"""
        agent_id = self._lookup_agent_by_public_key(public_key)
        if agent_id:
            return AuthResult(success=True, user_id=agent_id)
        return AuthResult(success=False, error="Unknown public key")

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """根据 context.credentials 中的凭证类型选择认证方式。

        支持以下凭证类型（按优先级）：
          - token: Bearer JWT access_token
          - api_key: API Key
          - certificate: SSH 证书
          - public_key: SSH 公钥（直接映射为匿名用户）
        """

        """支持Token、API-KEY、SSH证书等多种认证方式"""
        credentials = context.credentials or {}
        extra_headers = getattr(context, 'headers', None) or {}  # 从 context 取自定义 header

        # 1. Token认证（Web/TUI Channel）
        if "token" in credentials:
            return await self._authenticate_token(credentials["token"], extra_headers)

        # 2. API-KEY认证（3rd Agent PUB）
        if "api_key" in credentials:
            return self._authenticate_api_key(credentials["api_key"])

        # 3. SSH证书认证（SSH Channel）
        if "certificate" in credentials:
            return self._authenticate_certificate(credentials["certificate"])

        # 4. Public Key认证（SSH Channel）
        if "public_key" in credentials:
            return self._authenticate_public_key(credentials["public_key"])

        return AuthResult(success=False, error="No valid credentials")






