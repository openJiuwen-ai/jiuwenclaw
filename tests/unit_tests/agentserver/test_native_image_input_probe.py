from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.harness import image_modality_probe
from openjiuwen.harness.image_modality_probe import (
    probe_cache_key,
    reset_image_support_cache,
)

from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    reset_image_support_cache()
    yield
    reset_image_support_cache()


def _make_llm(model_name: str = "test-model", api_base: str = "https://api.example/v1"):
    return SimpleNamespace(
        model_client_config=SimpleNamespace(api_base=api_base),
        model_config=SimpleNamespace(model_name=model_name),
    )


def _make_vision_config() -> interface_module.VisionModelConfig:
    return interface_module.VisionModelConfig(
        api_key="vision-key",
        base_url="https://vision.example/v1",
        model="vision-model",
    )


def _seed_verdict(llm, supported: bool) -> None:
    image_modality_probe._probe_results[probe_cache_key(llm)] = supported


def test_cached_true_wins_over_configured_vision_model() -> None:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._vision_model_config = _make_vision_config()
    llm = _make_llm("gpt-4o")
    _seed_verdict(llm, True)

    assert adapter._native_image_input_enabled({}, llm) is True


def test_cached_false_wins_without_vision_model() -> None:
    adapter = JiuWenSwarmDeepAdapter()
    llm = _make_llm("routed-vllm-model")
    _seed_verdict(llm, False)

    assert adapter._native_image_input_enabled({}, llm) is False


def test_explicit_config_wins_over_cached_verdict() -> None:
    adapter = JiuWenSwarmDeepAdapter()
    llm = _make_llm()

    _seed_verdict(llm, False)
    assert (
        adapter._native_image_input_enabled({"enable_read_image_multimodal": True}, llm)
        is True
    )

    _seed_verdict(llm, True)
    assert (
        adapter._native_image_input_enabled({"enable_read_image_multimodal": False}, llm)
        is False
    )


@pytest.mark.asyncio
async def test_no_verdict_falls_back_and_schedules_probe() -> None:
    adapter = JiuWenSwarmDeepAdapter()
    llm = _make_llm()

    assert adapter._native_image_input_enabled({}, llm) is True
    assert probe_cache_key(llm) in image_modality_probe._probe_tasks

    adapter._vision_model_config = _make_vision_config()
    assert adapter._native_image_input_enabled({}, llm) is False


@pytest.mark.asyncio
async def test_model_none_falls_back_without_scheduling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = JiuWenSwarmDeepAdapter()
    scheduled: list[object] = []
    monkeypatch.setattr(
        interface_module, "schedule_image_support_probe", scheduled.append
    )

    assert adapter._native_image_input_enabled({}, None) is True
    adapter._vision_model_config = _make_vision_config()
    assert adapter._native_image_input_enabled({}, None) is False
    assert scheduled == []
