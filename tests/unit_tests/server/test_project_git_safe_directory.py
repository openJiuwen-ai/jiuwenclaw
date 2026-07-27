from __future__ import annotations

import subprocess

from jiuwenswarm.server.runtime.session.project_store import Project


def _cp(args: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=args,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_git_status_probe_returns_dubious_ownership_error(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.server.runtime.session import project_git

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = Project(
        project_id="proj_test",
        name="test",
        project_dir=str(project_dir),
        work_mode="code",
    )
    calls: list[list[str]] = []

    def fake_run_git(args, *, cwd, timeout=project_git.GIT_COMMAND_TIMEOUT_SEC):
        calls.append(args)
        if args == ["rev-parse", "--show-toplevel"]:
            return _cp(
                ["git", *args],
                128,
                stderr=(
                    "fatal: detected dubious ownership in repository at "
                    f"'{project_dir}'"
                ),
            )
        return _cp(["git", *args], 0)

    monkeypatch.setattr(project_git, "_run_git", fake_run_git)
    monkeypatch.setattr(project_git, "_find_git_executable", lambda: "git")

    status = project_git._git_to_repo_status(project)

    assert status.error is not None
    assert status.error.code == "GIT_DUBIOUS_OWNERSHIP"
    assert status.error.stderr
    assert "git config --global --add safe.directory" in status.error.hint
    assert project_dir.resolve().as_posix() in status.error.hint
    assert calls == [["rev-parse", "--show-toplevel"]]


def test_project_create_probe_returns_dubious_ownership_error(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.server.runtime.session import project_git

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("demo", encoding="utf-8")
    project = Project(
        project_id="proj_test",
        name="test",
        project_dir=str(project_dir),
        work_mode="code",
    )

    def fake_run_git(args, *, cwd, timeout=project_git.GIT_COMMAND_TIMEOUT_SEC):
        if args == ["rev-parse", "--show-toplevel"]:
            return _cp(
                ["git", *args],
                128,
                stderr="fatal: dubious ownership",
            )
        return _cp(["git", *args], 0)

    monkeypatch.setattr(project_git, "_run_git", fake_run_git)
    monkeypatch.setattr(project_git, "_find_git_executable", lambda: "git")

    result = project_git.ProjectGitService()._probe_on_project_create(project)

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "GIT_DUBIOUS_OWNERSHIP"
    assert "git config --global --add safe.directory" in result.error.hint


def test_git_init_returns_dubious_ownership_error(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.server.runtime.session import project_git

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = Project(
        project_id="proj_test",
        name="test",
        project_dir=str(project_dir),
        work_mode="code",
    )

    def fake_run_git(args, *, cwd, timeout=project_git.GIT_COMMAND_TIMEOUT_SEC):
        if args == ["check-ref-format", "--branch", "main"]:
            return _cp(["git", *args], 0, stdout="main\n")
        if args == ["init", "-b", "main", str(project_dir)]:
            return _cp(
                ["git", *args],
                128,
                stderr="fatal: detected dubious ownership in repository",
            )
        return _cp(["git", *args], 0)

    monkeypatch.setattr(project_git, "_run_git", fake_run_git)
    monkeypatch.setattr(project_git, "_find_git_executable", lambda: "git")

    status = project_git.ProjectGitService.init(project)

    assert status.error is not None
    assert status.error.code == "GIT_DUBIOUS_OWNERSHIP"
    assert "git config --global --add safe.directory" in status.error.hint


def test_dubious_ownership_status_maps_to_specific_snapshot_status():
    from jiuwenswarm.server.runtime.session import project_git

    status = project_git.GitRepoStatus(
        is_git=False,
        error=project_git.GitError(
            "GIT_DUBIOUS_OWNERSHIP",
            "git repository ownership check failed",
        ),
    )

    assert project_git._map_status_string(status) == "dubious_ownership"
