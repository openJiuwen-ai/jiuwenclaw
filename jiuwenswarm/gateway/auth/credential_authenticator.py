from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# 认证上下文
@dataclass
class AuthContext:
    channel_type: str = ""  # web / tui / ssh
    credentials: dict = field(default_factory=dict) # 认证凭据（token / public_key / api_key / certificate等）
    headers: dict = field(default_factory=dict)  # HTTP Headers
    remote_addr: str = ""  # 客户端地址


# 认证结果
@dataclass
class AuthResult:
    success: bool      # 认证是否成功
    user_id: str = ""       # 用户ID（认证成功时）
    error: str = ""        # 错误信息（认证失败时）
    extensions: dict = field(default_factory=dict)   # 扩展信息（如token解析后的claims）


# 抽象接口：统一认证和凭证管理
class CredentialAuthenticator(ABC):
    @abstractmethod
    async def authenticate(self, context: AuthContext) -> AuthResult:
        """认证用户身份，支持Token、API-KEY、SSH证书等多种认证方式"""
        pass