"""运行时配置（从 .env / 环境变量加载）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PKG_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PKG_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rest_host: str = Field(default="0.0.0.0", validation_alias="CLAW_MANAGER_REST_HOST")
    rest_port: int = Field(default=8765, validation_alias="CLAW_MANAGER_REST_PORT")

    db_type: str = Field(default="sqlite", validation_alias="CLAW_MANAGER_DB_TYPE")
    sqlite_path: str = Field(default="claw_manager.db", validation_alias="CLAW_MANAGER_SQLITE_PATH")
    db_host: str = Field(default="127.0.0.1", validation_alias="CLAW_MANAGER_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="CLAW_MANAGER_DB_PORT")
    db_user: str = Field(default="root", validation_alias="CLAW_MANAGER_DB_USER")
    db_password: str = Field(default="root", validation_alias="CLAW_MANAGER_DB_PASSWORD")
    db_name: str = Field(default="claw_manager", validation_alias="CLAW_MANAGER_DB_NAME")

    heartbeat_timeout_seconds: int = Field(
        default=30, validation_alias="CLAWMANAGER_HEARTBEAT_TIMEOUT_SECONDS"
    )
    scan_interval_seconds: int = Field(
        default=30, validation_alias="CLAWMANAGER_SCAN_INTERVAL_SECONDS"
    )
    rabbitmq_url: str | None = Field(default=None, validation_alias="CLAWMANAGER_RABBITMQ_URL")
    rabbitmq_exchange: str = Field(
        default="jiuwenclaw.events", validation_alias="CLAWMANAGER_RABBITMQ_EXCHANGE"
    )
    rabbitmq_routing_key: str = Field(
        default="event.instance.#", validation_alias="CLAWMANAGER_RABBITMQ_ROUTING_KEY"
    )
    rabbitmq_queue_name: str | None = Field(
        default=None, validation_alias="CLAWMANAGER_RABBITMQ_QUEUE_NAME"
    )
    manager_id: str = Field(default="default", validation_alias="CLAWMANAGER_MANAGER_ID")
    upstream_http_timeout_seconds: float = Field(
        default=60.0, validation_alias="CLAWMANAGER_UPSTREAM_HTTP_TIMEOUT_SECONDS"
    )
    upstream_api_key: str | None = Field(
        default=None, validation_alias="CLAWMANAGER_UPSTREAM_API_KEY"
    )
    allow_local_provision: bool = Field(
        default=False, validation_alias="CLAWMANAGER_ALLOW_LOCAL_PROVISION"
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

    @property
    def host(self) -> str:
        return self.rest_host

    @property
    def port(self) -> int:
        return self.rest_port


settings = Settings()
