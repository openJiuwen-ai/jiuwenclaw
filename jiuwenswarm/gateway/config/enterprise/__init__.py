# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""企业专属表（config_effective_* / *_template / 密钥等）→ PersistentStore。

仅 DB；运行时切流前 access 不注入，EE 仍用 ``DBHandler``。
"""

from jiuwenswarm.gateway.config.enterprise.access import (
    clear_config_record_repositories,
    get_config_record_repository,
    list_injected_store_names,
    set_config_record_repository,
    set_config_record_repositories,
)
from jiuwenswarm.gateway.config.enterprise.catalog import (
    ENTERPRISE_RECORD_SPECS,
    ENTERPRISE_RECORD_STORE_NAMES,
    EnterpriseRecordSpec,
    get_enterprise_record_spec,
)
from jiuwenswarm.gateway.config.enterprise.repository import ConfigRecordRepository

__all__ = [
    "ENTERPRISE_RECORD_SPECS",
    "ENTERPRISE_RECORD_STORE_NAMES",
    "ConfigRecordRepository",
    "EnterpriseRecordSpec",
    "clear_config_record_repositories",
    "get_config_record_repository",
    "get_enterprise_record_spec",
    "list_injected_store_names",
    "set_config_record_repository",
    "set_config_record_repositories",
]
