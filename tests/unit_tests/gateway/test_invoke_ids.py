# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""EE invoke_ids：默认 service_id / agent_id 拼接。"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_EXT_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "jiuwenclaw-ee"
    / "gateway"
    / "extensions"
    / "runtime_management_extension"
)
_PKG = "_ee_runtime_management_ext"
if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_EXT_DIR)]
    _pkg.__package__ = _PKG
    sys.modules[_PKG] = _pkg


def _load(name: str):
    full = f"{_PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, _EXT_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PKG
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_invoke_mod = _load("invoke_ids")
_default_invoke_ids = _invoke_mod._default_invoke_ids
_routing_bot_id = _invoke_mod._routing_bot_id


def test_default_invoke_ids_concatenates_group_bot_user(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_BOT_ID_GROUP_NUM", raising=False)
    svc, ag = _default_invoke_ids("grp", "bot", "user")
    assert svc == "grpbot"
    assert ag == "grpbotuser"


def test_routing_bot_id_buckets_when_group_num_set(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BOT_ID_GROUP_NUM", "4")
    routed = _routing_bot_id("bot-1")
    assert routed.startswith("b")
    assert routed in {f"b{i}" for i in range(4)}


def test_routing_bot_id_invalid_group_num_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BOT_ID_GROUP_NUM", "not-a-number")
    assert _routing_bot_id("bot-1") == "bot-1"


def test_installed_skill_resolve_uses_invoke_ids_extension(monkeypatch) -> None:
    import sys

    from jiuwenswarm.agents.harness.common.installed_skill import resolve_final_tenant_ids

    pkg = types.ModuleType("openjiuwen_runtime_management_extension")
    pkg.__path__ = []
    mod = types.ModuleType("openjiuwen_runtime_management_extension.invoke_ids")

    def _stub_default_invoke_ids(group_id: str, bot_id: str, user_id: str) -> tuple[str, str]:
        return f"svc-{group_id}", f"ag-{group_id}{bot_id}{user_id}"

    mod._default_invoke_ids = _stub_default_invoke_ids
    monkeypatch.setitem(sys.modules, "openjiuwen_runtime_management_extension", pkg)
    monkeypatch.setitem(sys.modules, "openjiuwen_runtime_management_extension.invoke_ids", mod)

    svc, ag = resolve_final_tenant_ids(group_id="g", bot_id="b", user_id="u")
    assert svc == hashlib.md5(b"svc-g").hexdigest()
    assert ag == hashlib.md5(b"ag-gbu").hexdigest()
