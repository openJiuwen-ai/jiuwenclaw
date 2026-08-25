# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""config.resolve_env_vars 的 ${API_KEY} 密钥包兜底（桌面 env 剔密后的模型校验链路）。"""

from __future__ import annotations

import pytest

import jiuwenswarm.common.secrets_bootstrap as sb
from jiuwenswarm.common.config import resolve_env_vars


@pytest.fixture(autouse=True)
def _reset_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sb, "_SECRETS", {})
    monkeypatch.setattr(sb, "_LOADED", False)
    yield


class TestApiKeyVaultFallback:
    def test_api_key_falls_back_to_vault_proxy_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.setattr(sb, "_SECRETS", {"proxyKey": "mpk_vault"})
        monkeypatch.setattr(sb, "_LOADED", True)
        assert resolve_env_vars("${API_KEY}") == "mpk_vault"

    def test_env_wins_over_vault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY", "env-key")
        monkeypatch.setattr(sb, "_SECRETS", {"proxyKey": "mpk_vault"})
        monkeypatch.setattr(sb, "_LOADED", True)
        assert resolve_env_vars("${API_KEY}") == "env-key"

    def test_no_vault_no_env_resolves_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("API_KEY", raising=False)
        assert resolve_env_vars("${API_KEY}") == ""

    def test_other_vars_unaffected_by_vault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTHER_VAR", raising=False)
        monkeypatch.setattr(sb, "_SECRETS", {"proxyKey": "mpk_vault"})
        monkeypatch.setattr(sb, "_LOADED", True)
        assert resolve_env_vars("${OTHER_VAR}") == ""
        assert resolve_env_vars("${OTHER_VAR:-fallback}") == "fallback"
