# coding: utf-8
# pylint: disable=protected-access
"""Unit tests for skill credential injection in shell_pip_patch."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jiuwenclaw.runtime import shell_pip_patch


# ============================================================
# provider registration
# ============================================================

class TestSetSkillCredentialProvider:
    @staticmethod
    def test_provider_none_returns_environment_unchanged():
        """When no provider is registered, _apply_skill_credentials is a no-op."""
        try:
            shell_pip_patch.set_skill_credential_provider(None)
            result = shell_pip_patch._apply_skill_credentials({"A": "1"})
            assert result == {"A": "1"}
        finally:
            shell_pip_patch.set_skill_credential_provider(None)

    @staticmethod
    def test_provider_returns_empty_dict_returns_environment_unchanged():
        try:
            shell_pip_patch.set_skill_credential_provider(lambda: {})
            result = shell_pip_patch._apply_skill_credentials({"A": "1"})
            assert result == {"A": "1"}
        finally:
            shell_pip_patch.set_skill_credential_provider(None)


# ============================================================
# active_skill lookup + merge
# ============================================================

class TestApplySkillCredentials:
    _orig_provider: object

    def setup_method(self):
        self._orig_provider = shell_pip_patch._skill_envs_provider

    def teardown_method(self):
        shell_pip_patch._skill_envs_provider = self._orig_provider

    @staticmethod
    def test_merges_for_active_skill():
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"akg-agents": {"AKG_API_KEY": "xxx", "AKG_BASE_URL": "yyy"}}
        )
        with patch(
            "jiuwenclaw.runtime.shell_pip_patch._resolve_session_id_for_credentials",
            return_value="sess-1",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value="akg-agents",
        ):
            result = shell_pip_patch._apply_skill_credentials(None)
        assert result == {"AKG_API_KEY": "xxx", "AKG_BASE_URL": "yyy"}

    @staticmethod
    def test_does_not_overwrite_existing_keys():
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"akg-agents": {"AKG_API_KEY": "injected"}}
        )
        with patch(
            "jiuwenclaw.runtime.shell_pip_patch._resolve_session_id_for_credentials",
            return_value="sess-1",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value="akg-agents",
        ):
            result = shell_pip_patch._apply_skill_credentials({"AKG_API_KEY": "user-set"})
        assert result == {"AKG_API_KEY": "user-set"}

    @staticmethod
    def test_skips_when_no_active_skill():
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"akg-agents": {"AKG_API_KEY": "xxx"}}
        )
        with patch(
            "jiuwenclaw.runtime.shell_pip_patch._resolve_session_id_for_credentials",
            return_value="sess-1",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value=None,
        ):
            result = shell_pip_patch._apply_skill_credentials({"EXISTING": "v"})
        assert result == {"EXISTING": "v"}

    @staticmethod
    def test_skips_when_skill_envs_missing_for_skill():
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"other-skill": {"OTHER_KEY": "v"}}
        )
        with patch(
            "jiuwenclaw.runtime.shell_pip_patch._resolve_session_id_for_credentials",
            return_value="sess-1",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value="akg-agents",
        ):
            result = shell_pip_patch._apply_skill_credentials(None)
        assert result is None


# ============================================================
# wrapper integration
# ============================================================

class TestWrapperIntegration:
    _orig_provider: object

    def setup_method(self):
        self._orig_provider = shell_pip_patch._skill_envs_provider

    def teardown_method(self):
        shell_pip_patch._skill_envs_provider = self._orig_provider

    @staticmethod
    def test_wrap_execute_cmd_applies_skill_credentials():
        """The patched execute_cmd must merge skill creds before calling orig."""
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"akg-agents": {"AKG_API_KEY": "injected"}}
        )

        captured: dict = {}

        async def fake_orig(self_inner, command, *, environment=None, **kwargs):
            captured["environment"] = environment
            captured["command"] = command
            return "ok"

        wrapped = shell_pip_patch._wrap_execute_cmd(fake_orig)

        with patch(
            "jiuwenclaw.runtime.shell_pip_patch._resolve_session_id_for_credentials",
            return_value="sess-1",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value="akg-agents",
        ):
            import asyncio
            result = asyncio.run(
                wrapped(object(), "echo hello", environment=None)
            )

        assert result == "ok"
        # command passes through (rewritten by isolation, but for a non-pip cmd
        # it stays unchanged)
        assert captured["command"] == "echo hello"
        # credential is merged in
        assert captured["environment"] is not None
        assert captured["environment"]["AKG_API_KEY"] == "injected"

    @staticmethod
    def test_wrap_execute_cmd_stream_applies_skill_credentials():
        """The patched execute_cmd_stream must merge skill creds too."""
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"akg-agents": {"AKG_API_KEY": "injected"}}
        )

        captured: dict = {}

        async def fake_orig(self_inner, command, *, environment=None, **kwargs):
            captured["environment"] = environment
            yield "chunk-1"

        wrapped = shell_pip_patch._wrap_execute_cmd_stream(fake_orig)

        with patch(
            "jiuwenclaw.runtime.shell_pip_patch._resolve_session_id_for_credentials",
            return_value="sess-1",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value="akg-agents",
        ):
            import asyncio

            async def collect():
                out = []
                async for item in wrapped(object(), "echo hello", environment=None):
                    out.append(item)
                return out

            result = asyncio.run(collect())

        assert result == ["chunk-1"]
        assert captured["environment"] is not None
        assert captured["environment"]["AKG_API_KEY"] == "injected"


# ============================================================
# patch scope: LOCAL + SANDBOX
# ============================================================

class TestPatchScopeBothModes:
    @staticmethod
    def test_apply_shell_pip_isolation_patch_patches_local_and_sandbox():
        """apply_shell_pip_isolation_patch must patch both ShellOperation classes."""
        from openjiuwen.core.sys_operation.local.shell_operation import (
            ShellOperation as LocalShellOperation,
        )
        from openjiuwen.core.sys_operation.sandbox.shell_operation import (
            ShellOperation as SandboxShellOperation,
        )

        # patch is idempotent; safe to call again
        shell_pip_patch.apply_shell_pip_isolation_patch()

        assert getattr(LocalShellOperation, "_jiuwenclaw_pip_isolation_patched_local", False) is True
        assert getattr(SandboxShellOperation, "_jiuwenclaw_pip_isolation_patched_sandbox", False) is True


# ============================================================
# background command path
# ============================================================

class TestBackgroundCredentialInjection:
    _orig_provider: object

    def setup_method(self):
        self._orig_provider = shell_pip_patch._skill_envs_provider

    def teardown_method(self):
        shell_pip_patch._skill_envs_provider = self._orig_provider

    @staticmethod
    def test_patched_execute_cmd_background_injects_credentials():
        """execute_cmd_background wrapper must also merge skill creds."""
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"akg-agents": {"AKG_API_KEY": "injected"}}
        )

        captured: dict = {}

        async def fake_orig(self_inner, command, *, environment=None, **kwargs):
            captured["environment"] = environment
            captured["command"] = command
            return "bg-ok"

        # _wrap_execute_cmd is reused for background — same wrapper contract
        wrapped = shell_pip_patch._wrap_execute_cmd(fake_orig)

        with patch(
            "jiuwenclaw.runtime.shell_pip_patch._resolve_session_id_for_credentials",
            return_value="sess-1",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value="akg-agents",
        ):
            import asyncio
            result = asyncio.run(
                wrapped(object(), "sleep 3600", environment=None)
            )

        assert result == "bg-ok"
        assert captured["environment"] is not None
        assert captured["environment"]["AKG_API_KEY"] == "injected"


# ============================================================
# adapter registration contract
# ============================================================

class TestAdapterProviderRegistration:
    """Verify the lambda registered by the adapter reads the current rail's envs."""

    def test_provider_lambda_reads_current_rail_envs(self, monkeypatch: pytest.MonkeyPatch):
        """When the rail is replaced or skill_envs is updated, the provider sees the new value."""
        # Simulate the adapter's registration lambda (mirrors the one in __init__).
        class FakeAdapter:
            def __init__(self):
                self._skill_credential_injection_rail = None

            def _register(self):
                shell_pip_patch.set_skill_credential_provider(
                    lambda: (
                        self._skill_credential_injection_rail.get_skill_envs()
                        if self._skill_credential_injection_rail is not None
                        else {}
                    )
                )

        try:
            adapter = FakeAdapter()
            adapter._register()

            # rail is None -> empty dict
            assert shell_pip_patch._skill_envs_provider() == {}

            # simulate rail creation: a fake rail exposing get_skill_envs()
            fake_rail = type(
                "FakeRail",
                (),
                {
                    "_skill_envs": {"akg-agents": {"AKG_API_KEY": "v1"}},
                    "get_skill_envs": lambda self: self._skill_envs,
                },
            )()
            adapter._skill_credential_injection_rail = fake_rail
            assert shell_pip_patch._skill_envs_provider() == {"akg-agents": {"AKG_API_KEY": "v1"}}

            # simulate update_skill_envs: replace _skill_envs reference on same rail
            fake_rail._skill_envs = {"akg-agents": {"AKG_API_KEY": "v2"}}
            assert shell_pip_patch._skill_envs_provider() == {"akg-agents": {"AKG_API_KEY": "v2"}}

            # simulate rail replacement: new instance with new envs
            new_rail = type(
                "FakeRail",
                (),
                {
                    "_skill_envs": {"other-skill": {"OTHER": "v"}},
                    "get_skill_envs": lambda self: self._skill_envs,
                },
            )()
            adapter._skill_credential_injection_rail = new_rail
            assert shell_pip_patch._skill_envs_provider() == {"other-skill": {"OTHER": "v"}}
        finally:
            shell_pip_patch.set_skill_credential_provider(None)


# ============================================================
# session_id resolution (ContextVar-only)
# ============================================================

class TestSessionIdResolution:
    """_resolve_session_id_for_credentials reads the ContextVar set by the rail."""

    @staticmethod
    def test_resolve_session_id_uses_contextvar_when_set():
        from jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail import (
            _current_session_var,
        )
        token = _current_session_var.set("ctx-session")
        try:
            sid = shell_pip_patch._resolve_session_id_for_credentials()
            assert sid == "ctx-session"
        finally:
            _current_session_var.reset(token)

    @staticmethod
    def test_resolve_session_id_falls_to_default_when_contextvar_unset():
        from jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail import (
            _current_session_var,
        )
        token = _current_session_var.set(None)
        try:
            sid = shell_pip_patch._resolve_session_id_for_credentials()
            assert sid == "default"
        finally:
            _current_session_var.reset(token)


# ============================================================
# provider exception safety (I1) + None preservation (I2)
# ============================================================

class TestProviderExceptionSafety:
    """I1: a failing provider must not break every shell command."""

    _orig_provider: object

    def setup_method(self):
        self._orig_provider = shell_pip_patch._skill_envs_provider

    def teardown_method(self):
        shell_pip_patch._skill_envs_provider = self._orig_provider

    @staticmethod
    def test_provider_that_raises_returns_environment_unchanged():
        """If the provider callback raises, _apply_skill_credentials logs and returns env unchanged."""

        def bad_provider():
            raise RuntimeError("simulated rail teardown")

        shell_pip_patch.set_skill_credential_provider(bad_provider)
        result = shell_pip_patch._apply_skill_credentials({"A": "1"})
        assert result == {"A": "1"}

    @staticmethod
    def test_provider_that_raises_preserves_none():
        """If provider raises and environment is None, return None (not {})."""

        def bad_provider():
            raise RuntimeError("simulated rail teardown")

        shell_pip_patch.set_skill_credential_provider(bad_provider)
        result = shell_pip_patch._apply_skill_credentials(None)
        assert result is None


class TestNonePreservation:
    """I2: no-op paths must preserve None rather than converting to {}."""

    _orig_provider: object

    def setup_method(self):
        self._orig_provider = shell_pip_patch._skill_envs_provider

    def teardown_method(self):
        shell_pip_patch._skill_envs_provider = self._orig_provider

    @staticmethod
    def test_no_active_skill_preserves_none():
        """When provider returns envs but no active skill, None must pass through."""
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"akg-agents": {"AKG_API_KEY": "x"}}
        )
        with patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value=None,
        ):
            result = shell_pip_patch._apply_skill_credentials(None)
        assert result is None

    @staticmethod
    def test_skill_envs_missing_for_active_skill_preserves_none():
        """When active skill has no creds entry, None must pass through."""
        shell_pip_patch.set_skill_credential_provider(
            lambda: {"other-skill": {"OTHER": "x"}}
        )
        with patch(
            "jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.get_session_active_skill",
            return_value="akg-agents",
        ):
            result = shell_pip_patch._apply_skill_credentials(None)
        assert result is None


# ============================================================
# patch scope: all three methods actually wrapped (I3)
# ============================================================

class TestPatchScopeAllMethodsWrapped:
    """I3: assert each ShellOperation method is actually a wrapped function."""

    @staticmethod
    def test_all_three_methods_are_wrapped_on_local():
        import openjiuwen.core.sys_operation.local.shell_operation as local_mod
        # Reload fresh class reference to capture pre-patch state is infeasible
        # post-patch; instead verify the wrappers carry the @wraps(orig) name
        # which the unwrapped methods would not have set to "patched".
        shell_pip_patch.apply_shell_pip_isolation_patch()
        # Each patched method must have __wrapped__ pointing at the original.
        assert hasattr(local_mod.ShellOperation.execute_cmd, "__wrapped__")
        assert hasattr(local_mod.ShellOperation.execute_cmd_stream, "__wrapped__")
        assert hasattr(local_mod.ShellOperation.execute_cmd_background, "__wrapped__")

    @staticmethod
    def test_all_three_methods_are_wrapped_on_sandbox():
        import openjiuwen.core.sys_operation.sandbox.shell_operation as sandbox_mod
        shell_pip_patch.apply_shell_pip_isolation_patch()
        assert hasattr(sandbox_mod.ShellOperation.execute_cmd, "__wrapped__")
        assert hasattr(sandbox_mod.ShellOperation.execute_cmd_stream, "__wrapped__")
        assert hasattr(sandbox_mod.ShellOperation.execute_cmd_background, "__wrapped__")
