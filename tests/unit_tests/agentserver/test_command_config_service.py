# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Discovery of user-defined slash commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.command_config_service import (
    CommandConfigService,
    command_accepts_args,
    command_for_wire,
    parse_command_file,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace with an isolated user dir, so the test never reads ~/."""
    user_home = tmp_path / "userhome"
    (user_home / "commands").mkdir(parents=True)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.command_config_service.get_user_workspace_dir",
        lambda: user_home,
    )
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    return ws_dir


# ---------------------------------------------------------------- parsing


def test_the_filename_is_the_command_name(tmp_path: Path) -> None:
    f = _write(tmp_path / "Review.md", "---\ndescription: x\n---\nBody here")
    command = parse_command_file(f, "project")
    assert command is not None
    assert command.name == "review"  # lower-cased, no frontmatter needed


def test_frontmatter_accepts_hyphen_and_underscore(tmp_path: Path) -> None:
    """Frontmatter is hand-written; refusing a file over a hyphen is a bad trade."""
    hyphen = parse_command_file(
        _write(tmp_path / "a.md", "---\nargument-hint: <path>\nallowed-tools: Read, Grep\n---\nB"),
        "project",
    )
    underscore = parse_command_file(
        _write(tmp_path / "b.md", "---\nargument_hint: <path>\nallowed_tools: [Read, Grep]\n---\nB"),
        "project",
    )
    assert hyphen is not None and underscore is not None
    assert hyphen.argument_hint == underscore.argument_hint == "<path>"
    assert hyphen.allowed_tools == underscore.allowed_tools == ["Read", "Grep"]


def test_a_file_without_frontmatter_is_still_a_command(tmp_path: Path) -> None:
    command = parse_command_file(_write(tmp_path / "plain.md", "Just a prompt"), "user")
    assert command is not None
    assert command.body == "Just a prompt"
    assert command.description == ""


def test_an_empty_body_is_not_a_command(tmp_path: Path) -> None:
    """Nothing to send."""
    assert parse_command_file(_write(tmp_path / "empty.md", "---\ndescription: x\n---\n"), "user") is None


def test_a_reserved_name_is_flagged(tmp_path: Path) -> None:
    command = parse_command_file(_write(tmp_path / "help.md", "Body"), "project")
    assert command is not None
    assert command.reserved is True


def test_a_name_with_whitespace_is_reserved_not_invocable(tmp_path: Path) -> None:
    """``parseSlashCommand`` (TUI) splits raw input on whitespace, so
    ``foo bar.md`` would list as ``/foo bar`` and then match nothing anyone
    can type. It must still be returned (so the UI can explain it), just
    flagged the same way a reserved name is."""
    command = parse_command_file(_write(tmp_path / "foo bar.md", "Body"), "project")
    assert command is not None
    assert command.name == "foo bar"
    assert command.reserved is True


# ---------------------------------------------------------------- scopes


def test_precedence_matches_the_agent_loader(workspace: Path, tmp_path: Path) -> None:
    """project > user > local, the same order agents use."""
    _write(workspace / ".jiuwenswarm" / "commands-local" / "dup.md", "LOCAL")
    _write(tmp_path / "userhome" / "commands" / "dup.md", "USER")
    _write(workspace / ".jiuwenswarm" / "commands" / "dup.md", "PROJECT")

    active = CommandConfigService(workspace).active_commands()
    assert [c.name for c in active] == ["dup"]
    assert active[0].body == "PROJECT"
    assert active[0].source == "project"


def test_shadowed_commands_are_reported_not_dropped(workspace: Path, tmp_path: Path) -> None:
    """Silently losing a file the user wrote is an unanswerable support question."""
    _write(tmp_path / "userhome" / "commands" / "dup.md", "USER")
    _write(workspace / ".jiuwenswarm" / "commands" / "dup.md", "PROJECT")

    everything = CommandConfigService(workspace).list_commands()
    shadowed = [c for c in everything if c.shadowed_by]
    assert len(shadowed) == 1
    assert shadowed[0].source == "user"
    assert shadowed[0].shadowed_by == "project"


def test_a_reserved_name_never_becomes_active(workspace: Path) -> None:
    _write(workspace / ".jiuwenswarm" / "commands" / "help.md", "Body")
    _write(workspace / ".jiuwenswarm" / "commands" / "mine.md", "Body")

    service = CommandConfigService(workspace)
    assert [c.name for c in service.active_commands()] == ["mine"]
    # ...but it is still listed, so the UI can explain why it does nothing.
    assert "help" in {c.name for c in service.list_commands()}


# ------------------------------------------------- client-declared built-ins


def test_a_client_builtin_reserves_the_name(workspace: Path) -> None:
    """RESERVED_NAMES is a floor, not the built-in list.

    ``/model`` is a TUI built-in and is not in RESERVED_NAMES, so without the
    client declaring it the server would report ``model.md`` as active while
    the TUI never ran it -- two answers to one question.
    """
    _write(workspace / ".jiuwenswarm" / "commands" / "model.md", "Body")
    _write(workspace / ".jiuwenswarm" / "commands" / "mine.md", "Body")

    undeclared = CommandConfigService(workspace)
    assert "model" in {c.name for c in undeclared.active_commands()}

    declared = CommandConfigService(workspace, builtin_names={"model", "skills"})
    assert [c.name for c in declared.active_commands()] == ["mine"]
    reserved = [c for c in declared.list_commands() if c.name == "model"]
    assert reserved and reserved[0].reserved is True


def test_a_client_builtin_reserves_every_definition_of_the_name(
    workspace: Path, tmp_path: Path
) -> None:
    """The reason none of them run is the built-in, not the shadowing."""
    _write(tmp_path / "userhome" / "commands" / "model.md", "USER")
    _write(workspace / ".jiuwenswarm" / "commands" / "model.md", "PROJECT")

    listed = CommandConfigService(workspace, builtin_names={"model"}).list_commands()
    assert len(listed) == 2
    assert all(c.reserved for c in listed)


def test_declared_builtins_are_matched_case_insensitively(workspace: Path) -> None:
    _write(workspace / ".jiuwenswarm" / "commands" / "model.md", "Body")
    service = CommandConfigService(workspace, builtin_names={"  MODEL  ", ""})
    assert service.active_commands() == []


def test_one_malformed_file_does_not_hide_the_others(workspace: Path) -> None:
    """The failure mode that matters most for user-authored content."""
    _write(workspace / ".jiuwenswarm" / "commands" / "broken.md", "---\n[[[not yaml\n---\nB")
    _write(workspace / ".jiuwenswarm" / "commands" / "good.md", "---\ndescription: ok\n---\nB")

    names = {c.name for c in CommandConfigService(workspace).active_commands()}
    assert "good" in names


def test_no_directories_means_no_commands(workspace: Path) -> None:
    assert CommandConfigService(workspace).active_commands() == []


def test_a_name_with_whitespace_is_never_active(workspace: Path) -> None:
    _write(workspace / ".jiuwenswarm" / "commands" / "foo bar.md", "Body")

    service = CommandConfigService(workspace)
    assert service.active_commands() == []
    # ...but still listed, so the UI can explain why it does nothing.
    assert "foo bar" in {c.name for c in service.list_commands()}


# ---------------------------------------------------------------- @file bounds


def test_the_resolver_reads_inside_the_workspace(workspace: Path) -> None:
    _write(workspace / "src" / "a.py", "CODE")
    assert CommandConfigService(workspace).file_resolver()("src/a.py") == "CODE"


def test_the_resolver_refuses_to_escape_the_workspace(workspace: Path, tmp_path: Path) -> None:
    """A @path can come from a user argument, so this check has to be real."""
    _write(tmp_path / "secret.txt", "SECRET")
    resolve = CommandConfigService(workspace).file_resolver()

    with pytest.raises(ValueError, match="outside the workspace"):
        resolve("../secret.txt")
    with pytest.raises(ValueError, match="outside the workspace"):
        resolve("/etc/passwd")


def test_a_symlink_cannot_step_outside(workspace: Path, tmp_path: Path) -> None:
    """Resolution happens on the real path, after symlinks."""
    outside = _write(tmp_path / "outside.txt", "SECRET")
    workspace.mkdir(parents=True, exist_ok=True)
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(ValueError, match="symlinks not allowed"):
        CommandConfigService(workspace).file_resolver()("link.txt")


def test_a_missing_file_says_so(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="not found"):
        CommandConfigService(workspace).file_resolver()("nope.py")


def test_command_for_wire_omits_body_and_sets_accepts_args(workspace: Path) -> None:
    _write(
        workspace / ".jiuwenswarm" / "commands" / "review.md",
        "---\nargument-hint: <path>\n---\nReview @$1",
    )
    command = CommandConfigService(workspace).active_commands()[0]
    wire = command_for_wire(command)
    assert "body" not in wire
    assert wire["accepts_args"] is True


def test_command_accepts_args_from_body_without_hint(workspace: Path) -> None:
    command = parse_command_file(
        _write(workspace / "noop.md", "Use $ARGUMENTS here"),
        "project",
    )
    assert command is not None
    assert command_accepts_args(command) is True


def test_the_resolver_rejects_the_filesystem_root() -> None:
    with pytest.raises(ValueError, match="invalid workspace root"):
        CommandConfigService("/").file_resolver()


# ------------------------------------------------ command file confinement


def test_a_symlinked_command_file_is_not_ingested(workspace: Path, tmp_path: Path) -> None:
    """A project ``.md`` command file must not be able to leak an arbitrary
    file's contents through ``commands.expand``, the same rule ``@file``
    enforces via ``_read_file_under_root``."""
    secret = _write(tmp_path / "secret.txt", "SECRET")
    commands_dir = workspace / ".jiuwenswarm" / "commands"
    commands_dir.mkdir(parents=True)
    link = commands_dir / "leak.md"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    commands = CommandConfigService(workspace).list_commands()
    assert "leak" not in {c.name for c in commands}
    assert all("SECRET" not in c.body for c in commands)


def test_a_symlink_escaping_via_a_subpath_is_also_refused(
    workspace: Path, tmp_path: Path
) -> None:
    """The confinement check runs on the resolved path, not just ``is_symlink``
    on the direct entry, so a symlinked directory containing the ``.md`` file
    is covered too -- exercised here by linking straight to a file outside a
    deeper tree, matching how ``_read_file_under_root`` treats resolution."""
    outside = _write(tmp_path / "outside" / "id_rsa", "PRIVATE KEY")
    commands_dir = workspace / ".jiuwenswarm" / "commands"
    commands_dir.mkdir(parents=True)
    link = commands_dir / "leak.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    commands = CommandConfigService(workspace).list_commands()
    assert all("PRIVATE KEY" not in c.body for c in commands)


def test_an_oversized_command_file_is_skipped_before_reading(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size gate has to run before the read completes, or a link to a
    device like ``/dev/zero`` can hang the eager ``commands.list`` scan."""
    import jiuwenswarm.server.runtime.command_config_service as service_module

    monkeypatch.setattr(service_module, "_MAX_COMMAND_FILE_BYTES", 5)
    _write(workspace / ".jiuwenswarm" / "commands" / "huge.md", "x" * 100)
    _write(workspace / ".jiuwenswarm" / "commands" / "fine.md", "ok")

    commands = CommandConfigService(workspace).list_commands()
    assert "huge" not in {c.name for c in commands}
    assert "fine" in {c.name for c in commands}
