# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Security policy data models (static only)."""

from __future__ import annotations

import enum
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _expand_path(value: str) -> str:
    """Expand shell-style path markers without requiring the path to exist."""
    return str(Path(os.path.expandvars(value)).expanduser())


def _contains_crlf_or_null(value: str) -> bool:
    """Check if string contains CRLF or null byte."""
    return "\r" in value or "\n" in value or "\x00" in value


def _contains_control_chars(value: str) -> bool:
    """Check if string contains control characters (excluding tab)."""
    for c in value:
        if ord(c) < 32 and c != "\t":
            return True
    return False


def _contains_path_traversal(value: str) -> bool:
    """Check if string contains path traversal sequence."""
    from urllib.parse import unquote
    decoded = unquote(value)
    return ".." in decoded or "/../" in decoded or decoded.endswith("/..")


def _normalize_octal_permissions(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 0 <= value <= 0o777:
            text = format(value, "o")
        else:
            text = str(value)
    else:
        text = str(value)
    if not text:
        raise ValueError(f"{label} cannot be empty")
    if not all(char in "01234567" for char in text):
        raise ValueError(f"{label} must be an octal value")
    if len(text) > 4:
        raise ValueError(f"{label} must be at most four octal digits")
    return text.zfill(4)


class BindMount(BaseModel):
    host_path: str
    sandbox_path: str
    mode: Literal["ro", "rw"] = "ro"

    @field_validator("host_path", "sandbox_path", mode="before")
    @classmethod
    def expand_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return _expand_path(value)
        return value


class DeviceMount(BaseModel):
    host_path: str
    sandbox_path: str

    @field_validator("host_path", "sandbox_path", mode="before")
    @classmethod
    def expand_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return _expand_path(value)
        return value


class DirectoryMount(BaseModel):
    path: str
    permissions: str | int | None = None

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> object:
        if isinstance(value, str):
            return _expand_path(value)
        return value

    @field_validator("permissions", mode="before")
    @classmethod
    def permissions_must_be_octal(cls, value: object) -> str | None:
        return _normalize_octal_permissions(value, label="directory permissions")


class FileMount(BaseModel):
    path: str
    permissions: str | int | None = None

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> object:
        if isinstance(value, str):
            return _expand_path(value)
        return value

    @field_validator("permissions", mode="before")
    @classmethod
    def permissions_must_be_octal(cls, value: object) -> str | None:
        return _normalize_octal_permissions(value, label="file permissions")


class FilesystemPolicy(BaseModel):
    directories: list[str | DirectoryMount] = Field(default_factory=list)
    files: list[str | FileMount] = Field(default_factory=list)
    read_only: list[str] = Field(default_factory=list)
    read_write: list[str] = Field(default_factory=list)
    bind_mounts: list[BindMount] = Field(default_factory=list)
    device: list[DeviceMount] = Field(default_factory=list)

    @field_validator("directories", mode="before")
    @classmethod
    def expand_directory_paths(cls, value: object) -> object:
        if isinstance(value, list):
            return [_expand_path(item) if isinstance(item, str) else item for item in value]
        return value

    @field_validator("files", mode="before")
    @classmethod
    def expand_file_paths(cls, value: object) -> object:
        if isinstance(value, list):
            return [_expand_path(item) if isinstance(item, str) else item for item in value]
        return value

    @field_validator("read_only", "read_write", mode="before")
    @classmethod
    def expand_path_lists(cls, value: object) -> object:
        if isinstance(value, list):
            return [_expand_path(item) if isinstance(item, str) else item for item in value]
        return value


class ProcessPolicy(BaseModel):
    run_as_user: str = "sandbox"
    run_as_group: str = "sandbox"


class NamespacePolicy(BaseModel):
    user: bool = True
    pid: bool = True
    ipc: bool = True
    cgroup: bool = True
    uts: bool = True


class CapabilityPolicy(BaseModel):
    add: list[str] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)


class LandlockPolicy(BaseModel):
    compatibility: Literal["disabled", "best_effort", "hard_requirement"] = "best_effort"


class ArchitectureSyscallPolicy(BaseModel):
    blocked: list[str] = Field(default_factory=list)


class SyscallPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x86_64: ArchitectureSyscallPolicy = Field(default_factory=ArchitectureSyscallPolicy)
    arm64: ArchitectureSyscallPolicy = Field(default_factory=ArchitectureSyscallPolicy)


class NetworkMode(str, enum.Enum):
    ISOLATED = "isolated"
    HOST = "host"


class NetworkRulePolicy(BaseModel):
    default: Literal["deny", "allow"] = "deny"
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    allowed_ips: list[str] = Field(default_factory=list)
    blocked_ips: list[str] = Field(default_factory=list)
    allowed_ports: list[int] = Field(default_factory=list)
    blocked_ports: list[int] = Field(default_factory=list)


class ProxyRouteEntry(BaseModel):
    path_prefix: str  # Required - no default (must be single-level, non-root)
    target_endpoint: str = "https://api.openai.com"
    api_key: str = ""
    skip_cert_verify: bool = False

    @field_validator("path_prefix", mode="after")
    @classmethod
    def validate_path_prefix(cls, value: str) -> str:
        from urllib.parse import unquote
        
        if not value or not value.strip():
            raise ValueError("path_prefix cannot be empty")
        
        normalized = value.strip()
        
        if _contains_crlf_or_null(normalized):
            raise ValueError("path_prefix contains invalid characters")
        
        decoded = unquote(normalized)
        if _contains_crlf_or_null(decoded):
            raise ValueError("path_prefix contains invalid characters")
        
        if _contains_control_chars(normalized) or _contains_control_chars(decoded):
            raise ValueError("path_prefix contains invalid characters")
        
        if _contains_path_traversal(normalized):
            raise ValueError("path_prefix cannot contain path traversal")
        
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        
        # Check for root path BEFORE stripping trailing slash
        # "/" normalized becomes "" after rstrip, so check original normalized
        if normalized.rstrip("/") == "":
            raise ValueError(
                "path_prefix cannot be root path '/'. "
                "Root path would match all requests and make other routes unreachable. "
                "Use a specific prefix like '/api' or '/llm-proxy'."
            )
        
        normalized = normalized.rstrip("/")
        
        # Ban internal slashes (single-level only)
        stripped = normalized.lstrip("/")
        if "/" in stripped:
            raise ValueError(
                f"path_prefix must be single-level (no internal slashes). "
                f"Got '{value}' -> '{normalized}'. "
                f"Use '/api' not '/api/v1'. Each route handles one path level only."
            )
        
        return normalized

    @field_validator("api_key", mode="after")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        from urllib.parse import unquote
        
        if not value:
            return value
        
        if _contains_crlf_or_null(value):
            raise ValueError("api_key contains invalid characters")
        
        decoded = unquote(value)
        if _contains_crlf_or_null(decoded):
            raise ValueError("api_key contains invalid characters")
        
        if _contains_control_chars(value) or _contains_control_chars(decoded):
            raise ValueError("api_key contains invalid characters")
        
        return value

    @field_validator("target_endpoint", mode="after")
    @classmethod
    def validate_target_endpoint(cls, value: str) -> str:
        from urllib.parse import urlparse
        
        if not value or not value.strip():
            raise ValueError("target_endpoint cannot be empty")
        
        normalized = value.strip()
        
        if _contains_crlf_or_null(normalized):
            raise ValueError("target_endpoint contains invalid characters")
        
        try:
            parsed = urlparse(normalized)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("target_endpoint must be a valid URL")
            if parsed.scheme not in ("http", "https"):
                raise ValueError("target_endpoint must use http or https scheme")
        except Exception as e:
            raise ValueError(f"target_endpoint must be a valid URL: {e}") from e
        
        return normalized


class InferencePrivacyProxyPolicy(BaseModel):
    listen_port: int = 0
    listen_host: str | None = None
    routes: list[ProxyRouteEntry] = Field(default_factory=list)

    @field_validator("listen_port", mode="after")
    @classmethod
    def validate_listen_port(cls, value: int) -> int:
        if value < 0 or value > 65535:
            raise ValueError("listen_port must be between 0 and 65535")
        return value

    @field_validator("listen_host", mode="after")
    @classmethod
    def validate_listen_host(cls, value: str | None, info) -> str | None:
        import ipaddress
        listen_port = info.data.get("listen_port", 0)
        if listen_port <= 0:
            return value
        if not value or not value.strip():
            raise ValueError("listen_host required when listen_port > 0")
        try:
            ipaddress.ip_address(value.strip())
        except ValueError as e:
            raise ValueError(f"listen_host must be valid IP address: {value}") from e
        return value.strip()


class NetworkPolicy(BaseModel):
    mode: NetworkMode = NetworkMode.ISOLATED
    egress: NetworkRulePolicy = Field(default_factory=NetworkRulePolicy)
    ingress: NetworkRulePolicy = Field(default_factory=NetworkRulePolicy)


class SecurityPolicy(BaseModel):
    """Complete static security policy for a sandbox."""

    version: int = 1
    name: str = "default"
    environment: dict[str, str] = Field(default_factory=dict)
    filesystem_policy: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    process: ProcessPolicy = Field(default_factory=ProcessPolicy)
    namespace: NamespacePolicy = Field(default_factory=NamespacePolicy)
    capabilities: CapabilityPolicy = Field(default_factory=CapabilityPolicy)
    landlock: LandlockPolicy = Field(default_factory=LandlockPolicy)
    syscall: SyscallPolicy = Field(default_factory=SyscallPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    inference_privacy_proxies: InferencePrivacyProxyPolicy = Field(default_factory=InferencePrivacyProxyPolicy)

    def tostring(self) -> str:
        """Serialize the policy to a YAML string."""
        return yaml.safe_dump(
            self.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        )

    def __str__(self) -> str:
        return self.tostring()
