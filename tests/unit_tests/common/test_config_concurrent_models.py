# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""并发写 models.defaults 的回归测试：验证乐观锁不丢失条目。

复现原 bug 场景：多线程同时 add model，裸 load-modify-dump 会丢失更新，
乐观锁（update_config）应保证全部条目最终都在配置里。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

import jiuwenswarm.common.config as cfg_mod


def _seed_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "defaults": [
                        {
                            "model_client_config": {
                                "api_base": "https://base.example.com/v1",
                                "api_key": "seed-key",
                                "model_name": "seed-model",
                                "client_provider": "OpenAI",
                            },
                            "model_config_obj": {"temperature": 0.95},
                            "is_default": True,
                        }
                    ]
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _make_entry(name: str) -> dict[str, Any]:
    return {
        "model_client_config": {
            "api_base": f"https://m{name}.example.com/v1",
            "api_key": f"key-{name}",
            "model_name": name,
            "client_provider": "OpenAI",
        },
        "model_config_obj": {"temperature": 0.95},
        "is_default": False,
    }


@pytest.fixture
def patched_config(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    _seed_config(cfg)
    monkeypatch.setattr(cfg_mod, "CONFIG_YAML_PATH", cfg)
    return cfg


def test_concurrent_add_no_lost_update(patched_config):
    """N 线程并发 append model：最终所有 N+seed 条目都在 defaults 里。

    用单事务 update_config（读最新 + 追加 + 写回）模拟正确用法，验证文件锁
    在并发下不丢条目。对比：若用 ensure_defaults_list + update_default_models
    两步（中间无锁），会丢更新——这正是原 bug。
    """
    names = [f"glm-{i}" for i in range(20)]
    errors: list[BaseException] = []

    def _add(name: str) -> None:
        entry = _make_entry(name)
        try:
            def _mutate(data):
                models = data.get("models") or {}
                if not isinstance(models, dict):
                    models = {}
                defs = models.get("defaults")
                defs = list(defs) if isinstance(defs, list) else []
                if any(isinstance(d, dict) and d.get("model_client_config", {}).get("model_name") == name
                       for d in defs):
                    return None
                defs.append(entry)
                models["defaults"] = defs
                data["models"] = models
                if "default" in data["models"]:
                    del data["models"]["default"]
                return data
            cfg_mod.update_config(_mutate)
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=_add, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发写入出现异常: {errors}"

    data = cfg_mod.load_yaml_round_trip(patched_config)
    defaults = data["models"]["defaults"]
    got_names = {
        e["model_client_config"]["model_name"] for e in defaults if isinstance(e, dict)
    }
    expected = set(names) | {"seed-model"}
    assert got_names == expected, (
        f"丢失更新: 期望 {len(expected)} 个模型, 实际 {len(got_names)} 个; "
        f"缺失: {expected - got_names}"
    )


def test_ensure_defaults_creates_placeholder_when_missing(patched_config):
    """defaults 不存在时写入 ${API_BASE} 等模板占位符条目（保持原契约，供 _config_set 后续填充）。"""
    raw = cfg_mod.load_yaml_round_trip(patched_config)
    raw["models"].pop("defaults")
    raw["models"].pop("default", None)
    cfg_mod.dump_yaml_round_trip(patched_config, raw)

    defs = cfg_mod.ensure_defaults_list_in_config()
    assert isinstance(defs, list) and len(defs) == 1
    mcc = defs[0].get("model_client_config", {})
    assert mcc.get("api_base") == "${API_BASE}"
    assert mcc.get("model_name") == "${MODEL_NAME}"

    data = cfg_mod.load_yaml_round_trip(patched_config)
    written = data["models"].get("defaults")
    assert isinstance(written, list) and len(written) == 1
    assert written[0].get("model_client_config", {}).get("api_key") == "${API_KEY}"


def test_update_config_retries_on_concurrent_change(patched_config):
    """并发写同一字段时 update_config 不丢计数（文件锁串行化，无丢失更新）。"""
    barrier = threading.Barrier(2)
    results: dict[str, int] = {"ok": 0}

    def _writer() -> None:
        barrier.wait()
        for _ in range(10):

            def _m(data):
                data["counter"] = data.get("counter", 0) + 1
                return data

            cfg_mod.update_config(_m)
            results["ok"] += 1

    ts = [threading.Thread(target=_writer) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    data = cfg_mod.load_yaml_round_trip(patched_config)
    assert data.get("counter") == 20, f"丢失计数: {data.get('counter')}"
