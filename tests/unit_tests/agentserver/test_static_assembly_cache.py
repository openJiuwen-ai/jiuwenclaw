from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from openjiuwen.harness.rails import SkillUseRail

from jiuwenswarm.agents.harness.common.rails import runtime_prompt_rail
from jiuwenswarm.server.runtime import static_assembly_cache
from jiuwenswarm.server.runtime.static_assembly_cache import (
    StaticAssemblyCachedSkillUseRail,
    clear_static_assembly_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIUWENSWARM_STATIC_ASSEMBLY_CACHE", "true")
    clear_static_assembly_cache()
    yield
    clear_static_assembly_cache()


@pytest.mark.asyncio
async def test_description_cache_is_identical_coalesced_and_fingerprint_invalidated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\ndescription: first\n---\nbody\n", encoding="utf-8")
    calls = 0

    async def _load(_self, path: Path) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        text = path.read_text(encoding="utf-8")
        return text.split("description:", 1)[1].splitlines()[0].strip()

    monkeypatch.setattr(SkillUseRail, "_load_description", _load)
    rails = [object.__new__(StaticAssemblyCachedSkillUseRail) for _ in range(2)]

    first, second = await asyncio.gather(
        rails[0]._load_description(skill_md),
        rails[1]._load_description(skill_md),
    )
    assert (first, second) == ("first", "first")
    assert calls == 1

    skill_md.write_text("---\ndescription: second value\n---\nbody\n", encoding="utf-8")
    third = await rails[0]._load_description(skill_md)
    assert third == "second value"
    assert calls == 2


@pytest.mark.asyncio
async def test_static_cache_disabled_preserves_original_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIUWENSWARM_STATIC_ASSEMBLY_CACHE", "false")
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("x", encoding="utf-8")
    calls = 0

    async def _load(_self, _path: Path) -> str:
        nonlocal calls
        calls += 1
        return "same-output"

    monkeypatch.setattr(SkillUseRail, "_load_description", _load)
    rails = [object.__new__(StaticAssemblyCachedSkillUseRail) for _ in range(2)]
    assert await rails[0]._load_description(skill_md) == "same-output"
    assert await rails[1]._load_description(skill_md) == "same-output"
    assert calls == 2


@pytest.mark.asyncio
async def test_directory_scan_is_shared_and_skill_file_change_invalidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "skills"
    for name, description in (("alpha", "first"), ("beta", "ignored")):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\ndescription: {description}\n---\nbody\n",
            encoding="utf-8",
        )

    original_iterdir = Path.iterdir
    root_scans = 0

    def _counted_iterdir(path: Path):
        nonlocal root_scans
        if path == root:
            root_scans += 1
        return original_iterdir(path)

    async def _load(_self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        return text.split("description:", 1)[1].splitlines()[0].strip()

    monkeypatch.setattr(Path, "iterdir", _counted_iterdir)
    monkeypatch.setattr(SkillUseRail, "_load_description", _load)
    rails = [
        StaticAssemblyCachedSkillUseRail(
            skills_dir=str(root),
            enabled_skills=["alpha"],
            include_tools=False,
        )
        for _ in range(2)
    ]

    await rails[0]._prepare_skills()
    await rails[1]._prepare_skills()
    assert root_scans == 1
    assert [(skill.name, skill.description) for skill in rails[1].skills] == [
        ("alpha", "first")
    ]

    alpha_md = root / "alpha" / "SKILL.md"
    alpha_md.write_text("---\ndescription: changed\n---\nbody\n", encoding="utf-8")
    await rails[1]._prepare_skills()
    assert root_scans == 2
    assert rails[1].skills[0].description == "changed"


@pytest.mark.asyncio
async def test_cache_hit_defers_dynamic_evolution_refresh_until_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_INVOKE must not duplicate the authoritative pre-model read."""

    root = tmp_path / "skills"
    root.mkdir()
    rail = StaticAssemblyCachedSkillUseRail(
        skills_dir=str(root),
        include_tools=False,
    )
    await rail._prepare_skills()

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.static_assembly_cache._watch_generation",
        lambda _rail: rail._static_scan_generation,
    )
    evolution_reads = 0
    baseline_checks = 0

    async def _fetch() -> None:
        nonlocal evolution_reads
        evolution_reads += 1

    def _baseline(_ctx) -> None:
        nonlocal baseline_checks
        baseline_checks += 1

    monkeypatch.setattr(rail, "_fetch_evolution_texts", _fetch)
    monkeypatch.setattr(rail, "_ensure_session_baseline", _baseline)

    await rail.before_invoke(object())

    assert evolution_reads == 0
    assert baseline_checks == 1


def test_runtime_state_parse_cache_is_exact_and_fingerprint_invalidated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIUWENSWARM_STATIC_ASSEMBLY_CACHE", "true")
    state_path = tmp_path / "runtime_state.yaml"
    state_path.write_text("model: first\nmode: code.normal\n", encoding="utf-8")
    runtime_prompt_rail._runtime_state_cache.clear()

    original_safe_load = runtime_prompt_rail.yaml.safe_load
    parse_calls = 0

    def _safe_load(stream):
        nonlocal parse_calls
        parse_calls += 1
        return original_safe_load(stream)

    monkeypatch.setattr(runtime_prompt_rail.yaml, "safe_load", _safe_load)

    first = runtime_prompt_rail._read_runtime_state(state_path)
    second = runtime_prompt_rail._read_runtime_state(state_path)
    assert first == second == {"model": "first", "mode": "code.normal"}
    assert parse_calls == 1

    state_path.write_text("model: second-value\nmode: code.normal\n", encoding="utf-8")
    third = runtime_prompt_rail._read_runtime_state(state_path)
    assert third == {"model": "second-value", "mode": "code.normal"}
    assert parse_calls == 2


@pytest.mark.asyncio
async def test_skill_watcher_ignores_unrelated_runtime_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _changes(_root: str, recursive: bool = True):
        assert recursive is True
        yield {(2, str(tmp_path / "runtime_state.yaml"))}
        yield {(2, str(tmp_path / "SKILL.md"))}

    monkeypatch.setattr(static_assembly_cache, "awatch", _changes)
    generation = static_assembly_cache._skill_scan_generation

    await static_assembly_cache._watch_skill_root(str(tmp_path))

    assert static_assembly_cache._skill_scan_generation == generation + 1


def test_watch_generation_reuses_prewarmed_normalized_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rail = object.__new__(StaticAssemblyCachedSkillUseRail)
    rail._static_watch_roots = (tmp_path,)
    monkeypatch.setattr(
        static_assembly_cache,
        "_scan_key",
        lambda _rail: (_ for _ in ()).throw(AssertionError("must not renormalize")),
    )
    monkeypatch.setattr(static_assembly_cache, "_ensure_skill_watchers", lambda roots: roots == (tmp_path,))

    assert static_assembly_cache._watch_generation(rail) == static_assembly_cache._skill_scan_generation
