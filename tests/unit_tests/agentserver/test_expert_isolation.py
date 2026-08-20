# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""专家会话隔离测试：两个 session 各选不同专家互不影响，root 实例始终无专家。
"""

import json
import tempfile
from pathlib import Path

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


def _make_package(root: Path, name: str, persona: str) -> None:
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


@pytest.mark.asyncio
async def test_two_sessions_different_experts_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experts_dir = tmp_path / "experts"
    experts_dir.mkdir()
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    _make_package(experts_dir, "expert-b", "你是专家阿乙。")
    monkeypatch.setattr(
        es,
        "get_expert_source",
        lambda: es.LocalDirExpertPackageSource(experts_dir=experts_dir),
    )

    root = JiuWenSwarmDeepAdapter()
    root._instance = create_deep_agent(
        model=_make_model(), system_prompt=ORIGINAL_IDENTITY, workspace=tempfile.mkdtemp()
    )
    child_1 = _make_child_adapter("sess-1")
    child_2 = _make_child_adapter("sess-2")
    root._session_adapters["sess-1"] = child_1
    root._session_adapters["sess-2"] = child_2

    await child_1.apply_expert("expert-a")
    await child_2.apply_expert("expert-b")

    assert _identity_text(child_1) == "你是专家阿甲。"
    assert _identity_text(child_2) == "你是专家阿乙。"
    assert ORIGINAL_IDENTITY in _identity_text(root), "root 实例不应被专家污染"

    # 会话 1 卸载专家不影响会话 2
    await child_1.apply_expert(None)
    assert ORIGINAL_IDENTITY in _identity_text(child_1)
    assert _identity_text(child_2) == "你是专家阿乙。"
