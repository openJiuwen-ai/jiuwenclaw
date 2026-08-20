# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for #2948: workspace migration silently discards cron jobs.

``_migrate_legacy_workspace`` relocates ``agent/home/cron_jobs.json`` to
``gateway/cron_jobs.json`` and removes ``agent/home``. ``get_cron_jobs_path``
must follow the file, or the scheduler reads a path that no longer exists and
every scheduled job silently stops firing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jiuwenswarm.common import utils
from jiuwenswarm.common.utils import (
    _migrate_legacy_workspace,
    get_cron_jobs_path,
)
from jiuwenswarm.gateway.cron.store import CronJobStore


@pytest.fixture
def ws(monkeypatch, tmp_path: Path) -> Path:
    """Point the workspace cache at an isolated temp dir."""
    monkeypatch.setattr(utils, "_workspace_base_dir", tmp_path)
    return tmp_path


def _write_cron(path: Path, jobs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "jobs": jobs}, ensure_ascii=False),
        encoding="utf-8",
    )


_JOB = {
    "id": "job-x",
    "name": "job-x",
    "cron_expr": "0 9 * * *",
    "timezone": "Asia/Shanghai",
    "description": "test job",
    "targets": "web",
    "work_mode": "work",
    "enabled": True,
}


def test_migrate_relocates_cron_and_path_follows(ws: Path) -> None:
    """After migration the file lives in gateway/ and the path must follow."""
    _write_cron(ws / "agent" / "home" / "cron_jobs.json", [_JOB])
    _migrate_legacy_workspace(ws)

    assert not (ws / "agent" / "home").exists()            # source removed
    assert (ws / "gateway" / "cron_jobs.json").exists()    # file relocated
    assert get_cron_jobs_path() == ws / "gateway" / "cron_jobs.json"


def test_path_stays_legacy_when_not_migrated(ws: Path) -> None:
    """Pre-migration deployments keep reading agent/home/cron_jobs.json."""
    _write_cron(ws / "agent" / "home" / "cron_jobs.json", [_JOB])
    assert get_cron_jobs_path() == ws / "agent" / "home" / "cron_jobs.json"


def test_path_fresh_workspace_is_legacy(ws: Path) -> None:
    """A fresh workspace (no files yet) uses the legacy location, as before."""
    assert get_cron_jobs_path() == ws / "agent" / "home" / "cron_jobs.json"


async def test_store_reads_jobs_after_migration(ws: Path) -> None:
    """The scheduler store must load the migrated jobs (regression for #2948)."""
    _write_cron(ws / "agent" / "home" / "cron_jobs.json", [_JOB])
    _migrate_legacy_workspace(ws)

    store = CronJobStore()
    jobs = await store.list_jobs()
    assert [j.id for j in jobs] == ["job-x"]
