def test_managed_git_bash_does_not_fall_back_to_system(tmp_path, monkeypatch):
    import shutil
    from jiuwenswarm.common import host_shell

    monkeypatch.setenv("CLAW_RUNTIME_SOURCE", "managed")
    monkeypatch.setenv("CLAW_GIT_BASH_EXE", str(tmp_path / "missing-bash.exe"))
    monkeypatch.setenv("GIT_BASH", str(tmp_path / "missing-bash.exe"))
    monkeypatch.delenv("GIT_BASH_PATH", raising=False)
    monkeypatch.delenv("JIUWENBOX_BASH_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "system-bash.exe")

    assert host_shell.host_bash_exe() is None


def test_command_tool_managed_git_bash_does_not_use_system_candidate(tmp_path, monkeypatch):
    from jiuwenswarm.agents.harness.common.tools import command_tools

    monkeypatch.setattr(command_tools.os, "name", "nt")
    monkeypatch.setenv("CLAW_RUNTIME_SOURCE", "managed")
    monkeypatch.setenv("CLAW_GIT_BASH_EXE", str(tmp_path / "missing-bash.exe"))
    monkeypatch.setenv("GIT_BASH", str(tmp_path / "missing-bash.exe"))

    assert command_tools._available_git_bash() is None


def test_prompt_managed_git_bash_does_not_scan_system_candidates(tmp_path, monkeypatch):
    from jiuwenswarm.agents.harness.common.prompt import shell_environment

    system_git_bash = tmp_path / "Program Files" / "Git" / "bin" / "bash.exe"
    system_git_bash.parent.mkdir(parents=True)
    system_git_bash.touch()

    monkeypatch.setenv("CLAW_RUNTIME_SOURCE", "managed")
    monkeypatch.setenv("CLAW_GIT_BASH_EXE", str(tmp_path / "missing-bash.exe"))
    monkeypatch.setenv("GIT_BASH", str(tmp_path / "missing-bash.exe"))
    monkeypatch.delenv("GIT_BASH_PATH", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setattr(
        shell_environment.shutil,
        "which",
        lambda name: str(tmp_path / "git.exe") if name == "git" else None,
    )

    assert shell_environment._available_git_bash() is None
