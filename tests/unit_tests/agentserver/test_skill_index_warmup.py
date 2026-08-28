"""Skill process-index warmup after listen / sync_agents_configs."""

from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.common.local_env_config import ENV_CONFIG_DICT
from jiuwenswarm.common.utils import JIUWENSWARM_SHARED_SKILLS_DIRS_ENV


@pytest.fixture(autouse=True)
def _reset_skill_warmup_task():
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    mod._skill_index_warmup_task = None
    mod._skill_index_warmup_roots_cache = []
    mod._skill_index_warmup_enabled_cache = None
    mod._startup_warmup_task = None
    yield
    task = mod._skill_index_warmup_task
    mod._skill_index_warmup_task = None
    mod._skill_index_warmup_roots_cache = []
    mod._skill_index_warmup_enabled_cache = None
    mod._startup_warmup_task = None
    if task is not None and not task.done():
        task.cancel()


def test_shared_skills_dirs_from_sync_params_reads_office_env(tmp_path) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = tmp_path / "office-claw-skills"
    shared.mkdir()
    params = {
        "agents": [
            {
                "agent_id": "office",
                "env": {"JIUWENSWARM_SHARED_SKILLS_DIRS": str(shared)},
            }
        ]
    }
    assert mod._shared_skills_dirs_from_sync_params(params) == [str(shared.resolve())]


def test_enabled_skills_from_sync_params_uses_office_only() -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = "D:/skills"
    params = {
        "agents": [
            {
                "agent_id": "agentteam",
                "env": {
                    "JIUWENSWARM_SHARED_SKILLS_DIRS": shared,
                    "ENABLED_SKILLS": "",
                },
            },
            {
                "agent_id": "expert-academic-evidence-researcher",
                "env": {
                    "JIUWENSWARM_SHARED_SKILLS_DIRS": shared,
                    "ENABLED_SKILLS": "alpha,beta",
                },
            },
            {
                "agent_id": "office",
                "env": {
                    "JIUWENSWARM_SHARED_SKILLS_DIRS": shared,
                    "ENABLED_SKILLS": "pptx-craft,docx-craft,email-craft",
                },
            },
        ]
    }
    assert (
        mod._enabled_skills_from_sync_params(params)
        == "pptx-craft,docx-craft,email-craft"
    )


def test_enabled_skills_from_sync_params_none_without_office() -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    params = {
        "agents": [
            {"agent_id": "agentteam", "env": {"ENABLED_SKILLS": "alpha,beta"}},
            {"agent_id": "other", "env": {"ENABLED_SKILLS": "beta,gamma"}},
        ]
    }
    assert mod._enabled_skills_from_sync_params(params) is None


@pytest.mark.asyncio
async def test_schedule_skill_index_warmup_after_sync_caches_shared_dirs_enabled_skills(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = tmp_path / "shared"
    shared.mkdir()
    started: list[bool] = []

    def _record(*, force: bool = False) -> None:
        started.append(force)

    monkeypatch.setattr(mod, "_start_skill_index_warmup", _record)
    mod.schedule_skill_index_warmup_after_sync(
        sync_params={
            "agents": [
                {
                    "agent_id": "agentteam",
                    "env": {"ENABLED_SKILLS": "alpha,beta"},
                },
                {
                    "agent_id": "office",
                    "env": {
                        "JIUWENSWARM_SHARED_SKILLS_DIRS": str(shared),
                        "ENABLED_SKILLS": "pptx-craft,docx-craft",
                    },
                },
            ]
        },
        force=True,
    )
    assert started == [True]
    assert mod._skill_index_warmup_enabled_cache == "pptx-craft,docx-craft"


def test_skill_index_warmup_roots_uses_resolve_agent_registered_skill_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = tmp_path / "office-claw-skills"
    shared.mkdir()
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.resolve_agent_registered_skill_dirs",
        lambda: [shared],
    )
    assert mod._skill_index_warmup_roots() == [str(shared.resolve())]


@pytest.mark.asyncio
async def test_warm_skill_md_index_fills_process_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire-check: roots/enabled are passed to warmup_process_skill_index.

    Mock agent-core API so CI does not require a refreshed uv.lock pin.
    """
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    import openjiuwen.harness.rails.skills as skills_pkg

    calls: list[tuple[list[str], str | None]] = []

    def _fake_warmup(roots, *, enabled_skills=None, disabled_skills=None):
        calls.append((list(roots), enabled_skills))
        return {
            "scanned": 1,
            "kept": 1,
            "filled": 1,
            "hits": 0,
            "entries": 1,
            "cost_ms": 1.0,
        }

    monkeypatch.setattr(
        skills_pkg,
        "warmup_process_skill_index",
        _fake_warmup,
        raising=False,
    )
    monkeypatch.setattr(mod, "_skill_index_warmup_roots", lambda: ["/tmp/skills"])
    monkeypatch.setattr(
        mod, "_skill_index_warmup_enabled_skills", lambda: "pptx-craft"
    )

    await mod._warm_skill_md_index()

    assert calls == [(["/tmp/skills"], "pptx-craft")]


@pytest.mark.asyncio
async def test_schedule_skill_index_warmup_after_sync_uses_sync_params(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = tmp_path / "shared"
    shared.mkdir()
    started: list[list[str]] = []

    def _record(*, force: bool = False) -> None:
        started.append(list(mod._skill_index_warmup_roots_cache))

    monkeypatch.setattr(mod, "_start_skill_index_warmup", _record)
    mod.schedule_skill_index_warmup_after_sync(
        sync_params={
            "agents": [
                {
                    "agent_id": "office",
                    "env": {"JIUWENSWARM_SHARED_SKILLS_DIRS": str(shared)},
                }
            ]
        },
        force=True,
    )
    assert started == [[str(shared.resolve())]]


@pytest.mark.asyncio
async def test_schedule_skill_index_warmup_after_sync_skips_without_shared_dirs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_shared_agent_skills_dirs",
        lambda: [],
    )
    called = False

    def _boom(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mod, "_start_skill_index_warmup", _boom)
    mod.schedule_skill_index_warmup_after_sync(sync_params={"agents": []}, force=True)
    assert called is False


@pytest.mark.asyncio
async def test_schedule_skill_index_warmup_after_sync_starts_when_shared_dirs_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = tmp_path / "shared"
    shared.mkdir()
    ENV_CONFIG_DICT[JIUWENSWARM_SHARED_SKILLS_DIRS_ENV] = str(shared)
    started: list[bool] = []

    def _record(*, force: bool = False) -> None:
        started.append(force)

    monkeypatch.setattr(mod, "_start_skill_index_warmup", _record)
    mod.schedule_skill_index_warmup_after_sync(force=True)
    assert started == [True]


@pytest.mark.asyncio
async def test_start_skill_index_warmup_force_cancels_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")

    gate = asyncio.Event()
    cancelled = False

    async def _slow_warm() -> None:
        nonlocal cancelled
        try:
            await gate.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    monkeypatch.setattr(mod, "_warm_skill_md_index", _slow_warm)
    mod._start_skill_index_warmup()
    await asyncio.sleep(0)
    mod._start_skill_index_warmup(force=True)
    await asyncio.sleep(0)
    gate.set()
    assert cancelled is True


@pytest.mark.asyncio
async def test_schedule_skill_index_warmup_after_sync_skips_same_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = tmp_path / "shared"
    shared.mkdir()
    started: list[bool] = []

    def _record(*, force: bool = False) -> None:
        started.append(force)

        async def _done() -> None:
            return None

        mod._skill_index_warmup_task = asyncio.get_running_loop().create_task(_done())

    monkeypatch.setattr(mod, "_start_skill_index_warmup", _record)
    params = {
        "agents": [
            {
                "agent_id": "office",
                "env": {"JIUWENSWARM_SHARED_SKILLS_DIRS": str(shared)},
            }
        ]
    }
    mod.schedule_skill_index_warmup_after_sync(sync_params=params)
    await asyncio.sleep(0)
    mod.schedule_skill_index_warmup_after_sync(sync_params=params)
    assert started == [False]


@pytest.mark.asyncio
async def test_schedule_skill_index_warmup_after_sync_skips_when_inflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mod = pytest.importorskip("jiuwenswarm.server.agent_ws_server")
    shared = tmp_path / "shared"
    shared.mkdir()
    started: list[bool] = []
    gate = asyncio.Event()

    async def _slow() -> None:
        await gate.wait()

    def _record(*, force: bool = False) -> None:
        started.append(force)
        mod._skill_index_warmup_task = asyncio.get_running_loop().create_task(_slow())

    monkeypatch.setattr(mod, "_start_skill_index_warmup", _record)
    params = {
        "agents": [
            {
                "agent_id": "office",
                "env": {"JIUWENSWARM_SHARED_SKILLS_DIRS": str(shared)},
            }
        ]
    }
    mod.schedule_skill_index_warmup_after_sync(sync_params=params)
    mod.schedule_skill_index_warmup_after_sync(sync_params=params, force=True)
    gate.set()
    await asyncio.sleep(0)
    assert started == [False]
