import subprocess
from pathlib import Path

from jiuwenswarm.server.utils.diff_service import DiffService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_git_diff_from_subdir_excludes_untracked_files(tmp_path):
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
    assert str(untracked) not in diff["files"]
    assert diff["stats"]["filesChanged"] == 1
    assert diff["stats"]["linesAdded"] == 1
    assert diff["stats"]["linesRemoved"] == 1


def test_git_diff_stats_include_tracked_files_beyond_detail_cap(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    for i in range(60):
        file_path = repo / f"file-{i:02d}.txt"
        file_path.write_text("before\n", encoding="utf-8")

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    for i in range(60):
        file_path = repo / f"file-{i:02d}.txt"
        file_path.write_text("after\n", encoding="utf-8")

    diff = DiffService().get_git_diff(str(repo))

    assert diff is not None
    assert len(diff["files"]) == 50
    assert diff["stats"]["filesChanged"] == 60
    assert diff["stats"]["linesAdded"] == 60
    assert diff["stats"]["linesRemoved"] == 60


def test_git_diff_preserves_tabs_in_file_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "core.quotepath", "false")

    tab_name = "dir\tfile.txt"
    tab_file = repo / tab_name
    tab_file.write_text("before\nbefore2\nbefore3\n", encoding="utf-8")
    _git(repo, "add", "--", tab_name)
    _git(repo, "commit", "-m", "initial")

    tab_file.write_text("after\nafter2\n", encoding="utf-8")

    diff = DiffService().get_git_diff(str(repo))

    assert diff is not None
    tab_abs = str(tab_file)
    assert tab_abs in diff["files"]
    file_info = diff["files"][tab_abs]
    assert file_info["linesAdded"] == 2
    assert file_info["linesRemoved"] == 3


def test_git_diff_staged_rename_with_modification_keeps_hunks(tmp_path):
    """staged rename + 内容修改时, hunks 应通过新路径对齐 (非空)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    old = repo / "old.txt"
    old.write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git(repo, "add", "old.txt")
    _git(repo, "commit", "-m", "initial")

    _git(repo, "mv", "old.txt", "new.txt")
    new = repo / "new.txt"
    new.write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
    _git(repo, "add", "new.txt")

    diff = DiffService().get_git_diff(str(repo))

    assert diff is not None
    assert str(new) in diff["files"]
    file_info = diff["files"][str(new)]
    assert file_info["linesAdded"] == 1
    assert file_info["linesRemoved"] == 1
    # rename 前 numstat key 是 "old => new", 与 hunk key "new" 不一致会导致 hunks 丢失
    assert len(file_info["hunks"]) == 1
    assert file_info["hunks"][0]["lines"]  # 非空


def test_git_diff_staged_rename_brace_form_keeps_hunks(tmp_path):
    """目录级 rename 触发 brace 简写 numstat (a/{b => c}/d.txt) 时, hunks 仍对齐."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    old = repo / "a" / "b" / "d.txt"
    old.parent.mkdir(parents=True)
    old.write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git(repo, "add", "a/b/d.txt")
    _git(repo, "commit", "-m", "initial")

    # a/b/d.txt -> a/c/d.txt: 共同前缀 a/ 和后缀 /d.txt, numstat 输出 a/{b => c}/d.txt
    new = repo / "a" / "c" / "d.txt"
    new.parent.mkdir(parents=True)
    _git(repo, "mv", "a/b/d.txt", "a/c/d.txt")
    new.write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
    _git(repo, "add", "a/c/d.txt")

    diff = DiffService().get_git_diff(str(repo))

    assert diff is not None
    assert str(new) in diff["files"]
    file_info = diff["files"][str(new)]
    assert file_info["linesAdded"] == 1
    assert file_info["linesRemoved"] == 1
    # brace 简写 numstat key "a/{b => c}/d.txt" 需展开为 "a/c/d.txt" 与 hunk key 对齐
    assert len(file_info["hunks"]) == 1
    assert file_info["hunks"][0]["lines"]  # 非空
