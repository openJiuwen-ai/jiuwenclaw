def test_configured_git_executable_has_priority(tmp_path, monkeypatch):
    import shutil
    from jiuwenswarm.server.runtime.session import project_git

    configured = tmp_path / "git.exe"
    configured.write_text("fake", encoding="utf-8")
    monkeypatch.setenv("CLAW_GIT_EXE", str(configured))
    monkeypatch.setattr(shutil, "which", lambda name: "system-git.exe")

    assert project_git._find_git_executable() == str(configured)


def test_managed_git_does_not_fall_back_to_system_when_missing(tmp_path, monkeypatch):
    import shutil
    from jiuwenswarm.server.runtime.session import project_git

    monkeypatch.setenv("CLAW_RUNTIME_SOURCE", "managed")
    monkeypatch.setenv("CLAW_GIT_EXE", str(tmp_path / "missing-git.exe"))
    monkeypatch.setattr(shutil, "which", lambda name: "system-git.exe")

    assert project_git._find_git_executable() is None
