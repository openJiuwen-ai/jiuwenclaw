# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""企业专属表 Repository 注入入口。

迁移期不注入：``get_*`` 返回 None，EE 仍走 ``DBHandler``。
切流后由装配层 ``set_config_record_repositories`` 注入。
"""

from __future__ import annotations

from jiuwenswarm.gateway.config.enterprise.repository import ConfigRecordRepository

_repos: dict[str, ConfigRecordRepository] = {}


def set_config_record_repository(
    store_name: str,
    repo: ConfigRecordRepository | None,
) -> None:
    if repo is None:
        _repos.pop(store_name, None)
    else:
        _repos[store_name] = repo


def set_config_record_repositories(
    repos: dict[str, ConfigRecordRepository] | None,
) -> None:
    _repos.clear()
    if repos:
        _repos.update(repos)


def get_config_record_repository(store_name: str) -> ConfigRecordRepository | None:
    return _repos.get(store_name)


def clear_config_record_repositories() -> None:
    _repos.clear()


def list_injected_store_names() -> tuple[str, ...]:
    return tuple(sorted(_repos))


__all__ = [
    "clear_config_record_repositories",
    "get_config_record_repository",
    "list_injected_store_names",
    "set_config_record_repository",
    "set_config_record_repositories",
]
