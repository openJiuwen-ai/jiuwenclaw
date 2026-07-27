from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeChannel:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_event(self, ws, event, payload):
        self.events.append({"ws": ws, "event": event, "payload": payload})


def _project(project_id: str = "proj-A"):
    return SimpleNamespace(
        project_id=project_id,
        project_dir="/tmp/proj-A",
        hidden=False,
        work_mode="code",
    )


@pytest.mark.asyncio
async def test_structural_error_pushes_error_event_before_pausing(monkeypatch):
    from jiuwenswarm.server.runtime.session import git_diff_watcher
    from jiuwenswarm.server.runtime.session.project_git import GitError, GitOperationError

    channel = _FakeChannel()
    registry = git_diff_watcher.GitDiffWatcherRegistry(channel=channel)
    watch = await registry.add_watch(
        object(), "proj-A", "sess-1", include_last_turn=True,
    )

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.project_store.get_project_by_id",
        lambda project_id: _project(project_id),
    )

    class _DiffStatusService:
        def get_project_diff_status(self, **kwargs):
            raise GitOperationError(
                GitError(
                    "NOT_GIT_REPOSITORY",
                    "not a git repository",
                    retryable=False,
                )
            )

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.git_diff_status.get_diff_status_service",
        lambda: _DiffStatusService(),
    )

    await registry._poll_loop("proj-A")

    assert channel.events
    assert channel.events[0]["event"] == "project.git.error"
    assert channel.events[0]["payload"]["watch_id"] == watch.watch_id
    assert channel.events[0]["payload"]["detail"]["code"] == "NOT_GIT_REPOSITORY"


@pytest.mark.asyncio
async def test_current_only_watch_does_not_read_last_turn(monkeypatch):
    from jiuwenswarm.server.runtime.session import git_diff_watcher
    from jiuwenswarm.server.runtime.session.git_diff_status import (
        DiffRepoInfo,
        DiffStats,
        DiffSummary,
        ProjectGitDiffStatus,
    )

    channel = _FakeChannel()
    registry = git_diff_watcher.GitDiffWatcherRegistry(channel=channel)
    await registry.add_watch(
        object(), "proj-A", "sess-1", include_last_turn=False,
    )

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.project_store.get_project_by_id",
        lambda project_id: _project(project_id),
    )

    class _DiffStatusService:
        def get_project_diff_status(self, **kwargs):
            return ProjectGitDiffStatus(
                project_id="proj-A",
                session_id=None,
                work_mode="code",
                repo=DiffRepoInfo(
                    is_git=True,
                    repo_root="/tmp/proj-A",
                    branch="main",
                    head="abc123",
                    transient=False,
                ),
                current=DiffSummary(
                    is_dirty=False,
                    stats=DiffStats(),
                    files={},
                ),
            )

    class _DiffService:
        def get_turn_diff_summaries(self, *args, **kwargs):
            raise AssertionError("last_turn should not be read")

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.git_diff_status.get_diff_status_service",
        lambda: _DiffStatusService(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.utils.diff_service.get_diff_service",
        lambda: _DiffService(),
    )

    await registry._compute_and_push("proj-A", registry._get_watches_for_project("proj-A"))

    assert channel.events
    assert channel.events[0]["event"] == "project.git.diff_changed"
    assert channel.events[0]["payload"]["last_turn"] is None


@pytest.mark.asyncio
async def test_empty_session_id_forces_include_last_turn_false(monkeypatch):
    """Bug 修复:``session_id=""`` 时强制关闭 ``include_last_turn``。

    原因:``_compute_and_push`` 用 ``if session_id:`` 判定是否计算 last_turn,
    空串会被判定为 False 而静默跳过 last_turn 计算。若 ``include_last_turn``
    仍为 True,语义不一致——前端误以为有 last_turn 数据。
    """
    from jiuwenswarm.server.runtime.session import git_diff_watcher

    channel = _FakeChannel()
    registry = git_diff_watcher.GitDiffWatcherRegistry(channel=channel)

    # 传入 session_id="" 且 include_last_turn=True
    watch = await registry.add_watch(
        object(), "proj-A", "", include_last_turn=True,
    )

    # 应被强制关闭
    assert watch.include_last_turn is False
    assert watch.session_id == ""
