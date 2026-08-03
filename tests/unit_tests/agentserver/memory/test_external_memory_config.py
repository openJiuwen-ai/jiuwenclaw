# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for external_memory_config.

Covers engine policy gates, external config reader, and OpenJiuwen provider
config mapping. Uses sys.modules stubs to avoid heavy deps at import time.
"""

import sys
from pathlib import Path
from types import ModuleType

import pytest


# ---------------------------------------------------------------------------
# Stub heavy deps before importing the module under test
# ---------------------------------------------------------------------------

def _ensure_module(name: str) -> ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = ModuleType(name)
        sys.modules[name] = mod
    return mod


def _install_stubs():
    ruamel = _ensure_module("ruamel")
    ruamel_yaml = _ensure_module("ruamel.yaml")
    ruamel.yaml = ruamel_yaml
    if not hasattr(ruamel_yaml, "YAML"):
        class _YAML:
            def __init__(self, *a, **k):
                pass

            @staticmethod
            def load(*a, **k):
                return {}

            @staticmethod
            def dump(*a, **k):
                pass
        ruamel_yaml.YAML = _YAML


_install_stubs()


def _get_config_file():
    return Path("/tmp/test_config.yaml")


def _get_agent_workspace_dir():
    return Path("/tmp/test_workspace")


# Load the real jiuwenclaw.utils (do NOT replace it in sys.modules — that
# leaks str-returning stubs into every later test in the session). Patch
# only the two attrs we need; the module-scoped autouse fixture in this
# package's conftest.py restores them after this module's tests finish.
import jiuwenclaw.utils as utils_stub  # noqa: E402

utils_stub.get_config_file = _get_config_file
utils_stub.get_agent_workspace_dir = _get_agent_workspace_dir

from jiuwenclaw.agentserver.memory import external_memory_config as emc  # noqa: E402


# ---------------------------------------------------------------------------
# get_memory_engine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("builtin", "builtin"),
    ("external", "external"),
    ("both", "both"),
    ("none", "none"),
    ("BUILTIN", "builtin"),
    ("  both ", "both"),
])
def test_get_memory_engine_valid_values(value, expected):
    assert emc.get_memory_engine({"memory": {"engine": value}}) == expected

# ---------------------------------------------------------------------------
# Engine gates truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine, builtin_ok, external_ok", [
    ("builtin", True, False),
    ("external", False, True),
    ("both", True, True),
    ("none", False, False),
])
def test_engine_gates_truth_table(engine, builtin_ok, external_ok):
    cfg = {"memory": {"engine": engine}}
    assert emc.is_builtin_memory_allowed(cfg) is builtin_ok
    assert emc.is_external_memory_allowed(cfg) is external_ok


# ---------------------------------------------------------------------------
# get_external_memory_config
# ---------------------------------------------------------------------------

def test_external_config_defaults_when_missing():
    out = emc.get_external_memory_config({"memory": {}})
    assert out["provider"] == ""
    assert out["user_id"] == "__default__"
    assert out["scope_id"] == "__default__"
    assert out["allowed_plugins"] == []
    assert out["openjiuwen"] == {}
    assert out["mem0"] == {}
    assert out["openviking"] == {}


def test_external_config_values_passthrough():
    cfg = {"memory": {"external": {
        "provider": "mem0",
        "user_id": "alice",
        "scope_id": "proj-a",
        "mem0": {"api_key": "sk-xxx"},
    }}}
    out = emc.get_external_memory_config(cfg)
    assert out["provider"] == "mem0"
    assert out["user_id"] == "alice"
    assert out["scope_id"] == "proj-a"
    assert out["mem0"] == {"api_key": "sk-xxx"}


def test_external_config_provider_whitespace_stripped():
    cfg = {"memory": {"external": {"provider": "  openjiuwen  "}}}
    assert emc.get_external_memory_config(cfg)["provider"] == "openjiuwen"


def test_external_config_malformed_section_tolerated():
    cfg = {"memory": {"external": "not-a-dict"}}
    assert emc.get_external_memory_config(cfg)["provider"] == ""


# ---------------------------------------------------------------------------
# is_external_memory_enabled  (engine gate AND provider non-empty)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cfg, expected", [
    # engine gate allows, provider present → enabled
    ({"memory": {"engine": "external", "external": {"provider": "mem0"}}}, True),
    ({"memory": {"engine": "both", "external": {"provider": "openjiuwen"}}}, True),
    # engine gate blocks, regardless of provider
    ({"memory": {"engine": "builtin", "external": {"provider": "mem0"}}}, False),
    ({"memory": {"engine": "none", "external": {"provider": "mem0"}}}, False),
    # engine allows but provider empty / missing
    ({"memory": {"engine": "external", "external": {"provider": ""}}}, False),
    ({"memory": {"engine": "external"}}, False),
])
def test_is_external_memory_enabled(cfg, expected):
    assert emc.is_external_memory_enabled(cfg) is expected


# ---------------------------------------------------------------------------
# build_openjiuwen_provider_config  (mapping to provider-expected shape)
# ---------------------------------------------------------------------------

def _patch_embed(monkeypatch, api_key="k", base_url="u", model="m"):
    monkeypatch.setattr(
        emc, "get_embed_config",
        lambda: {"api_key": api_key, "base_url": base_url, "model": model},
    )


def test_openjiuwen_provider_config_defaults(monkeypatch, tmp_path):
    _patch_embed(monkeypatch)
    monkeypatch.setattr(
        emc,
        "_resolve_ltm_dir",
        lambda service_id=None, agent_id=None: tmp_path / "ltm",
    )
    out, _scope = emc.build_openjiuwen_provider_config({"openjiuwen": {}})
    assert out["kv"]["backend"] == "shelve"
    assert out["vector"]["backend"] == "chroma"
    assert out["db"]["backend"] == "sqlite"
    assert out["kv"]["path"] == str(tmp_path / "ltm" / "kv")
    assert out["vector"]["persist_directory"] == str(tmp_path / "ltm" / "chroma")
    assert out["db"]["path"] == str(tmp_path / "ltm" / "ltm.db")
    assert out["embedding"]["model_name"] == "m"
    assert out["embedding"]["api_key"] == "k"


def test_openjiuwen_provider_config_overrides_applied(monkeypatch):
    _patch_embed(monkeypatch)
    ext_cfg = {"openjiuwen": {
        "kv_type": "in_memory",
        "kv_path": "/custom/kv",
        "vector_type": "chroma",
        "vector_persist_dir": "/custom/vec",
        "db_type": "sqlite",
        "db_path": "/custom/db.sqlite",
    }}
    out, _scope = emc.build_openjiuwen_provider_config(ext_cfg)
    assert out["kv"] == {"backend": "in_memory", "path": "/custom/kv"}
    assert out["vector"]["persist_directory"] == "/custom/vec"
    assert out["db"]["path"] == "/custom/db.sqlite"


def test_openjiuwen_empty_paths_use_tenant_ltm_dir(monkeypatch, tmp_path):
    _patch_embed(monkeypatch)
    roots: dict[tuple[str | None, str | None], Path] = {}

    def fake_resolve(service_id=None, agent_id=None):
        key = (service_id, agent_id)
        if key not in roots:
            roots[key] = tmp_path / f"{service_id}_{agent_id}" / "ltm"
            roots[key].mkdir(parents=True, exist_ok=True)
        return roots[key]

    monkeypatch.setattr(emc, "_resolve_ltm_dir", fake_resolve)
    a, _ = emc.build_openjiuwen_provider_config(
        {"openjiuwen": {}},
        service_id="svc_a",
        agent_id="agent_a",
    )
    b, _ = emc.build_openjiuwen_provider_config(
        {"openjiuwen": {}},
        service_id="svc_b",
        agent_id="agent_b",
    )
    assert a["kv"]["path"] != b["kv"]["path"]
    assert "svc_a_agent_a" in a["kv"]["path"].replace("\\", "/")
    assert "svc_b_agent_b" in b["kv"]["path"].replace("\\", "/")


def test_openjiuwen_tip_env_overrides_config_path(monkeypatch, tmp_path):
    _patch_embed(monkeypatch)
    monkeypatch.setattr(
        emc,
        "_resolve_ltm_dir",
        lambda service_id=None, agent_id=None: tmp_path / "ltm",
    )
    monkeypatch.setattr(emc, "read_env", lambda name, default="": {
        "MEMORY_KV_PATH": "/tip/kv",
        "MEMORY_VECTOR_DIR": "/tip/vec",
        "MEMORY_DB_PATH": "/tip/db.sqlite",
    }.get(name, default))
    out, _ = emc.build_openjiuwen_provider_config({
        "openjiuwen": {
            "kv_path": "/config/kv",
            "vector_persist_dir": "/config/vec",
            "db_path": "/config/db.sqlite",
        }
    })
    assert out["kv"]["path"] == "/tip/kv"
    assert out["vector"]["persist_directory"] == "/tip/vec"
    assert out["db"]["path"] == "/tip/db.sqlite"


def test_openjiuwen_provider_config_missing_embedding_does_not_raise(monkeypatch, tmp_path):
    # When embed is absent the function must NOT raise; just warn.
    monkeypatch.setattr(
        emc, "get_embed_config",
        lambda: {"api_key": "", "base_url": "", "model": ""},
    )
    monkeypatch.setattr(
        emc,
        "_resolve_ltm_dir",
        lambda service_id=None, agent_id=None: tmp_path / "ltm",
    )
    for var in ("EMBED_API_KEY", "EMBED_BASE_URL", "EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    out, _scope = emc.build_openjiuwen_provider_config({"openjiuwen": {}})
    assert out["embedding"]["model_name"] == ""


def test_openjiuwen_provider_config_missing_subsection_uses_defaults(monkeypatch, tmp_path):
    _patch_embed(monkeypatch)
    monkeypatch.setattr(
        emc,
        "_resolve_ltm_dir",
        lambda service_id=None, agent_id=None: tmp_path / "ltm",
    )
    out, _scope = emc.build_openjiuwen_provider_config({})
    assert out["kv"]["backend"] == "shelve"
    assert out["vector"]["backend"] == "chroma"
    assert out["db"]["backend"] == "sqlite"


def test_resolve_ltm_dir_uses_tenant_workspace(monkeypatch, tmp_path):
    def fake_workspace(service_id=None, agent_id=None):
        return tmp_path / f"service_{service_id}" / f"agent_{agent_id}" / "agent" / "jiuwenclaw_workspace"

    monkeypatch.setattr(
        "jiuwenclaw.utils.resolve_tenant_agent_workspace_dir",
        fake_workspace,
    )
    resolved = emc._resolve_ltm_dir("office", "bot1")
    assert resolved == fake_workspace("office", "bot1") / "memory" / "ltm"
    assert resolved.is_dir()


# ---------------------------------------------------------------------------
# external_memory_fingerprint
# ---------------------------------------------------------------------------

def _base_external_cfg(**overrides):
    cfg = {
        "memory": {
            "engine": "external",
            "external": {
                "provider": "openjiuwen",
                "user_id": "alice",
                "openjiuwen": {
                    "kv_path": "/data/kv",
                    "vector_persist_dir": "/data/chroma",
                    "db_path": "/data/ltm.db",
                },
            },
        },
        "embed": {
            "embed_api_key": "k",
            "embed_base_url": "https://embed.example",
            "embed_model": "text-embedding-v3",
        },
    }
    cfg.update(overrides)
    return cfg


def test_external_memory_fingerprint_stable_for_same_config(monkeypatch):
    monkeypatch.setattr(emc, "get_embed_config", lambda: {})
    cfg = _base_external_cfg()
    assert emc.external_memory_fingerprint(cfg) == emc.external_memory_fingerprint(cfg)


@pytest.mark.parametrize("mutator", [
    lambda c: c["memory"]["external"].update({"provider": "mem0"}),
    lambda c: c["memory"]["external"].update({"user_id": "bob"}),
    lambda c: c["memory"]["external"]["openjiuwen"].update({"kv_path": "/other/kv"}),
    lambda c: c["embed"].update({"embed_model": "other-model"}),
])
def test_external_memory_fingerprint_changes_on_config_delta(monkeypatch, mutator):
    monkeypatch.setattr(emc, "get_embed_config", lambda: {})
    before = _base_external_cfg()
    after = _base_external_cfg()
    mutator(after)
    assert emc.external_memory_fingerprint(before) != emc.external_memory_fingerprint(after)


def test_external_memory_fingerprint_differs_by_tenant_when_paths_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(emc, "get_embed_config", lambda: {})

    def fake_resolve(service_id=None, agent_id=None):
        p = tmp_path / f"{service_id}_{agent_id}" / "ltm"
        p.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(emc, "_resolve_ltm_dir", fake_resolve)
    cfg = {
        "memory": {
            "engine": "external",
            "external": {"provider": "openjiuwen", "openjiuwen": {}},
        },
        "embed": {},
    }
    fp_a = emc.external_memory_fingerprint(cfg, service_id="s1", agent_id="a1")
    fp_b = emc.external_memory_fingerprint(cfg, service_id="s1", agent_id="a2")
    assert fp_a != fp_b


def test_external_memory_fingerprint_engine_gate(monkeypatch):
    monkeypatch.setattr(emc, "get_embed_config", lambda: {})
    external_cfg = _base_external_cfg()
    none_cfg = _base_external_cfg()
    none_cfg["memory"]["engine"] = "none"
    assert emc.external_memory_fingerprint(external_cfg) != emc.external_memory_fingerprint(none_cfg)
