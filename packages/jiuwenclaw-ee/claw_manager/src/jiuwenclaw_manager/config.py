"""运行时配置（以环境变量为准，与设计文档中的组件选型对齐）。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAWMANAGER_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8765
    database_url: str = "sqlite+aiosqlite:///./claw_manager.db"
    heartbeat_timeout_seconds: int = 30
    scan_interval_seconds: int = 30
    rabbitmq_url: str | None = None
    # 与 Gateway / Agent-Server 发布端约定一致（可用环境变量覆盖）
    rabbitmq_exchange: str = "jiuwenclaw.events"
    rabbitmq_routing_key: str = "event.instance.#"
    rabbitmq_queue_name: str | None = None
    # 队列名默认 claw_manager_{manager_id}；多副本部署时请为每个进程设置不同 CLAWMANAGER_MANAGER_ID
    manager_id: str = "default"
    # 调用组网内 agent_client REST（extensions.agent_client 挂载的 /api/v1/instances/*）
    upstream_http_timeout_seconds: float = 60.0
    upstream_api_key: str | None = None


settings = Settings()
