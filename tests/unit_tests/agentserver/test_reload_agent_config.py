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
    read_env_if_set,
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

    @staticmethod
    def test_read_env_if_set_sees_staged_without_affecting_get_local_config():
        ENV_CONFIG_DICT["API_KEY"] = "old"
        stage_env_overrides({"API_KEY": "new"})
        assert get_local_config("API_KEY") == "old"
        assert read_env_if_set("API_KEY") == "new"

    @staticmethod
    def test_read_env_if_set_returns_none_when_unset():
        assert read_env_if_set("MISSING_KEY") is None


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
