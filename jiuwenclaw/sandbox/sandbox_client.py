from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_PLACEHOLDER_MSG = (
    "SandboxClient is a placeholder; replace jiuwenclaw/sandbox/sandbox_client.py "
    "with the deployment implementation"
)


@dataclass
class SandboxConfig:
    api_base: str
    template_id: str
    duration_seconds: int = 900
    timeout_seconds: int = 120
    metadata: dict[str, str] = field(default_factory=dict)
    command_timeout_seconds: int = 60
    code_timeout_seconds: int = 60


@dataclass
class ExecutionResult:
    success: bool = False
    output: str = ""
    error: str | None = None
    execution_time_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
        }


class SandboxClient:
    """SandboxClient 占位实现；部署时替换本文件为完整实现。"""

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._api_base = config.api_base.rstrip("/")
        self._client: Any | None = None

    @property
    def get_config(self) -> SandboxConfig:
        return self._config

    async def create_sandbox(self) -> ExecutionResult:
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def delete_sandbox(self, sandbox_id: str) -> ExecutionResult:
        _ = sandbox_id
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def refresh_duration(
        self,
        sandbox_id: str,
        duration_seconds: int | None = None,
    ) -> ExecutionResult:
        _ = sandbox_id, duration_seconds
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def exec_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        _ = sandbox_id, command, timeout_seconds
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def exec_code(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        _ = sandbox_id, code, language, timeout_seconds
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def upload_file(
        self,
        local_path: str,
        remote_path: str,
        sandbox_id: str,
        **kwargs: Any,
    ) -> ExecutionResult:
        _ = local_path, remote_path, sandbox_id, kwargs
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def download_file(
        self,
        remote_path: str,
        sandbox_id: str,
        **kwargs: Any,
    ) -> ExecutionResult:
        _ = remote_path, sandbox_id, kwargs
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def list_sandbox_files(self, sandbox_id: str, root: str = ".") -> list[str]:
        _ = sandbox_id, root
        raise NotImplementedError(_PLACEHOLDER_MSG)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
