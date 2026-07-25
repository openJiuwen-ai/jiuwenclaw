"""测试 extensions/auth 扩展的条件注册（不依赖完整 gateway/openjiuwen 运行时）。"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_EXT_PATH = (
        Path(__file__).resolve().parents[2]
        / "jiuwenswarm"
        / "extensions"
        / "auth"
        / "extension.py"
)


def _load_extension_module(auth_config: dict | None = None):
    spec = importlib.util.spec_from_file_location(
        "jiuwenswarm_auth_extension_under_test",
        _EXT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    cfg = {"extensions": {"auth": auth_config or {}}}
    with patch.dict(
            "sys.modules",
            {
                "jiuwenswarm.common.config": SimpleNamespace(
                    get_config=lambda: cfg,
                    resolve_env_vars=lambda v: v,
                ),
                "jiuwenswarm.common.utils": SimpleNamespace(logger=MagicMock()),
            },
    ):
        spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_passthrough_does_not_register_agentos():
    mod = _load_extension_module({"type": "passthrough"})
    registry = MagicMock()

    result = await mod.register_extensions(registry)

    assert result == []
    registry.register_authenticator.assert_not_called()


@pytest.mark.asyncio
async def test_agentos_registers_via_lazy_import():
    mod = _load_extension_module(
        {
            "type": "agentos",
            "agentos": {
                "auth_service_url": "http://iam-test:8080",
                "gateway_secret_key": "secret",
            },
        }
    )
    registry = MagicMock()
    fake_auth = object()
    fake_cls = MagicMock(return_value=fake_auth)
    fake_mod = types.ModuleType("jiuwenswarm.extensions.auth.agentos_authenticator")
    fake_mod.AgentOSAuthenticator = fake_cls
    pkg_auth = types.ModuleType("jiuwenswarm.extensions.auth")
    pkg_auth.__path__ = []  # type: ignore[attr-defined]

    with patch.dict(
            sys.modules,
            {
                "jiuwenswarm.extensions.auth": pkg_auth,
                "jiuwenswarm.extensions.auth.agentos_authenticator": fake_mod,
            },
    ):
        result = await mod.register_extensions(registry)

    assert result == []
    fake_cls.assert_called_once_with(
        auth_service_url="http://iam-test:8080",
        gateway_secret_key="secret",
    )
    registry.register_authenticator.assert_called_once_with(fake_auth)


@pytest.mark.asyncio
async def test_agentos_requires_auth_service_url():
    mod = _load_extension_module({"type": "agentos", "agentos": {}})
    registry = MagicMock()

    with pytest.raises(ValueError, match="auth_service_url"):
        await mod.register_extensions(registry)