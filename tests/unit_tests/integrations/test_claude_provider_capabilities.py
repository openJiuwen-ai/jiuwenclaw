"""Capability-registry tests for the Claude provider (Phase 3a registration).

Verifies the additive Claude branch in ``provider_capabilities`` and that the
Codex branch is unchanged. Portable across environments: if the (pre-existing)
``openjiuwen.core.foundation.llm.utils`` submodule is missing from a local
install, a minimal stub is injected so the Claude/Codex logic can still run. In
full CI the real module is present and no stub is used.
"""

from __future__ import annotations

import sys
import types


def _ensure_provider_utils() -> None:
    name = "openjiuwen.core.foundation.llm.utils.provider_utils"
    try:  # pragma: no cover - exercised differently per environment
        __import__(name)
        return
    except ModuleNotFoundError:
        pass
    pkg = types.ModuleType("openjiuwen.core.foundation.llm.utils")
    mod = types.ModuleType(name)
    mod.is_openai_account_provider = lambda value: str(value) == "OpenAIAccount"
    sys.modules.setdefault("openjiuwen.core.foundation.llm.utils", pkg)
    sys.modules.setdefault(name, mod)


_ensure_provider_utils()

# Imports intentionally follow the stub installer above so a missing local
# openjiuwen submodule does not break collection (see module docstring).
from jiuwenswarm.integrations.ai4research_subscription import provider_capabilities as pc  # noqa: E402
from jiuwenswarm.integrations.ai4research_subscription.claude_constants import (  # noqa: E402
    CLAUDE_MODEL_ALIAS,
    CLAUDE_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.constants import (  # noqa: E402
    CODEX_MODEL_ALIAS,
    CODEX_PROVIDER_NAME,
)


def test_claude_is_credential_free_config_without_auth_controller():
    cap = pc.get_model_provider_capabilities(CLAUDE_PROVIDER_NAME)
    # No credential in Jiuwen config: the CLI resolves creds from the environment.
    assert cap.requires_api_key is False
    assert cap.requires_api_base is False
    assert cap.subscription_auth is False  # no in-product auth controller
    assert cap.fixed_model_name == CLAUDE_MODEL_ALIAS


def test_codex_capabilities_unchanged():
    cap = pc.get_model_provider_capabilities(CODEX_PROVIDER_NAME)
    assert cap.requires_api_key is False
    assert cap.requires_api_base is False
    assert cap.subscription_auth is True
    assert cap.fixed_model_name == CODEX_MODEL_ALIAS


def test_both_providers_advertised():
    names = pc.available_model_provider_names()
    assert CLAUDE_PROVIDER_NAME in names
    assert CODEX_PROVIDER_NAME in names


def test_credential_free_config_rules():
    base = {"model_name": CLAUDE_MODEL_ALIAS, "client_provider": CLAUDE_PROVIDER_NAME, "api_base": ""}
    # A config with no credential is usable (creds come from the environment).
    assert pc.model_client_config_looks_usable({**base, "api_key": ""}) is True
    # A credential placed in Jiuwen config is rejected (must be empty).
    assert pc.model_client_config_looks_usable({**base, "api_key": "sk-op"}) is False
    assert (
        pc.model_client_config_looks_usable({**base, "api_key": "", "api_base": "https://x.invalid"})
        is False
    )


def test_claude_fixed_model_alias_enforced():
    assert pc.validate_provider_model_name(CLAUDE_PROVIDER_NAME, "some-other-model") is False
    assert pc.validate_provider_model_name(CLAUDE_PROVIDER_NAME, CLAUDE_MODEL_ALIAS) is True


def test_no_credential_fields_required_for_claude():
    missing = pc.missing_model_fields(
        model_name=CLAUDE_MODEL_ALIAS,
        model_provider=CLAUDE_PROVIDER_NAME,
        api_base="",
        api_key="",
    )
    # Neither credential is required in config.
    assert "api_key" not in missing
    assert "api_base" not in missing
