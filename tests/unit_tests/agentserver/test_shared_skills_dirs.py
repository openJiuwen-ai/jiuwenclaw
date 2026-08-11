# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared skills dirs + ENABLED_SKILLS tip wiring (OfficeClaw)."""

from __future__ import annotations

import os

import pytest

from jiuwenswarm.common.local_env_config import (
    ENV_CONFIG_DICT,
    bind_task_env_overlay,
    reset_task_env_overlay,
)
from jiuwenswarm.common.utils import (
    JIUWENCLAW_SHARED_SKILLS_DIRS_ENV,
    JIUWENSWARM_SHARED_SKILLS_DIRS_ENV,
    get_agent_skills_dir,
    get_shared_agent_skills_dirs,
    parse_shared_skills_dirs_raw,
    resolve_agent_registered_skill_dirs,
)
from jiuwenswarm.server.runtime.reload_result import env_touches_shared_skills_dirs
from jiuwenswarm.server.runtime.skill.skill_manager import enabled_skills_from_environ


@pytest.fixture(autouse=True)
def _reset_env():
    saved = dict(os.environ)
    ENV_CONFIG_DICT.clear()
    yield
    os.environ.clear()
    os.environ.update(saved)
    ENV_CONFIG_DICT.clear()


def test_env_touches_shared_skills_dirs():
    assert env_touches_shared_skills_dirs({"JIUWENSWARM_SHARED_SKILLS_DIRS": "/a"})
    assert env_touches_shared_skills_dirs({"JIUWENCLAW_SHARED_SKILLS_DIRS": "/a"})
    assert env_touches_shared_skills_dirs({"ENABLED_SKILLS": "pptx-craft"})
    assert not env_touches_shared_skills_dirs({"API_KEY": "x"})
    assert not env_touches_shared_skills_dirs(None)


def test_get_shared_agent_skills_dirs_uses_tip_not_only_os_environ(tmp_path):
    new_dir = tmp_path / "shared-skills"
    new_dir.mkdir()
    os.environ[JIUWENSWARM_SHARED_SKILLS_DIRS_ENV] = str(tmp_path / "old")
    ENV_CONFIG_DICT[JIUWENSWARM_SHARED_SKILLS_DIRS_ENV] = str(new_dir)

    dirs = get_shared_agent_skills_dirs()
    assert dirs == [new_dir.resolve()]


def test_get_shared_agent_skills_dirs_accepts_legacy_jiuwenclaw_key(tmp_path):
    legacy = tmp_path / "office-claw-skills"
    legacy.mkdir()
    ENV_CONFIG_DICT[JIUWENCLAW_SHARED_SKILLS_DIRS_ENV] = str(legacy)
    assert get_shared_agent_skills_dirs() == [legacy.resolve()]


def test_resolve_agent_registered_skill_dirs_prefers_shared(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    ENV_CONFIG_DICT[JIUWENSWARM_SHARED_SKILLS_DIRS_ENV] = str(shared)
    resolved = resolve_agent_registered_skill_dirs()
    assert resolved == [shared.resolve()]
    assert resolved != [get_agent_skills_dir()]


def test_resolve_agent_registered_skill_dirs_falls_back_to_workspace():
    ENV_CONFIG_DICT.pop(JIUWENSWARM_SHARED_SKILLS_DIRS_ENV, None)
    ENV_CONFIG_DICT.pop(JIUWENCLAW_SHARED_SKILLS_DIRS_ENV, None)
    for key in (
        JIUWENSWARM_SHARED_SKILLS_DIRS_ENV,
        JIUWENCLAW_SHARED_SKILLS_DIRS_ENV,
    ):
        os.environ.pop(key, None)
    assert resolve_agent_registered_skill_dirs() == [get_agent_skills_dir()]


def test_parse_shared_skills_dirs_raw_deduplicates(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    raw = f"{a}{os.pathsep}{a}"
    parsed = parse_shared_skills_dirs_raw(raw)
    assert parsed == [a.resolve()]


def test_enabled_skills_from_environ_reads_tip():
    assert enabled_skills_from_environ() is None
    ENV_CONFIG_DICT["ENABLED_SKILLS"] = "pptx-craft,other"
    overlay = bind_task_env_overlay({"ENABLED_SKILLS": "pptx-craft,other"})
    try:
        assert enabled_skills_from_environ() == "pptx-craft,other"
    finally:
        reset_task_env_overlay(overlay)
