# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=protected-access

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.agent_manager import AgentManager
from jiuwenclaw.agentserver.reload_result import (
    AGENT_CONFIG_HOT_RELOAD_MARKER,
    AGENT_CONFIG_HOT_RELOAD_REPLAY_MARKER,
    ReloadAggregateResult,
    ReloadResult,
    collect_changed_config_paths,
    collect_env_override_keys,
    log_agent_config_hot_reload,
    log_agent_config_hot_reload_replay,
    log_reload_config_changes,
    redact_reload_error_message,
    reload_touches_memory,
    summarize_reload_payload,
)
from jiuwenclaw.agentserver.memory.cache_registry import clear_memory_cache_registry
from jiuwenclaw.agentserver.memory.manager import INDEX_CACHE
from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    bind_task_env_overlay,
    clear_staged_env,
    get_local_config,
    get_staged_env,
    promote_staged_env,
    read_env,
    reset_task_env_overlay,
    stage_env_overrides,
)


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved_environ = dict(os.environ)
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    clear_memory_cache_registry()
    INDEX_CACHE.clear()
    yield
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    clear_memory_cache_registry()
    INDEX_CACHE.clear()
    os.environ.clear()
    os.environ.update(saved_environ)


class TestEnvStaging:
    @staticmethod
    def test_stage_without_promote():
        ENV_CONFIG_DICT["API_KEY"] = "old"
        stage_env_overrides({"API_KEY": "new"})
        assert get_local_config("API_KEY") == "old"
        assert get_staged_env()["API_KEY"] == "new"

    @staticmethod
    def test_task_overlay_priority():
        ENV_CONFIG_DICT["API_KEY"] = "active"
        stage_env_overrides({"API_KEY": "staged"})
        token = bind_task_env_overlay({"API_KEY": "overlay"})
        try:
            assert get_local_config("API_KEY") == "overlay"
            assert read_env("API_KEY") == "overlay"
        finally:
            reset_task_env_overlay(token)
        assert get_local_config("API_KEY") == "active"
        assert read_env("API_KEY") == "active"

    @staticmethod
    def test_promote_staged():
        stage_env_overrides({"MODEL_NAME": "m1"})
        promote_staged_env()
        assert get_local_config("MODEL_NAME") == "m1"
        assert get_staged_env() == {}
        assert os.environ.get("MODEL_NAME") == "m1"

    @staticmethod
    def test_null_env_deletes_staged_key():
        stage_env_overrides({"API_KEY": "x"})
        stage_env_overrides({"API_KEY": None})
        assert "API_KEY" not in get_staged_env()


class TestReloadConfigChangeLogging:
    @staticmethod
    def test_collect_env_override_keys():
        updated, removed = collect_env_override_keys(
            {"API_KEY": "x", "MODEL_NAME": "m", "OLD_KEY": None}
        )
        assert updated == ["API_KEY", "MODEL_NAME"]
        assert removed == ["OLD_KEY"]

    @staticmethod
    def test_collect_changed_config_paths():
        old = {"models": {"default": {"model": "a"}}, "memory": {"engine": "builtin"}}
        new = {"models": {"default": {"model": "b"}}, "memory": {"engine": "builtin"}}
        assert collect_changed_config_paths(old, new) == ["models.default.model"]

    @staticmethod
    def test_collect_changed_config_paths_new_snapshot():
        assert collect_changed_config_paths(None, {"memory": {"engine": "none"}}) == [
            "memory",
            "memory.engine",
        ]

    @staticmethod
    def test_reload_touches_memory_env():
        assert reload_touches_memory({"EMBED_MODEL": "m2"}, None) is True
        assert reload_touches_memory({"API_KEY": "x"}, {"embed": {}}) is False

    @staticmethod
    def test_reload_touches_memory_embed_config():
        embed = {"embed_model": "m", "embed_api_key": "k", "embed_base_url": "u"}
        old = {"memory": {"engine": "builtin"}, "embed": {**embed, "embed_model": "a"}}
        new = {"memory": {"engine": "builtin"}, "embed": {**embed, "embed_model": "b"}}
        assert reload_touches_memory(None, new, previous_config=old) is True
        assert reload_touches_memory(None, old, previous_config=old) is False

    @staticmethod
    def test_reload_touches_memory_external_fingerprint():
        old = {
            "memory": {
                "engine": "external",
                "external": {"provider": "mem0", "user_id": "alice"},
            }
        }
        new = {
            "memory": {
                "engine": "external",
                "external": {"provider": "mem0", "user_id": "bob"},
            }
        }
        assert reload_touches_memory(None, new, previous_config=old) is True
        assert reload_touches_memory(None, old, previous_config=old) is False

    @staticmethod
    def test_reload_touches_memory_external_env_keys():
        assert reload_touches_memory({"MEM0_API_KEY": "new"}, None) is True
        assert reload_touches_memory({"MEMORY_USER_ID": "u1"}, None) is True


class TestAgentConfigHotReloadLogging:
    @staticmethod
    def test_log_agent_config_hot_reload_includes_marker_and_reload_trace_id(caplog):
        test_logger = logging.getLogger("test.hot_reload")
        with caplog.at_level(logging.INFO):
            log_agent_config_hot_reload(
                test_logger,
                reload_trace_id="agent-reload-deadbeef",
                phase="changed_keys",
                source="TestSource",
                env_updated_keys=["MODEL_NAME"],
            )

        assert len(caplog.records) == 1
        message = caplog.records[0].message
        assert AGENT_CONFIG_HOT_RELOAD_MARKER in message
        assert "reload_trace_id=agent-reload-deadbeef" in message
        assert "request_id=" not in message
        assert "phase=changed_keys" in message
        assert "source=TestSource" in message
        assert "env_updated_keys=['MODEL_NAME']" in message

    @staticmethod
    def test_log_reload_config_changes_no_sensitive_values(caplog):
        test_logger = logging.getLogger("test.hot_reload_changes")
        secret_value = "sk-secret-should-not-appear"
        with caplog.at_level(logging.INFO):
            log_reload_config_changes(
                test_logger,
                env={"API_KEY": secret_value},
                config={"models": {"default": {"api_key": secret_value}}},
                reload_trace_id="agent-reload-abc12345",
                source="TenantAgentPool",
                config_set_req_id="ws-req-1",
            )

        message = caplog.records[0].message
        assert secret_value not in message
        assert "config_set_req_id=ws-req-1" in message
        assert "env_updated_keys=['API_KEY']" in message

    @staticmethod
    def test_log_agent_config_hot_reload_replay_no_values(caplog):
        test_logger = logging.getLogger("test.hot_reload_replay")
        secret_value = "sk-replay-secret"
        with caplog.at_level(logging.INFO):
            log_agent_config_hot_reload_replay(
                test_logger,
                reload_trace_id="agent-reload-replay01",
                session="sess_1",
                agent_key="web",
                mode="agent.plan",
                config={"embed": {"embed_api_key": secret_value}},
                env={"API_KEY": secret_value},
            )

        message = caplog.records[0].message
        assert AGENT_CONFIG_HOT_RELOAD_REPLAY_MARKER in message
        assert secret_value not in message
        assert "phase=replay" in message
        assert "env_key_count=1" in message
        assert "config_path_count=" in message

    @staticmethod
    def test_summarize_reload_payload():
        summary = summarize_reload_payload(
            {
                "reloaded": True,
                "applied": 2,
                "deferred": 1,
                "failed": [{"session": "web:agent.plan:s1", "error": "boom"}],
            }
        )
        assert summary["applied"] == 2
        assert summary["deferred"] == 1
        assert summary["failed_count"] == 1
        assert summary["failed_sessions"] == ["web:agent.plan:s1"]

    @staticmethod
    def test_failed_error_redaction():
        assert redact_reload_error_message("invalid API_KEY=sk-abc") == "[redacted]"
        assert redact_reload_error_message("x" * 200).endswith("...")
        assert redact_reload_error_message("short error") == "short error"


@pytest.mark.asyncio
async def test_agent_manager_syncs_memory_cache_after_reload():
    manager = AgentManager(agent_id="a1", service_id="s1")
    mock_agent = MagicMock()
    mock_agent.reload_agent_config = AsyncMock(return_value=ReloadResult(applied=True))
    mock_agent.get_memory_cache_fingerprint = AsyncMock(return_value="fp-new")
    manager.agents["web"] = {"agent.plan": {"sess1": mock_agent}}

    with patch.object(AgentManager, "is_working", return_value=False):
        with patch(
            "jiuwenclaw.agentserver.agent_manager.acquire_memory_cache_session",
            new=AsyncMock(),
        ) as acquire_mock:
            await manager.reload_agents_config(
                {"embed": {"embed_model": "new"}},
                {"EMBED_MODEL": "new"},
            )

    acquire_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_manager_reload_stages_env_without_os_write():
    manager = AgentManager(agent_id="a1", service_id="s1")
    os.environ["API_KEY"] = "before"

    mock_agent = MagicMock()
    mock_agent.reload_agent_config = AsyncMock(return_value=ReloadResult(deferred=True))
    manager.agents["web"] = {"agent.plan": {"sess1": mock_agent}}

    stage_env_overrides({"API_KEY": "after"})
    with patch.object(AgentManager, "is_working", return_value=True):
        result = await manager.reload_agents_config(None, {"API_KEY": "after"})

    assert isinstance(result, ReloadAggregateResult)
    assert result.deferred == 1
    assert os.environ.get("API_KEY") == "before"
    assert get_staged_env().get("API_KEY") == "after"


@pytest.mark.asyncio
async def test_agent_manager_promotes_when_all_idle():
    manager = AgentManager(agent_id="a1", service_id="s1")

    mock_agent = MagicMock()
    mock_agent.reload_agent_config = AsyncMock(return_value=ReloadResult(applied=True))
    manager.agents["web"] = {"agent.plan": {"sess1": mock_agent}}

    with patch.object(AgentManager, "is_working", return_value=False):
        stage_env_overrides({"MODEL_NAME": "new-model"})
        result = await manager.reload_agents_config(None, {"MODEL_NAME": "new-model"})

    assert result.applied == 1
    assert get_staged_env() == {}
    assert os.environ.get("MODEL_NAME") == "new-model"


@pytest.mark.asyncio
async def test_reload_aggregate_result_payload():
    aggregate = ReloadAggregateResult(applied=2, deferred=1, failed=[{"session": "x", "error": "e"}])
    payload = aggregate.to_payload()
    assert payload["applied"] == 2
    assert payload["deferred"] == 1
    assert payload["failed"] == [{"session": "x", "error": "e"}]


class _JiuWenClawTestHarness:
    """Test helper exposing JiuWenClaw reload/stream internals via subclass access."""

    _HarnessCls = None

    @classmethod
    def _harness_cls(cls):
        if cls._HarnessCls is None:
            from jiuwenclaw.agentserver.interface import JiuWenClaw

            class _Harness(JiuWenClaw):
                def attach_adapter(self, adapter) -> None:
                    self._adapter = adapter

                def acquire_stream(self) -> None:
                    self._acquire_inflight_stream()

                def release_stream(self) -> None:
                    self._release_inflight_stream()

                def sync_working_checker(self) -> None:
                    self._sync_adapter_working_checker()

                def inflight_stream_count(self) -> int:
                    return self._inflight_stream_count

                async def try_apply_pending_reload(self) -> None:
                    await self._try_apply_adapter_pending_reload()

            cls._HarnessCls = _Harness
        return cls._HarnessCls

    def __init__(self, agent_id: str = "a1", service_id: str = "s1") -> None:
        self._agent = self._harness_cls()(agent_id=agent_id, service_id=service_id)

    @property
    def agent(self):
        return self._agent


class _DeepAdapterReloadHarness:
    """Minimal DeepAdapter surface for reload defer/apply tests."""

    _HarnessCls = None

    @classmethod
    def _harness_cls(cls):
        if cls._HarnessCls is None:
            import asyncio

            from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter

            class _Harness(JiuWenClawDeepAdapter):
                @classmethod
                def for_reload_test(cls, *, working_checker: bool, pending_reload):
                    adapter = cls.__new__(cls)
                    adapter._instance = MagicMock()
                    adapter._pending_reload = pending_reload
                    adapter._reload_lock = asyncio.Lock()
                    adapter._working_checker = None
                    adapter.set_working_checker(lambda: working_checker)
                    return adapter

                def get_pending_reload(self):
                    return self._pending_reload

                def configure_for_force_apply_test(self) -> MagicMock:
                    self._embed_fingerprint = "old"
                    self._instance_overrides = {}
                    self._tool_cards = []
                    self._model = MagicMock()
                    self._agent_name = "test"
                    self._sandbox_fingerprint = ()
                    self._maybe_recreate_sys_operation = MagicMock()
                    mock_model = MagicMock()
                    mock_model.model_client_config = MagicMock()
                    self._instance.configure = MagicMock()
                    self._apply_model_to_react_agent = MagicMock()
                    self._refresh_fork_agent_executor_model = MagicMock()
                    self._make_deep_agent_config = MagicMock(return_value=MagicMock())
                    self._get_current_agent_rails = AsyncMock(return_value=[])
                    self.load_user_rails = AsyncMock()
                    self._handle_memory_rail_by_config = AsyncMock()
                    self._handle_external_memory_rail_by_config = AsyncMock()
                    self._apply_registered_skill_dirs_to_runtime_rails = MagicMock()
                    self._refresh_multimodal_configs = MagicMock()
                    self._sync_multimodal_tools_for_runtime = MagicMock()
                    self._filesystem_rail_enabled_for_profile = MagicMock(return_value=True)
                    self._sync_registered_skill_dirs_snapshot = MagicMock()
                    self._resolve_agent_card_id = MagicMock(return_value="id")
                    self._last_runtime_mode = "agent.plan"
                    self._memory_engine_snapshot = None
                    self._memory_rail = None
                    self._embed_config_fingerprint = MagicMock(return_value="new")
                    self._create_model = MagicMock(return_value=mock_model)
                    return mock_model

                async def run_maybe_apply_pending_reload(self):
                    return await self._maybe_apply_pending_reload()

            cls._HarnessCls = _Harness
        return cls._HarnessCls

    @classmethod
    def build(cls, *, working: bool, pending=None):
        return cls._harness_cls().for_reload_test(
            working_checker=working,
            pending_reload=pending,
        )


class TestJiuWenClawInflightStreamWorking:
    @staticmethod
    def test_is_working_true_when_inflight_stream_active():
        harness = _JiuWenClawTestHarness()
        mock_adapter = MagicMock()
        mock_adapter.is_working.return_value = False
        harness.agent.attach_adapter(mock_adapter)

        assert harness.agent.is_working() is False
        mock_adapter.is_working.reset_mock()

        harness.agent.acquire_stream()
        try:
            assert harness.agent.is_working() is True
            mock_adapter.is_working.assert_not_called()
        finally:
            harness.agent.release_stream()

        assert harness.agent.is_working() is False
        mock_adapter.is_working.assert_called_once()

    @staticmethod
    def test_release_inflight_stream_never_goes_negative():
        harness = _JiuWenClawTestHarness()
        harness.agent.release_stream()
        assert harness.agent.inflight_stream_count() == 0

    @staticmethod
    def test_working_checker_reflects_inflight_stream():
        harness = _JiuWenClawTestHarness()
        mock_adapter = MagicMock()
        mock_adapter.is_working.return_value = False
        harness.agent.attach_adapter(mock_adapter)
        harness.agent.sync_working_checker()

        checker = mock_adapter.set_working_checker.call_args[0][0]
        assert checker() is False

        harness.agent.acquire_stream()
        try:
            assert checker() is True
        finally:
            harness.agent.release_stream()


@pytest.mark.asyncio
async def test_deep_adapter_reload_deferred_when_working_checker_active():
    adapter = _DeepAdapterReloadHarness.build(working=True)

    result = await adapter.reload_agent_config(
        config_base={"models": {"default": {}}},
        env_overrides={"API_BASE": "http://example/v1"},
    )

    assert result.deferred is True
    assert adapter.get_pending_reload() is not None


@pytest.mark.asyncio
async def test_apply_pending_reload_if_idle_skips_working_checker():
    adapter = _DeepAdapterReloadHarness.build(
        working=True,
        pending=(
            {"models": {"default": {}}},
            {"API_BASE": "http://bad/v1"},
            False,
        ),
    )

    with patch.object(
        adapter, "reload_agent_config", new=AsyncMock(return_value=ReloadResult(applied=True))
    ) as apply_mock:
        with patch("jiuwenclaw.local_env_config.promote_staged_env") as promote_mock:
            result = await adapter.apply_pending_reload_if_idle()

    assert result is not None
    assert result.applied is True
    assert adapter.get_pending_reload() is None
    apply_mock.assert_awaited_once()
    promote_mock.assert_called_once()


@pytest.mark.asyncio
async def test_apply_pending_reload_if_idle_restores_pending_on_failure():
    pending = (None, {"MODEL_NAME": ""}, False)
    adapter = _DeepAdapterReloadHarness.build(working=True, pending=pending)

    with patch.object(
        adapter,
        "reload_agent_config",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await adapter.apply_pending_reload_if_idle()

    assert adapter.get_pending_reload() == pending


@pytest.mark.asyncio
async def test_force_apply_with_invalidate_memory_false():
    adapter = _DeepAdapterReloadHarness.build(working=False)
    adapter.configure_for_force_apply_test()

    with patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_config",
        return_value={"react": {"agent_name": "a"}},
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.memory_cache_fingerprint",
        return_value="mfp",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_memory_engine",
        return_value="builtin",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_config_cache",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_memory_manager_cache",
        new=AsyncMock(),
    ):
        result = await adapter.reload_agent_config(
            config_base=None,
            env_overrides={"MODEL_NAME": ""},
            _force_apply=True,
            _invalidate_memory_cache=False,
        )

    assert result.applied is True


@pytest.mark.asyncio
async def test_jiuwenclaw_applies_pending_reload_before_inflight_acquire():
    harness = _JiuWenClawTestHarness()
    mock_adapter = MagicMock()
    mock_adapter.is_working.return_value = False
    mock_adapter.apply_pending_reload_if_idle = AsyncMock(
        return_value=ReloadResult(applied=True)
    )
    harness.agent.attach_adapter(mock_adapter)

    await harness.agent.try_apply_pending_reload()

    mock_adapter.apply_pending_reload_if_idle.assert_awaited_once()

    harness.agent.acquire_stream()
    try:
        await harness.agent.try_apply_pending_reload()
        mock_adapter.apply_pending_reload_if_idle.assert_awaited_once()
    finally:
        harness.agent.release_stream()

    await harness.agent.try_apply_pending_reload()
    assert mock_adapter.apply_pending_reload_if_idle.await_count == 2


class TestPatchModelConfigFromEnv:
    @staticmethod
    def test_no_env_leaves_config_unchanged():
        from jiuwenclaw.config import patch_model_config_from_env

        config = {
            "models": {
                "defaults": [{"model_client_config": {"model_name": "deepseek-v3"}}],
            }
        }
        assert patch_model_config_from_env(config) is config

    @staticmethod
    def test_env_model_name_patches_defaults():
        from jiuwenclaw.config import patch_model_config_from_env

        config = {
            "models": {
                "defaults": [{"model_client_config": {"model_name": "deepseek-v3"}}],
            }
        }
        patched = patch_model_config_from_env(
            config,
            env_overrides={"MODEL_NAME": "new-model"},
        )
        assert (
            patched["models"]["defaults"][0]["model_client_config"]["model_name"]
            == "new-model"
        )
        assert (
            config["models"]["defaults"][0]["model_client_config"]["model_name"]
            == "deepseek-v3"
        )
        assert patched["react"]["model_name"] == "new-model"

    @staticmethod
    def test_env_dict_alone_does_not_patch_without_env_overrides():
        from jiuwenclaw.config import patch_model_config_from_env

        ENV_CONFIG_DICT["MODEL_NAME"] = "new-model"
        config = {
            "models": {
                "defaults": [{"model_client_config": {"model_name": "deepseek-v3"}}],
            }
        }
        assert patch_model_config_from_env(config) is config

    @staticmethod
    def test_env_overrides_patches_explicit_values_including_placeholders():
        from jiuwenclaw.config import patch_model_config_from_env

        config = {
            "models": {
                "defaults": [{"model_client_config": {"model_name": "deepseek-v3"}}],
            }
        }
        patched = patch_model_config_from_env(
            config,
            env_overrides={"MODEL_NAME": "your-model-name"},
        )
        assert (
            patched["models"]["defaults"][0]["model_client_config"]["model_name"]
            == "your-model-name"
        )

    @staticmethod
    def test_os_environ_alone_does_not_patch(monkeypatch):
        from jiuwenclaw.config import patch_model_config_from_env

        monkeypatch.setenv("MODEL_NAME", "from-os-environ")
        config = {
            "models": {
                "defaults": [{"model_client_config": {"model_name": "deepseek-v3"}}],
            }
        }
        assert patch_model_config_from_env(config) is config


class TestCreateModelEnvSync:
    @staticmethod
    def test_env_model_name_becomes_default_after_patch():
        """Simulates _create_model cache key selection after patch_model_config_from_env."""
        from jiuwenclaw.config import get_default_models, patch_model_config_from_env

        ENV_CONFIG_DICT["MODEL_NAME"] = "new-model"
        ENV_CONFIG_DICT["API_KEY"] = "test-key"

        config = {
            "models": {
                "defaults": [
                    {
                        "model_client_config": {
                            "model_name": "deepseek-v3",
                            "api_key": "k",
                            "api_base": "http://x",
                        },
                    }
                ]
            }
        }
        env_overrides = {"MODEL_NAME": "new-model", "API_KEY": "test-key"}
        patched = patch_model_config_from_env(config, env_overrides)
        cache_keys = {
            (entry.get("model_client_config") or {}).get("model_name")
            for entry in get_default_models(patched)
        }
        cache_keys.discard(None)

        env_model_name = str(env_overrides.get("MODEL_NAME", "")).strip()
        default_name = (
            env_model_name
            if env_model_name and env_model_name in cache_keys
            else next(iter(cache_keys))
        )

        assert default_name == "new-model"
        assert "new-model" in cache_keys
        assert "deepseek-v3" not in cache_keys


@pytest.mark.asyncio
async def test_maybe_apply_pending_reload_promotes_staged_env():
    stage_env_overrides({"MODEL_NAME": "new-model"})
    adapter = _DeepAdapterReloadHarness.build(
        working=False,
        pending=(None, {"MODEL_NAME": "new-model"}, False),
    )

    with patch.object(
        adapter, "reload_agent_config", new=AsyncMock(return_value=ReloadResult(applied=True))
    ):
        with patch("jiuwenclaw.local_env_config.promote_staged_env") as promote_mock:
            result = await adapter.run_maybe_apply_pending_reload()

    assert result is not None
    assert result.applied is True
    assert adapter.get_pending_reload() is None
    promote_mock.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_apply_pending_reload_restores_pending_on_failure():
    pending = (None, {"MODEL_NAME": "new-model"}, False)
    adapter = _DeepAdapterReloadHarness.build(working=False, pending=pending)

    with patch.object(
        adapter,
        "reload_agent_config",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await adapter.run_maybe_apply_pending_reload()

    assert adapter.get_pending_reload() == pending


@pytest.mark.asyncio
async def test_reload_translates_yaml_sandbox_to_env_overlay(monkeypatch: pytest.MonkeyPatch):
    """config_base['sandbox'] 翻译为 env overlay, _create_sys_operation 读到 yaml 值."""
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod
    from jiuwenclaw.config import get_sandbox_endpoint, get_sandbox_runtime

    adapter = _DeepAdapterReloadHarness.build(working=False)
    adapter.configure_for_force_apply_test()

    captured: dict = {}

    def _fake_create_sys_operation(self):
        endpoint = get_sandbox_endpoint()
        runtime = get_sandbox_runtime()
        captured["endpoint"] = endpoint
        captured["runtime"] = runtime
        return MagicMock()

    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_create_sys_operation",
        lambda self: _fake_create_sys_operation(self),
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_sandbox_config_fingerprint",
        lambda self: ("yaml-fp",),
    )

    # 旧指纹不同于 yaml-fp, 触发重建路径调用 _create_sys_operation
    adapter._sandbox_fingerprint = ("old-fp",)
    # configure_for_force_apply_test 把 _maybe_recreate_sys_operation 设成 MagicMock
    # 实例属性会遮蔽类方法, 删掉让真实指纹比对路径跑到 _create_sys_operation
    monkeypatch.delattr(adapter, "_maybe_recreate_sys_operation")

    with patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_config",
        return_value={"react": {"agent_name": "a"}, "sandbox": {
            "url": "http://yaml-sb/v1", "type": "yaml-type", "enabled": True,
        }},
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.memory_cache_fingerprint",
        return_value="mfp",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_memory_engine",
        return_value="builtin",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_config_cache",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_memory_manager_cache",
        new=AsyncMock(),
    ):
        result = await adapter.reload_agent_config(
            config_base={"sandbox": {
                "url": "http://yaml-sb/v1", "type": "yaml-type", "enabled": True,
            }},
            env_overrides=None,
            _force_apply=True,
            _invalidate_memory_cache=False,
        )

    assert result.applied is True
    assert captured["endpoint"]["url"] == "http://yaml-sb/v1"
    assert captured["endpoint"]["type"] == "yaml-type"
    assert captured["runtime"]["enabled"] is True


@pytest.mark.asyncio
async def test_reload_yaml_sandbox_overrides_env_overrides(monkeypatch: pytest.MonkeyPatch):
    """yaml sandbox 覆盖 env_overrides 中的 JIUWENCLAW_SANDBOX_URL."""
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod
    from jiuwenclaw.config import get_sandbox_endpoint

    adapter = _DeepAdapterReloadHarness.build(working=False)
    adapter.configure_for_force_apply_test()

    captured: dict = {}

    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_create_sys_operation",
        lambda self: (captured.setdefault("endpoint", get_sandbox_endpoint()), MagicMock())[1],
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_sandbox_config_fingerprint",
        lambda self: ("yaml-fp",),
    )
    adapter._sandbox_fingerprint = ("old-fp",)
    # 让真实 _maybe_recreate_sys_operation 跑到 _create_sys_operation
    monkeypatch.delattr(adapter, "_maybe_recreate_sys_operation")

    with patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_config",
        return_value={"react": {"agent_name": "a"}, "sandbox": {"url": "yaml-u"}},
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.memory_cache_fingerprint",
        return_value="mfp",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_memory_engine",
        return_value="builtin",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_config_cache",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_memory_manager_cache",
        new=AsyncMock(),
    ):
        await adapter.reload_agent_config(
            config_base={"sandbox": {"url": "yaml-u"}},
            env_overrides={"JIUWENCLAW_SANDBOX_URL": "env-u"},
            _force_apply=True,
            _invalidate_memory_cache=False,
        )

    assert captured["endpoint"]["url"] == "yaml-u"


@pytest.mark.asyncio
async def test_reload_yaml_sandbox_invalid_enabled_raises():
    """yaml sandbox.enabled='maybe' 让 reload 整体失败."""
    adapter = _DeepAdapterReloadHarness.build(working=False)
    adapter.configure_for_force_apply_test()

    with patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_config",
        return_value={"react": {"agent_name": "a"}},
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.memory_cache_fingerprint",
        return_value="mfp",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_memory_engine",
        return_value="builtin",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_config_cache",
    ), patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_memory_manager_cache",
        new=AsyncMock(),
    ):
        with pytest.raises(ValueError, match="sandbox.enabled"):
            await adapter.reload_agent_config(
                config_base={"sandbox": {"enabled": "maybe"}},
                env_overrides=None,
                _force_apply=True,
                _invalidate_memory_cache=False,
            )


@pytest.mark.asyncio
async def test_create_agent_replays_yaml_sandbox_to_sysop(monkeypatch: pytest.MonkeyPatch):
    """AgentManager.reload_agents_config 早于 agent 创建时, _create_agent 重放 reload_agent_config.

    场景:
    1. AgentManager 先收到 reload, _latest_config_base 存了 sandbox yaml 块.
    2. 后续 _create_agent 创建实例, 重放 reload_agent_config.
    3. _maybe_recreate_sys_operation 触发, _create_sys_operation 读到 yaml 值.
    """
    # 相对 plan Step 3.1 的 5 处调整 (因 reload_agent_config 真实路径需要):
    # 调整1: _fake_create_instance 设 self._instance = MagicMock(), 否则 reload_agent_config
    #        在 interface_deep.py:3611 抛 "未初始化" RuntimeError, replay 被 agent_manager.py:178-179 try/except 吞掉.
    # 调整2: _fake_create_instance 设 self._sandbox_fingerprint = self._sandbox_config_fingerprint()
    #        消费首次 fingerprint 调用, 让 _maybe_recreate_sys_operation 第二次读到 yaml-fp 触发重建.
    # 调整3: memory_cache_fingerprint / get_memory_engine 在 interface_deep.py 是模块级 import (非类方法),
    #        monkeypatch 必须打在 mod 上, 而不是 mod.JiuWenClawDeepAdapter.
    # 调整4: 多打 mod.clear_global_config_cache (reload_agent_config:3638 调用).
    # 调整5: 多打 mod.get_config 返回带 sandbox 块的 config, 让 _sandbox_yaml_to_env_overlay 路径生效.
    from jiuwenclaw.agentserver.agent_manager import AgentManager
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod
    from jiuwenclaw.config import get_sandbox_endpoint, get_sandbox_runtime

    manager = AgentManager(agent_id="a1", service_id="s1")
    yaml_sandbox = {"url": "http://lazy-sb/v1", "type": "lazy-type", "enabled": True}
    manager._latest_config_base = {"react": {"agent_name": "a"}, "sandbox": yaml_sandbox}
    manager._latest_env_overrides = {}

    captured: dict = {}

    async def _fake_create_instance(self, config, *, mode="agent", session_id="default"):
        # 模拟 _init_agent_instance_sync 完成后的状态 (_instance + _sys_operation + _sandbox_fingerprint 已就位), 不跑重逻辑
        self._instance = MagicMock()
        self._sys_operation = MagicMock(id="env-only-sysop")
        # 模拟 _init_agent_instance_sync 调用 _sandbox_config_fingerprint (env-only)
        # 存到 _sandbox_fingerprint, 消耗首次调用让 reload 时第二次调用读到 yaml-fp
        self._sandbox_fingerprint = self._sandbox_config_fingerprint()

    def _fake_create_sys_operation(self):
        endpoint = get_sandbox_endpoint()
        runtime = get_sandbox_runtime()
        captured.setdefault("calls", []).append((endpoint, runtime))
        new_sysop = MagicMock(id="yaml-sysop")
        return new_sysop

    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "create_instance", _fake_create_instance
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_create_sys_operation",
        _fake_create_sys_operation,
    )
    # _sandbox_config_fingerprint: 首次 (env-only) 与第二次 (yaml) 不同, 触发重建
    call_count = {"n": 0}
    def _fingerprint(self):
        call_count["n"] += 1
        return ("env-only-fp",) if call_count["n"] == 1 else ("yaml-fp",)
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_sandbox_config_fingerprint", _fingerprint
    )
    # 屏蔽 reload 其余重逻辑
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_embed_config_fingerprint", lambda self, c: "fp"
    )
    monkeypatch.setattr(mod, "memory_cache_fingerprint", lambda c: "mfp")
    monkeypatch.setattr(mod, "get_memory_engine", lambda c: "builtin")
    monkeypatch.setattr(mod, "clear_config_cache", lambda: None)
    monkeypatch.setattr(mod, "clear_global_config_cache", lambda: None)
    async def _noop_clear_memory(*a, **kw):
        return None
    monkeypatch.setattr(mod, "clear_memory_manager_cache", _noop_clear_memory)
    # get_config 返回带 sandbox 的 yaml 块, 让 reload 翻译路径走到
    monkeypatch.setattr(
        mod,
        "get_config",
        lambda: {"react": {"agent_name": "a"}, "sandbox": yaml_sandbox},
    )
    # 屏蔽 rails/model 等
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_refresh_multimodal_configs", lambda self, c: None
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_create_model",
        lambda self, c, e: MagicMock(model_client_config=MagicMock()),
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_resolve_agent_card_id", lambda self: "id"
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_sync_multimodal_tools_for_runtime", lambda self: None
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_filesystem_rail_enabled_for_profile",
        lambda self: True,
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_sync_registered_skill_dirs_snapshot",
        lambda self: None,
    )
    async def _empty_rails(self, c, cb):
        return []
    monkeypatch.setattr(mod.JiuWenClawDeepAdapter, "_get_current_agent_rails", _empty_rails)
    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(mod.JiuWenClawDeepAdapter, "load_user_rails", _noop)
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_handle_memory_rail_by_config", _noop
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_handle_external_memory_rail_by_config", _noop
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_apply_registered_skill_dirs_to_runtime_rails",
        lambda self: None,
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_make_deep_agent_config",
        lambda self, **kw: MagicMock(),
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter, "_apply_model_to_react_agent", lambda self, m: None
    )
    monkeypatch.setattr(
        mod.JiuWenClawDeepAdapter,
        "_refresh_fork_agent_executor_model",
        lambda self: None,
    )
    monkeypatch.setattr(mod.Runner, "resource_mgr", MagicMock(
        add_sys_operation=MagicMock(return_value=MagicMock(is_err=lambda: False, msg=lambda: "")),
        get_sys_operation=MagicMock(return_value=MagicMock(id="yaml-sysop")),
        remove_sys_operation=MagicMock(return_value=MagicMock(is_err=lambda: False, msg=lambda: "")),
    ))

    agent = await manager._create_agent("default", mode="agent.plan", session_id="sess1")

    # reload_agent_config 被重放, _create_sys_operation 读到 yaml 值
    # _create_sys_operation 仅在 _maybe_recreate_sys_operation 内被调用一次 (create_instance mock 不调用它).
    assert len(captured["calls"]) == 1
    last_endpoint, last_runtime = captured["calls"][-1]
    assert last_endpoint["url"] == "http://lazy-sb/v1"
    assert last_endpoint["type"] == "lazy-type"
    assert last_runtime["enabled"] is True
    # 旧 env-only sysop (id="env-only-sysop") 被清理, 而非新建的 yaml sysop.
    mod.Runner.resource_mgr.remove_sys_operation.assert_called_once_with("env-only-sysop")
