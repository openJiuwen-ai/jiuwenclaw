#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Toggle JiuwenClaw security review and permission checks in config.yaml."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


SECURITY_REVIEW_ON_VALUES: dict[str, Any] = {
    "enabled": True,
    "runtime_advice": True,
    "async_review": True,
    "evolve_security_skills": True,
    "propose_policy_rules": True,
    "timely_tool_failure_review": True,
}


def default_config_path() -> Path:
    explicit = os.environ.get("JIUWENCLAW_CONFIG_FILE")
    if explicit:
        return Path(explicit).expanduser()
    data_dir = Path(os.environ.get("JIUWENCLAW_DATA_DIR", "~/.jiuwenclaw")).expanduser()
    return data_dir / "config" / "config.yaml"


def apply_security_config(
    config_path: str | Path,
    *,
    enabled: bool,
    create_backup: bool = True,
) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    data = _load_config(path)
    backup_path = _backup_config(path) if create_backup else None

    react = _ensure_map(data, "react")
    security_review = _ensure_map(react, "security_review")
    permissions = _ensure_map(data, "permissions")

    if enabled:
        for key, value in SECURITY_REVIEW_ON_VALUES.items():
            security_review[key] = value
        permissions["enabled"] = True
        permissions["schema"] = "tiered_policy"
        permissions.setdefault("permission_mode", "normal")
        defaults = _ensure_map(permissions, "defaults")
        defaults.setdefault("*", "allow")
    else:
        security_review["enabled"] = False
        permissions["enabled"] = False

    _dump_config(path, data)
    status = read_security_config_status(path)
    status["mode"] = "on" if enabled else "off"
    status["backup"] = str(backup_path) if backup_path is not None else None
    return status


def read_security_config_status(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    data = _load_config(path)
    react = data.get("react")
    security_review = react.get("security_review") if isinstance(react, dict) else None
    permissions = data.get("permissions")
    if not isinstance(security_review, dict):
        security_review = {}
    if not isinstance(permissions, dict):
        permissions = {}

    return {
        "config": str(path),
        "security_review_enabled": security_review.get("enabled"),
        "runtime_advice_enabled": security_review.get("runtime_advice"),
        "permissions_enabled": permissions.get("enabled"),
        "permissions_schema": permissions.get("schema"),
        "permission_mode": permissions.get("permission_mode"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Toggle JiuwenClaw security review and permission checks."
    )
    parser.add_argument("mode", choices=("on", "off", "status"), help="security config action")
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="path to config.yaml; defaults to ~/.jiuwenclaw/config/config.yaml",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="do not create a timestamped config backup for on/off",
    )
    args = parser.parse_args(argv)

    if args.mode == "status":
        result = read_security_config_status(args.config)
    else:
        result = apply_security_config(
            args.config,
            enabled=args.mode == "on",
            create_backup=not args.no_backup,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _load_config(path: Path) -> CommentedMap:
    if not path.is_file():
        raise FileNotFoundError(f"config.yaml not found: {path}")
    yaml = _yaml()
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.load(handle)
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        raise ValueError("config root must be a YAML mapping")
    return data


def _dump_config(path: Path, data: CommentedMap) -> None:
    yaml = _yaml()
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


def _backup_config(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = path.with_name(f"{path.name}.bak-security-{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def _ensure_map(parent: CommentedMap, key: str) -> CommentedMap:
    value = parent.get(key)
    if isinstance(value, CommentedMap):
        return value
    new_value = CommentedMap()
    parent[key] = new_value
    return new_value


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


if __name__ == "__main__":
    raise SystemExit(main())
