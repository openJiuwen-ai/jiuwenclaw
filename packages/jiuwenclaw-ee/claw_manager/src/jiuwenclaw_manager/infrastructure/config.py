"""运行时配置（从 .env / 环境变量加载）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_files() -> tuple[str | Path, ...]:
    """解析可用的 .env 路径（优先 cwd，兼容 venv 安装布局）。"""
    candidates: list[Path] = [Path.cwd() / ".env"]
    here = Path(__file__).resolve()
    for depth in (5, 6):
        candidates.append(here.parents[depth] / ".env")
    return tuple(p for p in candidates if p.is_file())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_files() or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rest_host: str = Field(default="0.0.0.0", validation_alias="MANAGER_REST_HOST")
    rest_port: int = Field(default=8765, validation_alias="MANAGER_REST_PORT")

    db_type: str = Field(default="sqlite", validation_alias="MANAGER_DB_TYPE")
    sqlite_path: str = Field(default="claw_manager.db", validation_alias="MANAGER_SQLITE_PATH")
    db_host: str = Field(default="127.0.0.1", validation_alias="MANAGER_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="MANAGER_DB_PORT")
    db_user: str = Field(default="root", validation_alias="MANAGER_DB_USER")
    db_password: str = Field(default="root", validation_alias="MANAGER_DB_PASSWORD")
    db_name: str = Field(default="claw_manager", validation_alias="MANAGER_DB_NAME")

    manager_heartbeat_timeout_seconds: int = Field(
        default=120, validation_alias="MANAGER_HEARTBEAT_TIMEOUT_SECONDS"
    )
    MANAGER_HEARTBEAT_SCAN_INTERVAL_SECONDS: int = Field(
        default=60, validation_alias="MANAGER_HEARTBEAT_SCAN_INTERVAL_SECONDS"
    )
    allow_local_provision: bool = Field(
        default=False, validation_alias="MANAGER_ALLOW_LOCAL_PROVISION"
    )
    provision_workspace_root: str = Field(
        default=".claw_provisioned_instances",
        validation_alias="CLAWMANAGER_PROVISION_WORKSPACE_ROOT",
    )
    provision_python: str | None = Field(
        default=None, validation_alias="CLAWMANAGER_PROVISION_PYTHON"
    )
    provision_pythonpath: str | None = Field(
        default=None, validation_alias="CLAWMANAGER_PROVISION_PYTHONPATH"
    )
    provision_repo_root: str | None = Field(
        default=None, validation_alias="CLAWMANAGER_PROVISION_REPO_ROOT"
    )
    provision_extension_dirs: str | None = Field(
        default=None, validation_alias="CLAWMANAGER_PROVISION_EXTENSION_DIRS"
    )
    instance_config_template: str | None = Field(
        default=None, validation_alias="CLAWMANAGER_INSTANCE_CONFIG_TEMPLATE"
    )

    manager_ws_enabled: bool = Field(
        default=True, validation_alias="MANAGER_WS_ENABLED"
    )
    manager_ws_host: str = Field(
        default="127.0.0.1", validation_alias="MANAGER_WS_HOST"
    )
    manager_ws_port: int = Field(
        default=8766, validation_alias="MANAGER_WS_PORT"
    )

    @property
    def host(self) -> str:
        return self.rest_host

    @property
    def port(self) -> int:
        return self.rest_port


settings = Settings()
