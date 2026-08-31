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


class TestXiaoyiVaultFallback:
    """${CLAW_XIAOYI_*}（config.yaml 的 xiaoyi 渠道段占位符）经密钥包兜底。

    桌面形态 gateway 进程 env 零秘密（密钥只进 stdin 首帧 vault），渠道段
    ak/sk/agent_id/uid/api_key 占位符必须从 vault 解析，否则 gateway 启动判定
    「missing ak,sk,agent_id」渠道不启动（手机端消息无法下达到本地）。
    """

    _VAULT = {
        "proxyKey": "mpk_vault",
        "localAuth": {"ak": "ak_v", "sk": "sk_v", "agentId": "agent_v"},
        "uid": "uid_v",
        "apiKey": "key_v",
    }

    def test_xiaoyi_placeholders_resolve_from_vault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("CLAW_XIAOYI_AK", "CLAW_XIAOYI_SK", "CLAW_XIAOYI_AGENT_ID", "CLAW_XIAOYI_UID", "CLAW_XIAOYI_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(sb, "_SECRETS", self._VAULT)
        monkeypatch.setattr(sb, "_LOADED", True)
        out = resolve_env_vars(
            {
                "ak": "${CLAW_XIAOYI_AK}",
                "sk": "${CLAW_XIAOYI_SK}",
                "agent_id": "${CLAW_XIAOYI_AGENT_ID}",
                "uid": "${CLAW_XIAOYI_UID}",
                "api_key": "${CLAW_XIAOYI_API_KEY}",
            }
        )
        assert out == {
            "ak": "ak_v",
            "sk": "sk_v",
            "agent_id": "agent_v",
            "uid": "uid_v",
            "api_key": "key_v",
        }

    def test_env_wins_over_vault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAW_XIAOYI_AK", "ak_env")
        monkeypatch.setattr(sb, "_SECRETS", self._VAULT)
        monkeypatch.setattr(sb, "_LOADED", True)
        assert resolve_env_vars("${CLAW_XIAOYI_AK}") == "ak_env"

    def test_no_vault_resolves_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAW_XIAOYI_AK", raising=False)
        assert resolve_env_vars("${CLAW_XIAOYI_AK}") == ""

