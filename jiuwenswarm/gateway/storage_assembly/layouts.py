# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway 落盘清单：业务 name → FileLayout / DbLayout。

按落盘形态分组（不是按业务域）：

- YAML + DB：``config.yaml`` 片段 ↔ 同名表（双端同构）
- 仅 YAML：``config.yaml`` 片段，无企业表（personal-only）
- JSON + DB：独立 JSON 文件 ↔ 同名表
- 仅 DB：企业专属表，无 file
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from jiuwenswarm.gateway.storage.registry.store_registry import (
    DbLayout,
    FileLayout,
    StoreLayout,
    StoreRegistry,
)

# name → (yaml_pointer, key_fields, yaml_scalar_field)
_YamlSectionSpec = tuple[str, tuple[str, ...], str]
# name → (rel_path, shape, key_fields)
_JsonAndDbSpec = tuple[str, Literal["map", "list"], tuple[str, ...]]


def _yaml_and_db_section(
    table: str,
    pointer: str,
    *,
    config_file: Path | None,
    key_fields: tuple[str, ...] = (),
    yaml_scalar_field: str = "",
) -> StoreLayout:
    """形态：YAML 片段 + 同名 DB 表。"""
    file: FileLayout | None = None
    if config_file is not None:
        file = FileLayout(
            path=str(Path(config_file)),
            format="yaml",
            yaml_pointer=pointer,
            key_fields=key_fields,
            yaml_scalar_field=yaml_scalar_field,
        )
    return StoreLayout(file=file, db=DbLayout(table=table))


def _yaml_only_section(
    pointer: str,
    *,
    config_file: Path | None,
    key_fields: tuple[str, ...] = (),
    yaml_scalar_field: str = "",
) -> StoreLayout | None:
    """形态：仅 YAML 片段（personal-only，``db=None``）。

    ``config_file`` 为 None（enterprise 装配）时不注册。
    """
    if config_file is None:
        return None
    return StoreLayout(
        file=FileLayout(
            path=str(Path(config_file)),
            format="yaml",
            yaml_pointer=pointer,
            key_fields=key_fields,
            yaml_scalar_field=yaml_scalar_field,
        ),
        db=None,
    )


def _json_and_db(
    table: str,
    rel: str,
    *,
    persistent_root: Path | None,
    shape: Literal["map", "list"] = "map",
    key_fields: tuple[str, ...] = (),
) -> StoreLayout:
    """形态：独立 JSON 文件 + 同名 DB 表。"""
    file: FileLayout | None = None
    if persistent_root is not None:
        file = FileLayout(
            path=str(persistent_root / rel),
            format="json",
            shape=shape,
            key_fields=key_fields,
        )
    return StoreLayout(file=file, db=DbLayout(table=table))


def _db_table(table: str) -> StoreLayout:
    """形态：仅企业 DB 表（无 file）。"""
    return StoreLayout(db=DbLayout(table=table))


# ---------------------------------------------------------------------------
# 落盘清单（按形态；store name 勿与企业 catalog 漂移）
# ---------------------------------------------------------------------------

_YAML_AND_DB_SECTIONS: dict[str, _YamlSectionSpec] = {
    "channel_config": ("/channels", ("id",), ""),
    "permissions_config": ("/permissions", (), ""),
    "logging_config": ("/logging", (), ""),
    "memory_config": ("/memory", (), ""),
}

_YAML_ONLY_SECTIONS: dict[str, _YamlSectionSpec] = {
    "heartbeat_config": ("/heartbeat", (), ""),
    "browser_config": ("/browser", (), ""),
    "preferred_language_config": ("/preferred_language", (), "preferred_language"),
    "a2ui_config": ("/a2ui", (), ""),
}

_JSON_AND_DB_STORES: dict[str, _JsonAndDbSpec] = {
    "session_map": ("session_map.json", "map", ("identity_key",)),
    "cron_job": (
        "cron_jobs/{service_id}/{agent_id}/jobs.json",
        "list",
        ("id",),
    ),
}


def _build_layouts(
    *,
    persistent_root: Path | None,
    config_file: Path | None,
) -> dict[str, StoreLayout]:
    """按形态工厂组装全部业务 name → StoreLayout。"""
    from jiuwenswarm.gateway.config.enterprise.catalog import (
        ENTERPRISE_RECORD_STORE_NAMES,
    )

    layouts: dict[str, StoreLayout] = {}

    for name, (rel, shape, key_fields) in _JSON_AND_DB_STORES.items():
        layouts[name] = _json_and_db(
            name,
            rel,
            persistent_root=persistent_root,
            shape=shape,
            key_fields=key_fields,
        )

    for name, (pointer, key_fields, yaml_scalar_field) in _YAML_AND_DB_SECTIONS.items():
        layouts[name] = _yaml_and_db_section(
            name,
            pointer,
            config_file=config_file,
            key_fields=key_fields,
            yaml_scalar_field=yaml_scalar_field,
        )

    for name, (pointer, key_fields, yaml_scalar_field) in _YAML_ONLY_SECTIONS.items():
        layout = _yaml_only_section(
            pointer,
            config_file=config_file,
            key_fields=key_fields,
            yaml_scalar_field=yaml_scalar_field,
        )
        if layout is not None:
            layouts[name] = layout

    for table in ENTERPRISE_RECORD_STORE_NAMES:
        layouts[table] = _db_table(table)

    return layouts


def build_gateway_store_registry(
    *,
    persistent_root: Path | None = None,
    config_file: Path | None = None,
) -> StoreRegistry:
    """装配 name 对应的落盘布局。

    personal 传入绝对 ``persistent_root`` / ``config_file``；
    enterprise 可不传（file 为空，YAML-only name 不注册，只挂 DB）。
    """
    registry = StoreRegistry()
    registry.register_many(
        _build_layouts(persistent_root=persistent_root, config_file=config_file)
    )
    return registry


__all__ = ["build_gateway_store_registry"]
