from datetime import timedelta

from jose import jwt, JWTError
import httpx

from jiuwenswarm.gateway.auth.credential_authenticator import (
    TokenAuthenticator,
    AuthContext,
    AuthResult,
    KeyPair,
    SSHCertificate,
)



class AuthServiceClient:
    pass


class CredentialManager:
    @classmethod
    def generate_api_key(cls):
        pass

    @classmethod
    def generate_user_keypair(cls):
        pass

    @classmethod
    def generate_ssh_certificate(cls, public_key, user_id, validity):
        pass

    @classmethod
    def compute_api_key_hmac(cls, api_key, secret_key):
        pass


class AgentOSAuthenticator(TokenAuthenticator):

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
        """通过 REST API 验证 JWT access_token。

        调用 agent-os 的 /api/v1/auth/verify 接口进行验证。
        """
        """验证 JWT access_token。

           支持从 extra_headers 中提取 Authorization header 覆盖 token 参数。
           如果配置了 jwt_secret_key，优先本地解码（零 IO）；
           否则调用 agent-os 的 /api/v1/auth/verify 接口验证。
           """

        # 如果 extra_headers 中有 Authorization header，优先从中提取 token
        if extra_headers:
            auth_header = extra_headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]  # 覆盖 token 参数

        # 本地验证（零 IO）
        if self._gateway_secret_key:
            token_data = self._verify_access_token_local(token)
            if token_data is None:
                return AuthResult(success=False, user_id="", error="Token 无效或已过期")
            return AuthResult(
                success=True,
                user_id=token_data["user_id"],
                extensions={
                    "username": token_data["username"],
                    "role": token_data["role"],
                    "auth_method": "token",
                }
            )

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

        except httpx.ConnectError:
            return AuthResult(
                success=False, user_id="",
                error="无法连接到认证服务",
                extensions={"error_code": "CONNECTION_ERROR"},
            )
        except httpx.TimeoutException:
            return AuthResult(
                success=False, user_id="",
                error="认证服务请求超时",
                extensions={"error_code": "TIMEOUT"},
            )
        except httpx.RequestError as e:
            return AuthResult(
                success=False, user_id="",
                error=f"认证服务不可达: {e}",
                extensions={"error_code": "REQUEST_ERROR"},
            )

        # 处理 HTTP 状态码
        if resp.status_code == 401:
            return AuthResult(
                success=False,
                user_id="",
                error="Token 已过期，请重新登录",
                extensions={"error_code": "TOKEN_EXPIRED"},
            )
        elif resp.status_code == 403:
            return AuthResult(
                success=False,
                user_id="",
                error="无权限访问该资源",
                extensions={"error_code": "FORBIDDEN"},
            )
        elif resp.status_code == 429:
            return AuthResult(
                success=False,
                user_id="",
                error="请求过于频繁，请稍后重试",
                extensions={"error_code": "RATE_LIMITED"},
            )
        elif resp.status_code >= 500:
            return AuthResult(
                success=False,
                user_id="",
                error="认证服务内部错误，请稍后重试",
                extensions={"error_code": "SERVER_ERROR"},
            )
        elif resp.status_code != 200:
            return AuthResult(
                success=False,
                user_id="",
                error=f"认证服务返回异常状态码: {resp.status_code}",
                extensions={"error_code": "UNKNOWN_HTTP_ERROR"},
            )

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
        if not data.get("valid"):
            return AuthResult(
                success=False,
                user_id="",
                error="Token 无效或已过期",
                extensions={"error_code": "TOKEN_INVALID"},
            )

        return AuthResult(
            success=True,
            user_id=data["user_id"],
            extensions={
                "username": data.get("username"),
                "role": data.get("role"),
                "auth_method": "token",
            },
        )

    def _verify_access_token_local(self, token: str) -> dict | None:
        """本地解码并验证 JWT access_token（零 IO）。"""
        try:
            payload = jwt.decode(
                token,
                self._gateway_secret_key,
                algorithms=[self._gateway_algorithm],
                options={"require_exp": True},
            )
        except JWTError:
            return None
        if payload.get("type") != "access":
            return None
        return {
            "user_id": payload["sub"],
            "username": payload["username"],
            "role": payload["role"],
        }

    def _lookup_agent_by_api_key_hash(self, api_key_hash) -> str | None:
        """通过 API Key 的 HMAC 哈希值查找 agent_id

            根据设计文档 4.5.4.2 节：
            数据库中只存储 HMAC 值，不存储明文 API Key。
            """
        # 接入数据库存储 api_key_hash -> agent_id 映射
        # 当前简化实现：从 self._api_key_map 中查找
        if hasattr(self, '_api_key_map') and self._api_key_map:
            return self._api_key_map.get(api_key_hash)
        return None

    def _verify_ssh_certificate(self, certificate):
        pass

    def _lookup_agent_by_public_key(self, public_key):
        """通过公钥查找 agent_id

            根据设计文档 4.4.2.2 节：
            在本地存储中查找 public_key 对应的 agent_id。
            当前为简化实现，后续应接入数据库或配置中心。
            """
        # 接入数据库/配置中心存储公钥与 agent_id 的映射关系
        # 当前简化实现：从 self._public_key_map 中查找
        if hasattr(self, '_public_key_map') and self._public_key_map:
            return self._public_key_map.get(public_key)
        return None

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

    def _authenticate_api_key(self, api_key: str) -> AuthResult:
        """API-KEY认证

        根据设计文档 4.5.4.2 节：
        1. 计算 API-KEY 的 HMAC 值：HMAC-SHA256(api_key, gateway_secret_key)
        2. 与本地存储的 HMAC 值进行恒定时间比对
        3. 验证通过后返回 agent_id
        """
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

    # 凭证管理实现 --- 当前暂时不支持，抛出异常
    def generate_api_key(self) -> str:
        return CredentialManager.generate_api_key()

    def generate_user_keypair(self) -> KeyPair:
        return CredentialManager.generate_user_keypair()

    def generate_ssh_certificate(self, public_key: str, user_id: str, validity: timedelta) -> SSHCertificate:
        return CredentialManager.generate_ssh_certificate(public_key, user_id, validity)

    def compute_api_key_hmac(self, api_key: str, secret_key: str) -> str:
        return CredentialManager.compute_api_key_hmac(api_key, secret_key)






