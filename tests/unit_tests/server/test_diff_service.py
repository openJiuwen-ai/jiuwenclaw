import subprocess
from pathlib import Path

from jiuwenswarm.server.utils.diff_service import DiffService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_git_diff_from_subdir_includes_repo_root_untracked_files(tmp_path):
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
    subdir = repo / "pkg"
    subdir.mkdir()
    untracked = repo / "未跟踪.txt"
    untracked.write_text("line one\nline two\n", encoding="utf-8")

    diff = DiffService().get_git_diff(str(subdir))

    assert diff is not None
    assert str(tracked) in diff["files"]
    assert str(untracked) in diff["files"]
    assert diff["files"][str(untracked)]["isUntracked"] is True
    assert diff["files"][str(untracked)]["linesAdded"] == 2
    assert diff["stats"]["filesChanged"] == 2
