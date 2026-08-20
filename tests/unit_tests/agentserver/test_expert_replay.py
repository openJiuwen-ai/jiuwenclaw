# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""入口 _replay_expert_from_metadata 与 expert_switch_blocked 测试。
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.harness.factory import create_deep_agent

import jiuwenswarm.server.runtime.session.session_metadata as sm
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)
from jiuwenswarm.server.runtime.expert import expert_store as es

ORIGINAL_IDENTITY = "你是测试宿主助手。"


def _make_model() -> Model:
    return Model(
        ModelClientConfig(
            client_id="test",
            client_provider="openai",
            api_key="dummy",
            api_base="https://example.com/v1",
        ),
        ModelRequestConfig(model_name="dummy-model"),
    )


def _make_package(root: Path, name: str, persona: str) -> Path:
    pkg = root / name
    (pkg / "agents").mkdir(parents=True)
    (pkg / "agents" / "00-identity.md").write_text(persona, encoding="utf-8")
    (pkg / "manifest.json").write_text(
        json.dumps(
            {
                "packageType": "agent_template",
                "agentCard": {"id": name, "name": name, "description": "测试包"},
                "persona": {"dir": "agents"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pkg


def _make_child_adapter(session_id: str) -> JiuWenSwarmDeepAdapter:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = create_deep_agent(
        model=_make_model(), system_prompt=ORIGINAL_IDENTITY, workspace=tempfile.mkdtemp()
    )
    adapter.mark_as_session_scoped(session_id)
    return adapter


def _identity_text(adapter: JiuWenSwarmDeepAdapter) -> str:
    section = adapter._instance.system_prompt_builder.get_section("identity")
    return section.content.get("cn") or next(iter(section.content.values()))


@pytest.fixture
def experts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "experts"
    root.mkdir()
    source = es.LocalDirExpertPackageSource(experts_dir=root)
    monkeypatch.setattr(es, "get_expert_source", lambda: source)
    return root


@pytest.mark.asyncio
async def test_replay_applies_expert_from_metadata(
        experts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    monkeypatch.setattr(
        sm,
        "get_session_metadata",
        lambda session_id, cache_bust=False, **_: {"expert_id": "expert-a"},
    )
    adapter = _make_child_adapter("sess-1")

    await adapter._replay_expert_from_metadata()

    assert adapter._current_expert_id == "expert-a"
    assert _identity_text(adapter) == "你是专家阿甲。"


@pytest.mark.asyncio
async def test_replay_noop_without_expert_in_metadata(
        experts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sm,
        "get_session_metadata",
        lambda session_id, cache_bust=False, **_: {"title": "无专家会话"},
    )
    adapter = _make_child_adapter("sess-2")

    await adapter._replay_expert_from_metadata()

    assert adapter._current_expert_id is None
    assert ORIGINAL_IDENTITY in _identity_text(adapter)


@pytest.mark.asyncio
async def test_replay_failure_degrades_to_no_expert(
        experts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重放失败（包不存在/仓库不可达）降级无专家、不抛穿、不清 metadata。"""
    monkeypatch.setattr(
        sm,
        "get_session_metadata",
        lambda session_id, cache_bust=False, **_: {"expert_id": "missing-pkg"},
    )
    adapter = _make_child_adapter("sess-3")

    await adapter._replay_expert_from_metadata()  # 不应抛出

    assert adapter._current_expert_id is None
    assert adapter._expert_load_record is None
    assert ORIGINAL_IDENTITY in _identity_text(adapter)


@pytest.mark.asyncio
async def test_replay_read_metadata_failure_is_safe(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(session_id, cache_bust=False, **_):
        raise OSError("disk error")

    monkeypatch.setattr(sm, "get_session_metadata", _boom)
    adapter = _make_child_adapter("sess-4")

    await adapter._replay_expert_from_metadata()  # 不应抛出
    assert adapter._current_expert_id is None


def test_switch_blocked_by_active_counter() -> None:
    root = JiuWenSwarmDeepAdapter()
    root._active_session_ids["sess-1"] = 1
    assert root.expert_switch_blocked("sess-1") is True
    assert root.expert_switch_blocked("sess-2") is False


def test_switch_blocked_by_child_executing() -> None:
    root = JiuWenSwarmDeepAdapter()
    child = SimpleNamespace(_is_session_live=lambda sid: True)
    root._session_adapters["sess-1"] = child
    assert root.expert_switch_blocked("sess-1") is True


def test_switch_allowed_when_idle() -> None:
    root = JiuWenSwarmDeepAdapter()
    child = SimpleNamespace(_is_session_live=lambda sid: False)
    root._session_adapters["sess-1"] = child
    assert root.expert_switch_blocked("sess-1") is False
    # 无子适配器（未装配）也不阻塞
    assert root.expert_switch_blocked("sess-new") is False
