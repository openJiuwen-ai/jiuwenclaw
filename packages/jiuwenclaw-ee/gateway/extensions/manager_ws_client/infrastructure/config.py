# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""数据库配置（默认读取 ``GATEWAY_*``，见仓库根 ``.env.example``）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def load_env() -> None:
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

    gateway_db_type: str = Field(default="sqlite", validation_alias="GATEWAY_DB_TYPE")
    gateway_sqlite_path: str = Field(
        default="gateway.db",
        validation_alias="GATEWAY_SQLITE_PATH",
    )
    gateway_db_host: str = Field(default="127.0.0.1", validation_alias="GATEWAY_DB_HOST")
    gateway_db_port: int = Field(default=3306, validation_alias="GATEWAY_DB_PORT")
    gateway_db_user: str = Field(default="root", validation_alias="GATEWAY_DB_USER")
    gateway_db_password: str = Field(
        default="root",
        validation_alias="GATEWAY_DB_PASSWORD",
    )
    gateway_db_name: str = Field(
        default="gateway",
        validation_alias="GATEWAY_DB_NAME",
    )


def get_settings() -> Settings:
    """重新加载 .env 后返回当前配置（provision 子进程注入的环境变量生效）。"""
    load_env()
    return Settings()


settings = get_settings()
