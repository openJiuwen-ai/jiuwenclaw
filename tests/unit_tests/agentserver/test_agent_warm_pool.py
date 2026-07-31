from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.agent_warm_pool import AgentWarmPool


class _FakeRootAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.prepared: list[str] = []
        self.cleaned: list[str] = []

    async def prepare_session(self, *, session_id: str, **_kwargs) -> None:
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("prepare failed")
        self.prepared.append(session_id)

    async def cleanup_session_runtime(self, session_id: str) -> bool:
        self.cleaned.append(session_id)
        return True


class _FakeManager:
    def __init__(self, agent: _FakeRootAgent) -> None:
        self.agent = agent
        self.pins = 0

    async def get_agent(self, **_kwargs):
        return self.agent

    def pin_agent(self, _agent) -> None:
        self.pins += 1

    def unpin_agent(self, _agent) -> None:
        self.pins -= 1


async def _wait_until(predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


@pytest.fixture
def isolated_pool(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_warm_pool.get_agent_sessions_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_warm_pool.project_store.list_projects",
        lambda **_kwargs: [],
    )

    def factory(agent: _FakeRootAgent) -> AgentWarmPool:
        return AgentWarmPool(_FakeManager(agent), max_concurrency=4)

    yield factory


def test_warm_key_normalizes_project_directory(tmp_path: Path) -> None:
    key = AgentWarmPool.make_key(
        channel_id=" web ",
        project_id="project-a",
        project_dir=str(tmp_path / ".." / tmp_path.name),
        work_mode="CODE",
    )
    assert key.channel_id == "web"
    assert key.project_dir == str(tmp_path.resolve()).lower()
    assert key.agent_mode == "code"


@pytest.mark.asyncio
async def test_one_ready_slot_per_key_and_atomic_claim(isolated_pool) -> None:
    agent = _FakeRootAgent()
    pool = isolated_pool(agent)
    stats = await pool.sync(["web"], config={"model": "a"})
    assert stats["target"] == 2
    await _wait_until(lambda: len(pool._slots) == 2)

    work_key = pool.make_key(
        channel_id="web",
        project_id="default",
        project_dir="",
        work_mode="work",
    )
    claims = await asyncio.gather(pool.claim(work_key), pool.claim(work_key))
    assert len({claim.session_id for claim in claims}) == 2
    assert sum(claim.prewarm_hit for claim in claims) == 1
    assert {claim.prewarm_status for claim in claims} == {"ready", "warming"}
    await _wait_until(lambda: work_key in pool._slots)
    assert len([key for key in pool._slots if key == work_key]) == 1
    await pool.close()


@pytest.mark.asyncio
async def test_config_revision_replaces_unclaimed_slots(isolated_pool) -> None:
    agent = _FakeRootAgent()
    pool = isolated_pool(agent)
    await pool.sync(["web"], config={"model": "old"})
    await _wait_until(lambda: len(pool._slots) == 2)
    old_ids = {slot.session_id for slot in pool._slots.values()}

    await pool.sync(["web"], config={"model": "new"})
    await _wait_until(
        lambda: len(pool._slots) == 2
        and not old_ids.intersection(slot.session_id for slot in pool._slots.values())
    )
    assert old_ids.issubset(set(agent.cleaned))
    await pool.close()


@pytest.mark.asyncio
async def test_failed_prepare_never_becomes_ready(isolated_pool) -> None:
    pool = isolated_pool(_FakeRootAgent(fail=True))
    await pool.sync(["web"], config={"model": "broken"})
    await _wait_until(lambda: not pool._tasks)
    stats = await pool.stats()
    assert stats["ready"] == 0
    assert stats["failed"] == 2
    await pool.close()


@pytest.mark.asyncio
async def test_claim_marker_survives_until_metadata_activation(isolated_pool) -> None:
    pool = isolated_pool(_FakeRootAgent())
    await pool.sync(["web"], config={"model": "ready"})
    await _wait_until(lambda: len(pool._slots) == 2)
    key = pool.make_key(
        channel_id="web",
        project_id="default",
        project_dir="",
        work_mode="work",
    )

    claim = await pool.claim(key)
    marker = pool._marker_path(claim.session_id)
    assert claim.prewarm_hit is True
    assert marker.is_file()

    pool.clear_marker(claim.session_id)
    assert not marker.exists()
    await pool.close()


def test_new_boot_cleans_only_metadata_less_marked_workspace(
    isolated_pool, tmp_path: Path
) -> None:
    old_pool = isolated_pool(_FakeRootAgent())
    key = old_pool.make_key(
        channel_id="web",
        project_id="default",
        project_dir="",
        work_mode="work",
    )
    stale_id = "web_stale"
    persisted_id = "web_persisted"
    for session_id in (stale_id, persisted_id):
        (tmp_path / session_id).mkdir()
        old_pool._write_marker(session_id, key)
    (tmp_path / persisted_id / "metadata.json").write_text("{}", encoding="utf-8")

    isolated_pool(_FakeRootAgent())

    assert not (tmp_path / stale_id).exists()
    assert (tmp_path / persisted_id / "metadata.json").is_file()
