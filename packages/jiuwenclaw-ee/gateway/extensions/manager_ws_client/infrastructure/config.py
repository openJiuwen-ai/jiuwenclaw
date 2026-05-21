# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""manager_ws_client 运行时配置（从仓库根 .env 加载）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def load_manager_ws_client_env() -> None:
    """从仓库根 ``.env`` 加载（优先 cwd，兼容 venv 安装布局）。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    env_path = Path.cwd() / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    db_type: str = Field(default="sqlite", validation_alias="MANAGER_WS_CLIENT_DB_TYPE")
    sqlite_path: str = Field(
        default="agent_client.db",
        validation_alias="MANAGER_WS_CLIENT_SQLITE_PATH",
    )
    db_host: str = Field(default="127.0.0.1", validation_alias="MANAGER_WS_CLIENT_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="MANAGER_WS_CLIENT_DB_PORT")
    db_user: str = Field(default="root", validation_alias="MANAGER_WS_CLIENT_DB_USER")
    db_password: str = Field(
        default="root",
        validation_alias="MANAGER_WS_CLIENT_DB_PASSWORD",
    )
    db_name: str = Field(
        default="manager_ws_client",
        validation_alias="MANAGER_WS_CLIENT_DB_NAME",
    )


def get_settings() -> Settings:
    """重新加载 .env 后返回当前配置（provision 子进程注入的环境变量生效）。"""
    load_manager_ws_client_env()
    return Settings()


settings = get_settings()
