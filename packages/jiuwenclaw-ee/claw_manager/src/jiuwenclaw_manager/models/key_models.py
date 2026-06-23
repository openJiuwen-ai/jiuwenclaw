# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""配置下发密钥相关表（**新增独立表，绝不改动 instance_info 等既有表**）。

- ``manager_identity``：Manager 自身 Ed25519 签名密钥对（单例，私钥本地受保护）。
- ``instance_enc_pubkey``：各实例 Gateway 在握手时上交的 X25519 加密公钥。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# Manager 签名密钥对（单例，固定主键 id="default"）。
MANAGER_IDENTITY_TABLE_DEF = TableDefinition(
    table_name="manager_identity",
    columns=[
        ColumnDefinition("id", "string", length=32, primary_key=True, nullable=False),
        ColumnDefinition("sign_alg", "string", length=32, nullable=False),
        ColumnDefinition("private_key", "string", length=512, nullable=False),
        ColumnDefinition("public_key", "string", length=256, nullable=False),
        ColumnDefinition("key_version", "string", length=32, nullable=False),
        ColumnDefinition("fingerprint", "string", length=128, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[],
)

# 各实例 Gateway 的加密公钥（握手上交），按 jiuwenclaw_id 关联实例。
INSTANCE_ENC_PUBKEY_TABLE_DEF = TableDefinition(
    table_name="instance_enc_pubkey",
    columns=[
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("enc_alg", "string", length=32, nullable=False),
        ColumnDefinition("public_key", "string", length=256, nullable=False),
        ColumnDefinition("fingerprint", "string", length=128, nullable=False),
        ColumnDefinition("status", "string", length=32, nullable=False, default="bound"),
        ColumnDefinition("bound_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["status"], unique=False),
    ],
)
