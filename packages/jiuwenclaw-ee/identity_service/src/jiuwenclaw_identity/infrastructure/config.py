"""身份服务运行时配置（从 .env / 环境变量加载，前缀 IDENTITY_）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_files() -> tuple[str | Path, ...]:
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

    # ---- HTTP ----
    rest_host: str = Field(default="0.0.0.0", validation_alias="AUTH_SRV_REST_HOST")
    rest_port: int = Field(default=8770, validation_alias="AUTH_SRV_REST_PORT")

    # ---- 数据库（独立身份库；DBHandler 抽象，sqlite/mysql/postgresql 通用）----
    db_type: str = Field(default="sqlite", validation_alias="AUTH_SRV_DB_TYPE")
    sqlite_path: str = Field(default="identity.db", validation_alias="AUTH_SRV_SQLITE_PATH")
    db_host: str = Field(default="127.0.0.1", validation_alias="AUTH_SRV_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="AUTH_SRV_DB_PORT")
    db_user: str = Field(default="root", validation_alias="AUTH_SRV_DB_USER")
    db_password: str = Field(default="root", validation_alias="AUTH_SRV_DB_PASSWORD")
    db_name: str = Field(default="identity", validation_alias="AUTH_SRV_DB_NAME")
    pg_schema: str = Field(default="public", validation_alias="AUTH_SRV_PG_SCHEMA")

    # ---- JWT（RS256：私钥签发，资源服务器用公钥验签）----
    jwt_issuer: str = Field(default="jiuwenclaw-identity", validation_alias="AUTH_SRV_JWT_ISSUER")
    jwt_audience: str = Field(default="jiuwenclaw", validation_alias="AUTH_SRV_JWT_AUDIENCE")
    access_ttl_seconds: int = Field(default=1800, validation_alias="AUTH_SRV_ACCESS_TTL")
    refresh_ttl_seconds: int = Field(default=7 * 24 * 3600, validation_alias="AUTH_SRV_REFRESH_TTL")
    # JWT 签名密钥落身份库(表 identity_jwt_signing_key,生成一次→落库→多副本读同一行)。

    # ---- 引导播种 ----
    seed_admin: bool = Field(default=True, validation_alias="AUTH_SRV_SEED_ADMIN")
    seed_user1: bool = Field(default=True, validation_alias="AUTH_SRV_SEED_USER1")

    @property
    def host(self) -> str:
        return self.rest_host

    @property
    def port(self) -> int:
        return self.rest_port


settings = Settings()
