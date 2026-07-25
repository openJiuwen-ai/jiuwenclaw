from abc import ABC, abstractmethod
from datetime import timedelta, datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


class UnsupportedOperationError(Exception):
    """不支持的操作异常"""
    pass


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


@dataclass
class KeyPair:
    public_key: str
    private_key: str


@dataclass
class SSHCertificate:
    public_key: str
    certificate: str
    expires_at: datetime


# 抽象接口：统一认证和凭证管理
class CredentialAuthenticator(ABC):
    @abstractmethod
    async def authenticate(self, context: AuthContext) -> AuthResult:
        """认证用户身份，支持Token、API-KEY、SSH证书等多种认证方式"""
        pass

    @abstractmethod
    def generate_api_key(self) -> str:
        raise UnsupportedOperationError("generate_api_key not supported")

    @abstractmethod
    def generate_user_keypair(self) -> KeyPair:
        """生成用户公私钥对，用于SSH认证"""
        raise UnsupportedOperationError("generate_user_keypair not supported")

    @abstractmethod
    def generate_ssh_certificate(self, public_key: str, user_id: str, validity: timedelta) -> SSHCertificate:
        """生成SSH证书"""
        raise UnsupportedOperationError("generate_ssh_certificate not supported")

    @abstractmethod
    def compute_api_key_hmac(self, api_key: str, secret_key: str) -> str:
        """计算API-KEY的HMAC值，用于存储验证"""
        raise UnsupportedOperationError("compute_api_key_hmac not supported")

# ── 凭证管理接口 ──
class CredentialManager(ABC):
    """凭证管理器"""
    @abstractmethod
    def generate_api_key(self) -> str:
        """生成 API Key"""
        raise UnsupportedOperationError("generate_api_key not supported")

    @abstractmethod
    def generate_user_keypair(self) -> KeyPair:
        """生成用户公私钥对，用于SSH认证"""
        raise UnsupportedOperationError("generate_user_keypair not supported")

    @abstractmethod
    def generate_ssh_certificate(self, public_key: str, user_id: str, validity: timedelta) -> SSHCertificate:
        """生成 SSH 证书"""
        raise UnsupportedOperationError("generate_ssh_certificate not supported")

    @abstractmethod
    def compute_api_key_hmac(self, api_key: str, secret_key: str) -> str:
        """计算 API Key 的 HMAC 值，用于存储验证"""
        raise UnsupportedOperationError("compute_api_key_hmac not supported")