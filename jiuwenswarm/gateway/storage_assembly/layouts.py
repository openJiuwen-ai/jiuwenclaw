# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway 落盘清单：业务 name → FileLayout / DbLayout。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jiuwenswarm.gateway.storage.registry.store_registry import (
    DbLayout,
    FileLayout,
    StoreLayout,
    StoreRegistry,
)


def _overlay(
    table: str,
    pointer: str,
    *,
    config_file: Path | None,
    key_fields: tuple[str, ...] = (),
) -> StoreLayout:
    """YAML overlay：personal 写 ``config.yaml`` 的 ``pointer`` 片段，enterprise 写同名表。

    ``config_file`` 为 None 时 file 布局为空（enterprise）。
    """
    file: FileLayout | None = None
    if config_file is not None:
        file = FileLayout(
            path=str(Path(config_file)),
            format="yaml",
            yaml_pointer=pointer,
            key_fields=key_fields,
        )
    return StoreLayout(file=file, db=DbLayout(table=table))


def _db_only(table: str) -> StoreLayout:
    """仅企业表：无 file 布局；personal 调用对应 name 应 fail-fast。"""
    return StoreLayout(db=DbLayout(table=table))


def _persist_file(
    persistent_root: Path | None,
    rel: str,
    **kwargs: Any,
) -> FileLayout | None:
    """把相对路径拼成绝对 ``FileLayout``；未传 ``persistent_root`` 则无文件布局。"""
    if persistent_root is None:
        return None
    return FileLayout(path=str(persistent_root / rel), **kwargs)


# name → (yaml_pointer, key_fields)；空 key_fields 表示整段一份 document
_OVERLAYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "channel_config": ("/channels", ("id",)),
    "permissions_config": ("/permissions", ()),
    "logging_config": ("/logging", ()),
    "memory_config": ("/memory", ()),
}

# 无 personal 文件；只注册 DbLayout
_ENTERPRISE_ONLY: tuple[str, ...] = (
    "config_effective_global_policy",
    "config_effective_service_policy",
    "config_effective_agent_policy",
    "config_default_template_mapping",
    "model_template",
    "embedding_template",
    "extension_config_template",
    "skill_whitelist_template",
    "service_config_template",
    "log_masking_rule",
    "task_memory_config",
    "manager_sign_pubkey",
    "gateway_enc_keypair",
    "gateway_sign_keypair",
)


def _build_layouts(
    *,
    persistent_root: Path | None,
    config_file: Path | None,
) -> dict[str, StoreLayout]:
    """汇总全部业务 name 的布局：JSON 文件、YAML overlay、企业专属表。"""
    layouts: dict[str, StoreLayout] = {
        "session_map": StoreLayout(
            file=_persist_file(
                persistent_root,
                "session_map.json",
                format="json",
                shape="map",
                key_fields=("identity_key",),
            ),
            db=DbLayout(table="session_map"),
        ),
        "cron_job": StoreLayout(
            file=_persist_file(
                persistent_root,
                "cron_jobs/{service_id}/{agent_id}/jobs.json",
                format="json",
                shape="list",
                key_fields=("id",),
            ),
            db=DbLayout(table="cron_job"),
        ),
    }
    for name, (pointer, key_fields) in _OVERLAYS.items():
        layouts[name] = _overlay(
            name, pointer, config_file=config_file, key_fields=key_fields
        )
    for table in _ENTERPRISE_ONLY:
        layouts[table] = _db_only(table)
    return layouts


def build_gateway_store_registry(
    *,
    persistent_root: Path | None = None,
    config_file: Path | None = None,
) -> StoreRegistry:
    """装配 name 对应的落盘布局。personal 传入绝对 ``persistent_root`` / ``config_file``；enterprise 可不传，此时 file 布局为空，只注册 DB。"""
    registry = StoreRegistry()
    registry.register_many(
        _build_layouts(persistent_root=persistent_root, config_file=config_file)
    )
    return registry


__all__ = ["build_gateway_store_registry"]
