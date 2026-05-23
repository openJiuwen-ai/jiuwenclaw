from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

import httpx


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
    error: Optional[str] = None
    execution_time_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SandboxClient:
    """Placeholder SandboxClient; replaced with full implementation at deploy time."""

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._api_base = config.api_base.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def get_config(self) -> SandboxConfig:
        return self._config

    async def create_sandbox(self) -> ExecutionResult:
        return self._placeholder_result("create_sandbox")

    async def delete_sandbox(self, sandbox_id: str) -> ExecutionResult:
        return self._placeholder_result("delete_sandbox")

    async def refresh_duration(
        self,
        sandbox_id: str,
        duration_seconds: int | None = None,
    ) -> ExecutionResult:
        return self._placeholder_result("refresh_duration")

    async def exec_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        return self._placeholder_result("exec_command")

    async def exec_code(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        return self._placeholder_result("exec_code")

    async def upload_file(
        self,
        local_path: str,
        remote_path: str,
        sandbox_id: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            output=f"placeholder upload_file: {local_path} -> {remote_path} (sandbox_id={sandbox_id})",
        )

    async def download_file(
        self,
        remote_path: str,
        sandbox_id: str,
        **kwargs: Any,
    ) -> ExecutionResult:
        return self._placeholder_result("download_file")

    async def list_sandbox_files(self, sandbox_id: str, root: str = ".") -> list[str]:
        return []

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _placeholder_result(method: str) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            error=f"SandboxClient placeholder: {method} not implemented",
        )
