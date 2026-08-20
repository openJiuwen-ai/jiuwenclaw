# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""回归：extension_config_sync 持久化使用实时 config 路径，而非 import 期冻结常量。"""

from __future__ import annotations

import importlib

import yaml


def test_update_extensions_in_config_writes_to_live_path_not_frozen_constant(monkeypatch, tmp_path):
    """回归：update_extensions_in_config 用实时路径，不写 import 期冻结常量指向的诱饵文件。

    独立持久化 ``extensions`` 段（与权限规则、CLI 信任目录、文件操作权限不同段）。
    若该 writer 被误改回 ``_CONFIG_YAML_PATH``，本测试应捕获。
    """
    live_config = tmp_path / "config.yaml"
    live_config.write_text("permissions: {enabled: true}\n", encoding="utf-8")
    frozen_config = tmp_path / "frozen_resources_config.yaml"
    frozen_config.write_text("permissions: {enabled: true}\n", encoding="utf-8")

    config_mod = importlib.import_module("jiuwenclaw.config")
    monkeypatch.setattr(config_mod, "_CONFIG_YAML_PATH", frozen_config)
    monkeypatch.setattr(config_mod, "_current_config_yaml_path", lambda: live_config)

    from jiuwenclaw.extensions.extension_config_sync import update_extensions_in_config
    update_extensions_in_config(extension_configs='{"ext": {}}')

    live_saved = yaml.safe_load(live_config.read_text(encoding="utf-8"))
    frozen_saved = yaml.safe_load(frozen_config.read_text(encoding="utf-8"))
    assert live_saved.get("extensions", {}).get("extension_configs") == '{"ext": {}}'
    assert not frozen_saved.get("extensions"), "诱饵文件不应被写入"
