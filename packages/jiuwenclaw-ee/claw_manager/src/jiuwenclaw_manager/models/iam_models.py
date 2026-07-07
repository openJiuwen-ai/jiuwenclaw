"""IAM 表定义：用户 / 认证身份 / 会话 / 组织 / 成员 / bot / bot 可见性。

这些都是**平台全局**实体（不带 jiuwenclaw_id）；模板绑定仍按 jiuwenclaw_id 落在
``config_default_template_mapping``。认证与身份解耦：``app_user`` 存身份/角色，
``auth_identity`` 存凭据/外部 IdP（可二次开发，新增 provider 不动业务表）。
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
    # 主键 user_id 已覆盖按 id 查；status 低选择性 + 小表，不建索引。
)

# 认证身份：一个 user 可挂多种登录方式（local / oidc / ldap...）。换登录方式只动这张表。
AUTH_IDENTITY_TABLE_DEF = TableDefinition(
    table_name="auth_identity",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("provider", "string", length=32, nullable=False),
        ColumnDefinition("external_subject", "string", length=256, nullable=False),
        # local：口令哈希串；外部 IdP：可空。
        ColumnDefinition("credential", "string", length=512, nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["provider", "external_subject"], unique=True),
        IndexDefinition(["user_id"], unique=False),
    ],
)

# 登录会话：不透明 token，DB 落地，可撤销。
AUTH_SESSION_TABLE_DEF = TableDefinition(
    table_name="auth_session",
    columns=[
        ColumnDefinition("token", "string", length=128, primary_key=True, nullable=False),
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

# 用户↔组织 多对多。默认无组织 = 指向保留行 __none__ 的成员关系。
USER_ORG_MEMBERSHIP_TABLE_DEF = TableDefinition(
    table_name="user_org_membership",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("group_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
    ],
    indexes=[
        # unique(user_id, group_id) 的最左前缀已覆盖"查某人的组织"(WHERE user_id=)，
        # 故不再单独建 user_id 索引；group_id 不是前缀，需单独建以支持"查某组织有谁"。
        IndexDefinition(["user_id", "group_id"], unique=True),
        IndexDefinition(["group_id"], unique=False),
    ],
)

BOT_TABLE_DEF = TableDefinition(
    table_name="bot",
    columns=[
        ColumnDefinition("bot_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("status", "string", length=16, nullable=False, default="active"),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# bot 可见性：**按实例(jiuwenclaw_id)** 的 全局 / 某组织 / 某人。多行表达。
#   scope_type=global → scope_id=""（保留空串，保证唯一索引可用）
#   scope_type=org    → scope_id=group_id
#   scope_type=user   → scope_id=user_id
# 同一个全局 bot 可在不同实例上有不同可见范围(每行带 jiuwenclaw_id)。
BOT_VISIBILITY_TABLE_DEF = TableDefinition(
    table_name="bot_visibility",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("bot_id", "string", length=64, nullable=False),
        ColumnDefinition("scope_type", "string", length=8, nullable=False),
        ColumnDefinition("scope_id", "string", length=64, nullable=False, default=""),
        ColumnDefinition("created_at", "datetime", nullable=False),
    ],
    indexes=[
        # unique(jiuwenclaw_id, bot_id, scope_type, scope_id)：一实例内某 bot 某范围唯一；
        # 其最左前缀覆盖"某实例上某 bot 的可见性"(WHERE jiuwenclaw_id=,bot_id=)与"某实例有哪些 bot"。
        IndexDefinition(["jiuwenclaw_id", "bot_id", "scope_type", "scope_id"], unique=True),
        # 支持 MeService 按实例+范围反查"某实例上某组织/某人能看哪些 bot"。
        IndexDefinition(["jiuwenclaw_id", "scope_type", "scope_id"], unique=False),
    ],
)

# 用户 ↔ 实例(gateway) 绑定：普通用户被管理员分配到哪些实例才能使用其上的 bot。
# user_id 存字符串、不跨库外键(和 bot_visibility.scope_id 一致；身份目录在 identity 服务)。
USER_GATEWAY_TABLE_DEF = TableDefinition(
    table_name="user_gateway",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
    ],
    indexes=[
        # unique(jiuwenclaw_id, user_id)：一实例内一用户仅一行；最左前缀覆盖"某实例花名册"(WHERE jiuwenclaw_id=)。
        IndexDefinition(["jiuwenclaw_id", "user_id"], unique=True),
        # 反查"某用户绑了哪些实例"(所属实例列)。
        IndexDefinition(["user_id"], unique=False),
    ],
)

# 组织 ↔ 实例(gateway) 绑定：组织被分配到哪些实例。语义同 user_gateway。
ORG_GATEWAY_TABLE_DEF = TableDefinition(
    table_name="org_gateway",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("group_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "group_id"], unique=True),
        IndexDefinition(["group_id"], unique=False),
    ],
)

# 身份表(app_user/auth_identity/auth_session/org/user_org_membership)已迁至独立认证服务
# (jiuwenclaw_identity 的 identity.db),管理库**不再建**这些表;仅保留平台配置侧的 bot/可见性/实例绑定。
# 上面的身份 TableDefinition 定义暂保留(未被建表引用),供历史参考,后续可清理。
IAM_TABLE_DEFINITIONS = (
    BOT_TABLE_DEF,
    BOT_VISIBILITY_TABLE_DEF,
    USER_GATEWAY_TABLE_DEF,
    ORG_GATEWAY_TABLE_DEF,
)
