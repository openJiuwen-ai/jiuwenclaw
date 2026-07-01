"""身份服务表定义：用户 / 认证身份 / 刷新会话 / 组织 / 成员。

权威的"人 + 凭据 + 目录(组织/成员)"数据源,独立于 claw_manager 管理库。
认证与身份解耦：``app_user`` 存身份/角色，``auth_identity`` 存凭据/外部 IdP
（二次开发新增 provider 不动业务表）。bot / 可见性 / 模板等平台配置留在管理库。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# 无组织的保留 group_id（避免 NULL 特判，可见性/查询全程统一）。
NO_ORG_GROUP_ID = "__none__"

APP_USER_TABLE_DEF = TableDefinition(
    table_name="app_user",
    columns=[
        ColumnDefinition("user_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("display_name", "string", length=128, nullable=False),
        ColumnDefinition("is_admin", "boolean", nullable=False, default=False),
        ColumnDefinition("status", "string", length=16, nullable=False, default="active"),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# 认证身份：一个 user 可挂多种登录方式（local / oidc / ldap...）。换登录方式只动这张表。
AUTH_IDENTITY_TABLE_DEF = TableDefinition(
    table_name="auth_identity",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("provider", "string", length=32, nullable=False),
        ColumnDefinition("external_subject", "string", length=256, nullable=False),
        ColumnDefinition("credential", "string", length=512, nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["provider", "external_subject"], unique=True),
        IndexDefinition(["user_id"], unique=False),
    ],
)

# 刷新会话：refresh token 落地，可撤销/轮换（access JWT 自包含、不落库）。
AUTH_SESSION_TABLE_DEF = TableDefinition(
    table_name="auth_session",
    columns=[
        ColumnDefinition("refresh_token", "string", length=128, primary_key=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("expires_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["user_id"], unique=False),
        IndexDefinition(["expires_at"], unique=False),
    ],
)

ORG_TABLE_DEF = TableDefinition(
    table_name="org",
    columns=[
        ColumnDefinition("group_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("status", "string", length=16, nullable=False, default="active"),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# 用户↔组织 多对多。默认无组织 = 不存在任何真实成员关系（自动归类）。
USER_ORG_MEMBERSHIP_TABLE_DEF = TableDefinition(
    table_name="user_org_membership",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("group_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["user_id", "group_id"], unique=True),
        IndexDefinition(["group_id"], unique=False),
    ],
)

# JWT 签名密钥（RS256,单例固定主键 id="default"）。生成一次→落库→所有副本读同一行。
# 参考 claw_manager `manager_identity` 的落库范式,但本表/库/算法独立(RSA PEM 文本)。
IDENTITY_JWT_SIGNING_KEY_TABLE_DEF = TableDefinition(
    table_name="identity_jwt_signing_key",
    columns=[
        ColumnDefinition("id", "string", length=32, primary_key=True, nullable=False),
        ColumnDefinition("sign_alg", "string", length=32, nullable=False),         # "RS256"
        ColumnDefinition("private_key", "string", length=4096, nullable=False),     # PKCS8 PEM
        ColumnDefinition("public_key", "string", length=1024, nullable=False),      # SPKI PEM
        ColumnDefinition("key_version", "string", length=32, nullable=False),       # "v1"
        ColumnDefinition("fingerprint", "string", length=128, nullable=False),      # SHA-256 hex(public PEM)
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

IDENTITY_TABLE_DEFINITIONS = (
    APP_USER_TABLE_DEF,
    AUTH_IDENTITY_TABLE_DEF,
    AUTH_SESSION_TABLE_DEF,
    ORG_TABLE_DEF,
    USER_ORG_MEMBERSHIP_TABLE_DEF,
    IDENTITY_JWT_SIGNING_KEY_TABLE_DEF,
)
