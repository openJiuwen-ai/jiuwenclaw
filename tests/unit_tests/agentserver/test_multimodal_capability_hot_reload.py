from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


class _AbilityManager:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []

    def add(self, card) -> None:
        self.added.append(card.name)

    def remove(self, name: str) -> None:
        self.removed.append(name)


def _vision_config() -> dict:
    return {
        "models": {
            "vision": {
                "model_client_config": {
                    "api_base": "https://vision.example/v1",
                    "api_key": "secret",
                    "model_name": "vision-model",
                    "client_provider": "OpenAI",
                }
            }
        }
    }


def test_multimodal_switch_hot_reload_registers_and_removes_vision_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = SimpleNamespace(name="vision_test", stateless=False)
    tool = SimpleNamespace(card=card, vision_model_config=None)
    registered: list[tuple[object, str | None]] = []
    unregistered: list[object] = []

    monkeypatch.setattr(interface_deep, "create_vision_tools", lambda **_kwargs: [tool])
    monkeypatch.setattr(
        interface_deep,
        "register_tool",
        lambda value, owner_id=None: registered.append((value, owner_id)),
    )
    monkeypatch.setattr(
        interface_deep, "unregister_tool", lambda value: unregistered.append(value)
    )
    monkeypatch.setenv("AUDIO_ENABLED", "false")
    monkeypatch.setenv("VIDEO_ENABLED", "false")
    monkeypatch.delenv("IMAGE_GEN_API_KEY", raising=False)

    adapter = JiuWenSwarmDeepAdapter()
    ability_manager = _AbilityManager()
    adapter._instance = SimpleNamespace(ability_manager=ability_manager)
    adapter._tool_cards = []
    config = _vision_config()

    monkeypatch.setenv("VISION_ENABLED", "false")
    adapter._refresh_multimodal_configs(config)
    adapter._sync_multimodal_tools_for_runtime()
    assert adapter._vision_tools_registered is False
    assert registered == []

    monkeypatch.setenv("VISION_ENABLED", "true")
    adapter._refresh_multimodal_configs(config)
    adapter._sync_multimodal_tools_for_runtime()
    assert adapter._vision_tools_registered is True
    assert registered == [(tool, adapter._tool_owner_id())]
    assert ability_manager.added == ["vision_test"]
    assert [item.name for item in adapter._tool_cards] == ["vision_test"]

    monkeypatch.setenv("VISION_ENABLED", "false")
    adapter._refresh_multimodal_configs(config)
    adapter._sync_multimodal_tools_for_runtime()
    assert adapter._vision_tools_registered is False
    assert unregistered == [tool]
    assert ability_manager.removed == ["vision_test"]
    assert adapter._tool_cards == []


@pytest.mark.asyncio
async def test_multimodal_scope_uses_targeted_reload_without_resetting_other_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = SimpleNamespace()
    config = _vision_config()
    targeted_snapshot = AsyncMock(return_value=config)
    full_snapshot = AsyncMock()
    sync_tools = MagicMock()
    fan_out = AsyncMock()

    monkeypatch.setattr(
        adapter,
        "_apply_multimodal_reload_snapshot",
        targeted_snapshot,
    )
    monkeypatch.setattr(adapter, "_apply_reload_config_snapshot", full_snapshot)
    monkeypatch.setattr(adapter, "_sync_multimodal_tools_for_runtime", sync_tools)
    monkeypatch.setattr(adapter, "_fan_out_reload_to_session_adapters", fan_out)

    await adapter.reload_agent_config(
        config,
        {"VISION_ENABLED": "true"},
        reload_scopes={"multimodal"},
    )

    targeted_snapshot.assert_awaited_once_with(
        config,
        {"VISION_ENABLED": "true"},
    )
    full_snapshot.assert_not_awaited()
    sync_tools.assert_called_once_with()
    fan_out.assert_awaited_once_with(
        config,
        {"VISION_ENABLED": "true"},
        None,
        {"multimodal"},
    )
