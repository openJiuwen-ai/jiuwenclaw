# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

from jiuwenavatar.common.gitcode_config import sync_gitcode_token_to_skill_config


def test_sync_gitcode_token_updates_existing_config(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "gitcode-repo"
    skill_dir.mkdir(parents=True)
    config_path = skill_dir / "gitcode-repo.json"
    config_path.write_text(
        json.dumps({"gitcode_token": "", "workspaces": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "jiuwenavatar.common.utils.get_agent_skills_dir",
        lambda: skills_dir,
    )

    assert sync_gitcode_token_to_skill_config("pat-secret") is True
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["gitcode_token"] == "pat-secret"
    assert data["workspaces"] == []


def test_sync_gitcode_token_skips_when_config_missing(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    (skills_dir / "gitcode-repo").mkdir(parents=True)

    monkeypatch.setattr(
        "jiuwenavatar.common.utils.get_agent_skills_dir",
        lambda: skills_dir,
    )

    assert sync_gitcode_token_to_skill_config("pat-secret") is False
