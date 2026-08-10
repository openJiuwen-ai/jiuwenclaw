# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""User-defined slash commands loaded from Markdown files.

Sources, highest precedence first -- the same three scopes and the same order
as :mod:`agent_config_service`, deliberately: two different precedence rules for
two kinds of ``.md`` file in the same tree is how confident wrong answers get
made.

- project: ``<workspace>/.jiuwenswarm/commands/*.md``
- user:    ``~/.jiuwenswarm/commands/*.md``
- local:   ``<workspace>/.jiuwenswarm/commands-local/*.md``

Built-in commands live in the TUI and are **not** loaded here. They always win a
name collision; see ``list_commands`` for why the loser is reported rather than
dropped.

File format is YAML frontmatter + Markdown body, where the body is the prompt.
The command's name is its **filename**, not a frontmatter field: a file called
``review.md`` is ``/review``. Frontmatter carries only what the UI needs to
describe and gate the command.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import yaml

from jiuwenswarm.common.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)

CommandSource = Literal["user", "project", "local"]

# Built-ins a user file must never take over, enforced even when the client
# declares nothing. Shadowing /help would make the system undiscoverable;
# shadowing /compact or /rewind would risk a conversation. The list is
# intentionally short: only commands whose loss is not recoverable by typing
# something else.
#
# It is a floor, not the built-in list. The real built-ins live in the client
# -- the TUI has ~97 of them and the Web has its own set -- so the server
# cannot enumerate them. Clients pass their own names via ``builtin_names``;
# see :class:`CommandConfigService`.
RESERVED_NAMES = frozenset({
    "help", "compact", "rewind", "clear", "exit", "quit", "config", "statusline",
})

_MAX_BODY_CHARS = 100_000

# Gate applied to the file on disk, before anything is read into memory. A
# command body is capped at `_MAX_BODY_CHARS` *after* parsing, which still
# means reading the whole file first; a symlink to something like `/dev/zero`
# would hang the eager `commands.list` scan long before that cap applies.
_MAX_COMMAND_FILE_BYTES = 1_000_000


@dataclass
class CommandDefinition:
    """One user-defined slash command."""

    name: str
    description: str
    body: str
    source: CommandSource
    file_path: str
    argument_hint: str = ""
    # Parsed and reported, **not enforced**. Restricting tools for the duration
    # of one command needs a per-turn tool scope that does not exist yet -- the
    # only filtering today (``_filter_tool_cards``) is scoped to an agent
    # definition. Surfaced so a client can show it; treating it as a guarantee
    # would be a false one.
    allowed_tools: list[str] | None = None
    # Set when a higher-precedence source defines the same name.
    shadowed_by: CommandSource | None = None
    # Set when the name collides with a built-in, which always wins.
    reserved: bool = False


_POSITIONAL_HINT = re.compile(r"\$[1-9](?!\d)(?!\.\d)")


def command_accepts_args(command: CommandDefinition) -> bool:
    """Whether the UI should treat this command as taking arguments."""
    if command.argument_hint:
        return True
    if "$ARGUMENTS" in command.body:
        return True
    return bool(_POSITIONAL_HINT.search(command.body))


def command_for_wire(command: CommandDefinition) -> dict:
    """Serialize for ``commands.list`` without shipping the full prompt body."""
    data = asdict(command)
    data["accepts_args"] = command_accepts_args(command)
    data.pop("body", None)
    return data


def _first(frontmatter: dict, *keys: str, default=None):
    """Read the first present key.

    Frontmatter is hand-written, so accept both ``argument-hint`` and
    ``argument_hint``. Rejecting a file over a hyphen would be a bad trade.
    """
    for key in keys:
        if key in frontmatter and frontmatter[key] is not None:
            return frontmatter[key]
    return default


def _coerce_tools(value) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, (list, tuple)):
        return [str(t).strip() for t in value if str(t).strip()]
    return None


def _is_invocable_name(name: str) -> bool:
    """Whether the slash parser can ever match this name.

    ``parseSlashCommand`` (the TUI) splits raw input on whitespace before
    comparing it to a command's name, so a stem like ``foo bar`` would be
    listed as ``/foo bar`` and then never match anything a user can actually
    type. Treated the same as a reserved name: still returned, never active.
    """
    return bool(name) and not any(ch.isspace() for ch in name)


def parse_command_file(file_path: Path, source: CommandSource) -> CommandDefinition | None:
    """Parse one file, or return None when it is not a command definition.

    Returning None rather than raising keeps one malformed file from hiding
    every other command in the directory -- the failure mode that matters most
    for user-authored content.
    """
    content = file_path.read_text(encoding="utf-8")

    frontmatter: dict = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            loaded = yaml.safe_load(parts[1])
            if isinstance(loaded, dict):
                frontmatter = loaded
            body = parts[2]

    body = body.strip()
    if not body:
        # A command with no prompt has nothing to send.
        return None
    if len(body) > _MAX_BODY_CHARS:
        logger.warning(
            "Command file %s body is %d chars, truncating to %d",
            file_path, len(body), _MAX_BODY_CHARS,
        )
        body = body[:_MAX_BODY_CHARS]

    name = file_path.stem.strip().lower()
    if not name:
        return None

    return CommandDefinition(
        name=name,
        description=str(_first(frontmatter, "description", default="") or "").strip(),
        body=body,
        source=source,
        file_path=str(file_path),
        argument_hint=str(
            _first(frontmatter, "argument-hint", "argument_hint", default="") or ""
        ).strip(),
        allowed_tools=_coerce_tools(_first(frontmatter, "allowed-tools", "allowed_tools")),
        reserved=name in RESERVED_NAMES or not _is_invocable_name(name),
    )


class CommandConfigService:
    """Discovers user-defined commands across the three scopes.

    ``builtin_names`` is the set of command names the calling client already
    owns. The server cannot know them -- built-ins are defined in the TUI and
    in the Web UI, not here -- so the client declares them and the server
    enforces the result for both listing and expansion. Without it, a file
    named ``model.md`` would be reported active and would be expandable, while
    the TUI quietly never ran it: two answers to one question.
    """

    def __init__(
        self,
        workspace_dir: Path | str | None = None,
        builtin_names: set[str] | frozenset[str] | None = None,
    ):
        self._workspace_dir = Path(workspace_dir) if workspace_dir else Path.cwd()
        self._builtin_names = frozenset(
            n.strip().lower() for n in (builtin_names or ()) if n and n.strip()
        )

    @staticmethod
    def _user_dir() -> Path:
        return get_user_workspace_dir() / "commands"

    def _project_dir(self) -> Path:
        return self._workspace_dir / ".jiuwenswarm" / "commands"

    def _local_dir(self) -> Path:
        return self._workspace_dir / ".jiuwenswarm" / "commands-local"

    @staticmethod
    def _load_from_dir(dir_path: Path, source: CommandSource) -> list[CommandDefinition]:
        if not dir_path.exists():
            return []
        root = dir_path.resolve()
        found: list[CommandDefinition] = []
        for md_file in sorted(dir_path.glob("*.md")):
            try:
                _confine_command_file(md_file, root)
            except ValueError as exc:
                logger.warning("Skipping command file %s: %s", md_file, exc)
                continue
            try:
                command = parse_command_file(md_file, source)
                if command is not None:
                    found.append(command)
            except Exception:
                logger.warning("Failed to parse command file: %s", md_file, exc_info=True)
        return found

    def list_commands(self) -> list[CommandDefinition]:
        """All discovered commands, with losers marked rather than dropped.

        A shadowed or reserved command is still returned, flagged. Silently
        losing a file the user wrote is how "my command does nothing" becomes an
        unanswerable support question; the UI can show why instead.
        """
        # Load order sets precedence: later wins, so project > user > local.
        ordered: list[tuple[list[CommandDefinition], CommandSource]] = [
            (self._load_from_dir(self._local_dir(), "local"), "local"),
            (self._load_from_dir(self._user_dir(), "user"), "user"),
            (self._load_from_dir(self._project_dir(), "project"), "project"),
        ]

        grouped: dict[str, list[CommandDefinition]] = {}
        for commands, _ in ordered:
            for command in commands:
                grouped.setdefault(command.name, []).append(command)

        result: list[CommandDefinition] = []
        for name, group in sorted(grouped.items()):
            # A client-declared built-in reserves the name for every definition
            # of it, winner and losers alike: the reason none of them run is
            # the built-in, not the shadowing.
            if name in self._builtin_names:
                for command in group:
                    command.reserved = True
            winner = group[-1]
            for loser in group[:-1]:
                loser.shadowed_by = winner.source
                result.append(loser)
            result.append(winner)
        return result

    def active_commands(self) -> list[CommandDefinition]:
        """Only the commands that will actually run."""
        return [
            c for c in self.list_commands()
            if c.shadowed_by is None and not c.reserved
        ]

    # ---- @file resolution --------------------------------------------------

    def file_resolver(self):
        """Resolver for ``@path`` embedding, bounded to the workspace.

        The boundary is enforced here rather than in the expansion module
        because a ``@path`` can come from a user-supplied argument -- a command
        body containing ``@$1`` embeds whatever file the caller names. That is
        the intended power, and it is exactly why the check must be real:
        ``../../.ssh/id_rsa`` has to fail.

        Resolution walks path components without following symlinks. Hardlinks
        to files outside the workspace remain a known limitation on Unix.
        """
        root = _validate_workspace_root(self._workspace_dir)

        def _resolve(ref: str) -> str:
            return _read_file_under_root(root, ref)

        return _resolve


def _confine_command_file(md_file: Path, root: Path) -> None:
    """Refuse a command file the same way ``_read_file_under_root`` refuses an
    ``@file`` reference that escapes the workspace.

    A project ``.md`` file's *contents* become a command's body, and
    ``commands.expand`` returns that body to the client, so a symlink such as
    ``.jiuwenswarm/commands/leak.md -> ~/.ssh/id_rsa`` must not be ingested any
    more than an ``@file`` reference to it would be allowed to resolve. The
    size gate runs before any read, and before ``parse_command_file`` opens
    the file: a link to a device like ``/dev/zero`` must not be able to hang
    the eager ``commands.list`` scan.
    """
    if md_file.is_symlink():
        raise ValueError("symlinks are not allowed in the commands directory")
    resolved = md_file.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("resolves outside the commands directory")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise ValueError(f"cannot stat command file: {exc}") from exc
    if size > _MAX_COMMAND_FILE_BYTES:
        raise ValueError(f"command file too large ({size} bytes)")


def _validate_workspace_root(workspace_dir: Path | str) -> Path:
    """Reject roots that cannot meaningfully bound ``@file`` reads."""
    root = Path(workspace_dir).resolve()
    if root == Path(root.anchor):
        raise ValueError("invalid workspace root")
    if not root.is_dir():
        raise ValueError("invalid workspace root")
    return root


def _read_file_under_root(root: Path, ref: str) -> str:
    """Read one file under ``root``, rejecting escapes and symlink hops."""
    ref_path = Path(ref)
    if ref_path.is_absolute() or ".." in ref_path.parts:
        raise ValueError("outside the workspace")

    current = root
    for part in ref_path.parts:
        if part in (".", ""):
            continue
        next_path = current / part
        if next_path.is_symlink():
            raise ValueError("symlinks not allowed")
        if not next_path.exists():
            raise ValueError("not found")
        current = next_path

    if not current.is_file():
        raise ValueError("not found")

    resolved = current.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("outside the workspace")
    return resolved.read_text(encoding="utf-8", errors="replace")
