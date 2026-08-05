import asyncio
import subprocess
import threading
from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.utils.diff_service import DiffService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.mark.asyncio
async def test_runtime_state_git_probe_is_non_blocking_and_coalesced(monkeypatch):
    adapter = JiuWenSwarmDeepAdapter()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def _slow_write(**_kwargs) -> None:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(adapter, "_write_runtime_state", _slow_write)
    adapter._schedule_runtime_state_write(
        mode="agent",
        language="zh",
        channel="web",
        session_id="web_runtime_state",
        project_dir=None,
    )
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
    first_task = adapter._runtime_state_write_task
    assert first_task is not None and not first_task.done()

    adapter._schedule_runtime_state_write(
        mode="agent",
        language="zh",
        channel="web",
        session_id="web_runtime_state",
        project_dir=None,
    )
    assert adapter._runtime_state_write_task is first_task
    assert calls == 1

    release.set()
    await asyncio.wait_for(first_task, timeout=1)


@pytest.mark.asyncio
async def test_memory_reindex_is_singleflight_per_workspace_and_config(
    monkeypatch, tmp_path
):
    first = JiuWenSwarmDeepAdapter()
    second = JiuWenSwarmDeepAdapter()
    for adapter in (first, second):
        adapter._workspace_dir = str(tmp_path)
        adapter._memory_embedding_fingerprint = "embed-v2"
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _slow_reindex(self, key):
        nonlocal calls
        calls += 1
        started.set()
        try:
            await release.wait()
        finally:
            with self._MEMORY_REINDEX_KEYS_LOCK:
                self._MEMORY_REINDEX_KEYS.discard(key)

    JiuWenSwarmDeepAdapter._MEMORY_REINDEX_KEYS.clear()
    monkeypatch.setattr(JiuWenSwarmDeepAdapter, "_do_memory_reindex", _slow_reindex)
    first._schedule_memory_reindex()
    second._schedule_memory_reindex()
    await asyncio.wait_for(started.wait(), timeout=1)

    assert calls == 1
    assert first._memory_reindex_task is not None
    assert second._memory_reindex_task is None

    release.set()
    await asyncio.wait_for(first._memory_reindex_task, timeout=1)


def test_ensure_project_gitignore_agent_history_updates_repo_root(tmp_path):
    repo = tmp_path / "repo"
    subdir = repo / "pkg"
    subdir.mkdir(parents=True)
    _git(repo, "init")

    JiuWenSwarmDeepAdapter._ensure_project_gitignore_agent_history(str(subdir))
    JiuWenSwarmDeepAdapter._ensure_project_gitignore_agent_history(str(subdir))

    gitignore = repo / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    assert content.count(".agent_history/") == 1


@pytest.mark.parametrize(
    "existing_rule",
    [
        ".agent_history",
        ".agent_history/",
        ".agent_history/*",
        ".agent_history/**",
        ".agent_history/**/*",
        "**/.agent_history",
        "**/.agent_history/",
        "**/.agent_history/*",
    ],
)
def test_ensure_project_gitignore_agent_history_idempotent_for_equivalent_rules(
    tmp_path, existing_rule
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    gitignore = repo / ".gitignore"
    gitignore.write_text(f"build/\n{existing_rule}\n", encoding="utf-8")

    before = gitignore.read_bytes()

    JiuWenSwarmDeepAdapter._ensure_project_gitignore_agent_history(str(repo))
    JiuWenSwarmDeepAdapter._ensure_project_gitignore_agent_history(str(repo))

    after = gitignore.read_bytes()
    assert before == after, (
        f".gitignore should remain unchanged for equivalent rule {existing_rule!r}"
    )


def test_ensure_project_gitignore_agent_history_preserves_crlf(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    gitignore = repo / ".gitignore"
    original = b"build/\r\ndist/\r\n"
    gitignore.write_bytes(original)

    JiuWenSwarmDeepAdapter._ensure_project_gitignore_agent_history(str(repo))

    after = gitignore.read_bytes()
    # Original CRLF lines must be preserved verbatim.
    assert after.startswith(original)
    # Appended rule uses LF (consistent with the rest of the addition) and is
    # separated from existing content by exactly one blank line.
    assert after.endswith(b"# JiuwenSwarm runtime file operation logs\n.agent_history/\n")
    assert after.count(b".agent_history/") == 1


def test_git_diff_excludes_unignored_agent_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    tracked = repo / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")

    tracked.write_text("after\n", encoding="utf-8")
    history_dir = repo / ".agent_history"
    history_dir.mkdir()
    (history_dir / "file_ops_jiuwenswarm_sess.json").write_text(
        "{\n  \"file.txt\": []\n}\n",
        encoding="utf-8",
    )

    diff = DiffService().get_git_diff(str(repo))

    assert diff is not None
    assert diff["stats"] == {"filesChanged": 1, "linesAdded": 1, "linesRemoved": 1}
    assert str(history_dir / "file_ops_jiuwenswarm_sess.json") not in diff["files"]
