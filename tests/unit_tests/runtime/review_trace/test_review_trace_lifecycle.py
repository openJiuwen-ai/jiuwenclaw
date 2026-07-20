# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import jiuwenavatar.server.runtime.review_trace as review_trace_module
from jiuwenavatar.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter


class _FakeAgent:
    def __init__(self) -> None:
        self.registered: list[object] = []
        self.unregistered: list[object] = []

    async def register_rail(self, rail: object) -> None:
        self.registered.append(rail)

    async def unregister_rail(self, rail: object) -> None:
        self.unregistered.append(rail)


def _make_adapter(agent: _FakeAgent) -> JiuWenClawDeepAdapter:
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._instance = agent
    adapter._committer_review_trace_rail = None
    adapter._committer_review_trace_rail_registered = False
    adapter._committer_review_trace_avatar_id = ""
    adapter._committer_review_trace_registered_agent = None
    adapter._committer_review_trace_lock = asyncio.Lock()
    return adapter


def _patch_review_trace_builders(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    created: list[tuple[str, object]] = []

    def build(*, avatar_id: str = "") -> object:
        rail = object()
        created.append((avatar_id, rail))
        return rail

    monkeypatch.setattr(review_trace_module, "build_committer_review_trace_rail", build)
    monkeypatch.setattr(
        review_trace_module,
        "committer_review_trace_base_dir",
        lambda *, avatar_id="": Path("review-traces") / avatar_id,
    )
    monkeypatch.setattr(
        review_trace_module,
        "should_collect_committer_review_trace",
        lambda persona_id: persona_id == "committer",
    )
    return created


@pytest.mark.asyncio
async def test_committer_registers_once_and_non_committer_unregisters(monkeypatch) -> None:
    created = _patch_review_trace_builders(monkeypatch)
    agent = _FakeAgent()
    adapter = _make_adapter(agent)
    committer = SimpleNamespace(persona_id="committer", avatar_id="avatar-1")

    await adapter._sync_committer_review_trace_rail(committer)
    await adapter._sync_committer_review_trace_rail(committer)

    assert [avatar_id for avatar_id, _rail in created] == ["avatar-1"]
    assert agent.registered == [created[0][1]]
    assert adapter._committer_review_trace_registered_agent is agent

    await adapter._sync_committer_review_trace_rail(
        SimpleNamespace(persona_id="developer", avatar_id="avatar-1")
    )

    assert agent.unregistered == [created[0][1]]
    assert adapter._committer_review_trace_rail_registered is False
    assert adapter._committer_review_trace_registered_agent is None


@pytest.mark.asyncio
async def test_avatar_switch_rebinds_review_trace_rail(monkeypatch) -> None:
    created = _patch_review_trace_builders(monkeypatch)
    agent = _FakeAgent()
    adapter = _make_adapter(agent)

    await adapter._sync_committer_review_trace_rail(
        SimpleNamespace(persona_id="committer", avatar_id="avatar-1")
    )
    await adapter._sync_committer_review_trace_rail(
        SimpleNamespace(persona_id="committer", avatar_id="avatar-2")
    )

    first_rail = created[0][1]
    second_rail = created[1][1]
    assert agent.unregistered == [first_rail]
    assert agent.registered == [first_rail, second_rail]
    assert adapter._committer_review_trace_rail is second_rail
    assert adapter._committer_review_trace_avatar_id == "avatar-2"


@pytest.mark.asyncio
async def test_agent_replacement_recovers_stale_registration(monkeypatch) -> None:
    created = _patch_review_trace_builders(monkeypatch)
    first_agent = _FakeAgent()
    second_agent = _FakeAgent()
    adapter = _make_adapter(first_agent)
    context = SimpleNamespace(persona_id="committer", avatar_id="avatar-1")

    await adapter._sync_committer_review_trace_rail(context)
    rail = created[0][1]
    adapter._instance = second_agent

    await adapter._sync_committer_review_trace_rail(context)

    assert first_agent.unregistered == [rail]
    assert second_agent.registered == [rail]
    assert adapter._committer_review_trace_registered_agent is second_agent
    assert adapter._committer_review_trace_rail_registered is True


@pytest.mark.asyncio
async def test_cleanup_unregisters_and_clears_review_trace_state(monkeypatch) -> None:
    created = _patch_review_trace_builders(monkeypatch)
    agent = _FakeAgent()
    adapter = _make_adapter(agent)
    adapter._close_a2x_client = AsyncMock()

    await adapter._sync_committer_review_trace_rail(
        SimpleNamespace(persona_id="committer", avatar_id="avatar-1")
    )
    await adapter.cleanup()

    assert agent.unregistered == [created[0][1]]
    assert adapter._committer_review_trace_rail is None
    assert adapter._committer_review_trace_avatar_id == ""
    assert adapter._committer_review_trace_rail_registered is False
    assert adapter._committer_review_trace_registered_agent is None
    adapter._close_a2x_client.assert_awaited_once_with()
