# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""企业专属 PersistentStore name → 业务主键 / 实例作用域。

仅 DB 布局（见 ``storage_assembly.layouts``）；personal 无 file，调用应 fail-fast。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnterpriseRecordSpec:
    """一行企业表的定位方式。

    - ``key_fields``：业务主键（不含 ``jiuwenclaw_id``）；空元组表示实例内至多一行。
    - ``scope_field``：实例隔离列；``None`` 表示全局单例表（如 gateway 密钥对）。
    """

    key_fields: tuple[str, ...] = ()
    scope_field: str | None = "jiuwenclaw_id"


# store name == DB 表名；与 layouts._ENTERPRISE_ONLY / EE TableDefinition 对齐
ENTERPRISE_RECORD_SPECS: dict[str, EnterpriseRecordSpec] = {
    "config_effective_global_policy": EnterpriseRecordSpec(key_fields=("policy_id",)),
    "config_effective_service_policy": EnterpriseRecordSpec(key_fields=("policy_id",)),
    "config_effective_agent_policy": EnterpriseRecordSpec(key_fields=("policy_id",)),
    "config_default_template_mapping": EnterpriseRecordSpec(key_fields=("policy_id",)),
    "model_template": EnterpriseRecordSpec(key_fields=("template_id",)),
    "embedding_template": EnterpriseRecordSpec(key_fields=("template_id",)),
    "extension_config_template": EnterpriseRecordSpec(key_fields=("template_id",)),
    "skill_whitelist_template": EnterpriseRecordSpec(key_fields=("template_id",)),
    "service_config_template": EnterpriseRecordSpec(key_fields=("template_id",)),
    "log_masking_rule": EnterpriseRecordSpec(key_fields=("rule_id",)),
    "task_memory_config": EnterpriseRecordSpec(key_fields=()),
    "manager_sign_pubkey": EnterpriseRecordSpec(key_fields=()),
    "gateway_enc_keypair": EnterpriseRecordSpec(
        key_fields=("id",),
        scope_field=None,
    ),
    "gateway_sign_keypair": EnterpriseRecordSpec(
        key_fields=("id",),
        scope_field=None,
    ),
}

ENTERPRISE_RECORD_STORE_NAMES: tuple[str, ...] = tuple(ENTERPRISE_RECORD_SPECS.keys())


def get_enterprise_record_spec(store_name: str) -> EnterpriseRecordSpec:
    try:
        return ENTERPRISE_RECORD_SPECS[store_name]
    except KeyError as exc:
        raise KeyError(f"unknown enterprise record store: {store_name!r}") from exc


__all__ = [
    "ENTERPRISE_RECORD_SPECS",
    "ENTERPRISE_RECORD_STORE_NAMES",
    "EnterpriseRecordSpec",
    "get_enterprise_record_spec",
]
