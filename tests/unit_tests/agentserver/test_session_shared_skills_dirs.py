# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os

import pytest

from jiuwenclaw.agentserver.reload_result import env_touches_shared_skills_dirs
from jiuwenclaw.agentserver.session_skill_dirs import (
    bind_session_registered_skill_dirs,
    get_bound_session_registered_skill_dirs,
    reset_session_registered_skill_dirs,
)
from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    bind_task_env_overlay,
    reset_task_env_overlay,
    stage_env_overrides,
)
from jiuwenclaw.utils import (
    JIUWENCLAW_SHARED_SKILLS_DIRS_ENV,
    get_shared_agent_skills_dirs,
    parse_shared_skills_dirs_raw,
    resolve_agent_registered_skill_dirs,
)


@pytest.fixture(autouse=True)
def _reset_env():
    saved = dict(os.environ)
    ENV_CONFIG_DICT.clear()
    yield
    os.environ.clear()
    os.environ.update(saved)
    ENV_CONFIG_DICT.clear()


def test_env_touches_shared_skills_dirs():
    assert env_touches_shared_skills_dirs({"JIUWENCLAW_SHARED_SKILLS_DIRS": "/a"})
    assert not env_touches_shared_skills_dirs({"API_KEY": "x"})
    assert not env_touches_shared_skills_dirs(None)


def test_get_shared_agent_skills_dirs_uses_read_env_not_only_os_environ(tmp_path):
    new_dir = tmp_path / "shared-skills"
    new_dir.mkdir()
    os.environ[JIUWENCLAW_SHARED_SKILLS_DIRS_ENV] = str(tmp_path / "old")
    ENV_CONFIG_DICT[JIUWENCLAW_SHARED_SKILLS_DIRS_ENV] = str(new_dir)

    dirs = get_shared_agent_skills_dirs()
    assert dirs == [new_dir.resolve()]


def test_session_binding_overrides_staged_env(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    stage_env_overrides({JIUWENCLAW_SHARED_SKILLS_DIRS_ENV: str(new_dir)})
    ENV_CONFIG_DICT[JIUWENCLAW_SHARED_SKILLS_DIRS_ENV] = str(new_dir)
    overlay_token = bind_task_env_overlay({JIUWENCLAW_SHARED_SKILLS_DIRS_ENV: str(new_dir)})
    skill_token = bind_session_registered_skill_dirs([str(old_dir)])
    try:
        resolved = resolve_agent_registered_skill_dirs()
        assert resolved == [old_dir]
        assert get_bound_session_registered_skill_dirs() == [str(old_dir)]
    finally:
        reset_session_registered_skill_dirs(skill_token)
        reset_task_env_overlay(overlay_token)

    resolved_after = resolve_agent_registered_skill_dirs()
    assert resolved_after == [new_dir.resolve()]


def test_parse_shared_skills_dirs_raw_deduplicates(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    raw = f"{a}{os.pathsep}{a}"
    parsed = parse_shared_skills_dirs_raw(raw)
    assert parsed == [a.resolve()]
