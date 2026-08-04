# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice Python port — command parsing.

Port of src/core/command-shell.ts, command-match.ts, command-identity.ts,
and execution-input.ts.
"""

from __future__ import annotations

import os
import re

from .types import CommandMatchCandidate, ToolExecutionInput


# ---------------------------------------------------------------------------
# Shell tokenizer
# ---------------------------------------------------------------------------


def tokenize_command(command: str) -> list[str]:
    """Single-pass quote-aware shell tokenizer.

    Backslash outside quotes escapes the next character.
    Inside quotes, backslash is NOT special — only the matching close-quote ends the region.
    Quote characters themselves are stripped from tokens.
    """
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaping = False

    for char in command.strip():
        if escaping:
            current.append(char)
            escaping = False
            continue

        if char == "\\":
            escaping = True
            continue

        if quote is not None:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue

        if char in ("'", '"'):
            quote = char
            continue

        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue

        current.append(char)

    if escaping:
        current.append("\\")

    if current:
        tokens.append("".join(current))

    return tokens


# ---------------------------------------------------------------------------
# Command chain splitting
# ---------------------------------------------------------------------------


def split_command_chain(command: str) -> list[str]:
    """Split on unquoted &&, ;, and newlines."""
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    text = command

    while i < len(text):
        char = text[i]

        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            i += 1
            continue

        if char in ("'", '"'):
            quote = char
            current.append(char)
            i += 1
            continue

        if char == "\\":
            current.append(char)
            if i + 1 < len(text):
                current.append(text[i + 1])
            i += 2
            continue

        # Check for &&
        if char == "&" and i + 1 < len(text) and text[i + 1] == "&":
            segments.append("".join(current).strip())
            current = []
            i += 2
            continue

        # Check for ; or \n
        if char in (";", "\n"):
            segments.append("".join(current).strip())
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    tail = "".join(current).strip()
    if tail:
        segments.append(tail)

    return [s for s in segments if s]


def is_compound_command(command: str) -> bool:
    """Check if command contains unquoted ;, newline, |, &&, or ||."""
    quote: str | None = None
    i = 0
    text = command

    while i < len(text):
        char = text[i]

        if quote is not None:
            if char == quote:
                quote = None
            i += 1
            continue

        if char in ("'", '"'):
            quote = char
            i += 1
            continue

        if char == "\\":
            i += 2
            continue

        if char in (";", "\n", "|"):
            return True

        if char == "&" and i + 1 < len(text) and text[i + 1] == "&":
            return True

        i += 1

    return False


# ---------------------------------------------------------------------------
# Environment / cd prefix stripping
# ---------------------------------------------------------------------------

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def strip_env_assignments(argv: list[str]) -> list[str] | None:
    """Strip leading KEY=VALUE assignments from argv. Returns remaining argv or None."""
    i = 0
    while i < len(argv) and _ENV_ASSIGN_RE.match(argv[i]):
        i += 1
    return argv[i:] if i < len(argv) else None


def strip_cd_prefix(command: str, max_rounds: int = 8) -> str:
    """Iteratively strip 'cd <dir> &&' or 'pushd <dir> &&' prefixes."""
    result = command.strip()
    for _ in range(max_rounds):
        tokens = tokenize_command(result)
        if len(tokens) < 2:
            break
        if tokens[0] not in ("cd", "pushd"):
            break
        # Find the && after cd <arg>
        parts = split_command_chain(result)
        if len(parts) < 2:
            break
        first_tokens = tokenize_command(parts[0])
        if len(first_tokens) != 2:
            break
        # Check no redirections
        if any(c in parts[0] for c in "<>|;&"):
            break
        result = " && ".join(parts[1:]).strip() if len(parts) > 1 else result
    return result


# ---------------------------------------------------------------------------
# Shell runner unwrap
# ---------------------------------------------------------------------------

_SHELL_NAMES = frozenset({
    "bash", "sh", "zsh", "fish", "dash", "ksh", "csh", "tcsh",
})

_C_FLAG_RE = re.compile(r"-[A-Za-z]*c[A-Za-z]*")


def unwrap_shell_runner(input_: ToolExecutionInput) -> str | None:
    """Extract command body from 'bash -c \"body\"' style invocations."""
    argv = input_.argv or (tokenize_command(input_.command) if input_.command else [])
    if not argv:
        return None

    cmd_name = os.path.basename(argv[0])
    if cmd_name not in _SHELL_NAMES:
        return None

    for i in range(1, len(argv) - 1):
        if _C_FLAG_RE.match(argv[i]):
            return argv[i + 1]

    return None


# ---------------------------------------------------------------------------
# Effective command resolution
# ---------------------------------------------------------------------------

_SETUP_SEGMENTS = frozenset({
    "cd", "pwd", "set", "source", ".", "export", "unset", "trap", "true",
})


def _is_setup_segment(segment: str) -> bool:
    tokens = tokenize_command(segment)
    if not tokens:
        return True
    name = os.path.basename(tokens[0])
    return name in _SETUP_SEGMENTS


def resolve_effective_command(input_: ToolExecutionInput) -> str | None:
    """Strip setup wrappers (env vars, cd, export, etc.) to find the real command."""
    command = input_.command
    if not command:
        return None

    command = strip_cd_prefix(command)

    for _ in range(16):
        parts = split_command_chain(command)
        if not parts:
            return None

        # Skip setup segments from the front
        first_real = None
        for part in parts:
            if not _is_setup_segment(part):
                first_real = part
                break

        if first_real is None:
            return None

        # Strip env assignments from the first real segment
        tokens = tokenize_command(first_real)
        remaining = strip_env_assignments(tokens)
        if remaining is None:
            return None

        if remaining == tokens:
            # No env assignments stripped — we're done
            return first_real

        # Rebuild and try again
        command = " ".join(remaining)
        if len(parts) > 1:
            idx = parts.index(first_real)
            command = " && ".join([" ".join(remaining)] + parts[idx + 1:])

    return command


# ---------------------------------------------------------------------------
# Command match candidates
# ---------------------------------------------------------------------------


def derive_candidates(input_: ToolExecutionInput) -> list[CommandMatchCandidate]:
    """Generate up to 3 match candidates: original, shell-body, effective."""
    candidates: list[CommandMatchCandidate] = []

    # Original
    argv = input_.argv or (tokenize_command(input_.command) if input_.command else [])
    candidates.append(CommandMatchCandidate(
        argv=list(argv),
        source="original",
        command=input_.command,
    ))

    # Shell-body
    shell_body = unwrap_shell_runner(input_)
    if shell_body:
        shell_argv = tokenize_command(shell_body)
        candidates.append(CommandMatchCandidate(
            argv=shell_argv,
            source="shell-body",
            command=shell_body,
        ))

    # Effective
    effective = resolve_effective_command(input_)
    if effective and effective != input_.command:
        eff_argv = tokenize_command(effective)
        candidates.append(CommandMatchCandidate(
            argv=eff_argv,
            source="effective",
            command=effective,
        ))

    # Deduplicate
    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: list[CommandMatchCandidate]) -> list[CommandMatchCandidate]:
    seen: dict[str, CommandMatchCandidate] = {}
    priority_map = {"original": 0, "shell-body": 1, "effective": 2}

    for c in candidates:
        key = (c.command or "") + "\0" + "\0".join(c.argv)
        if key not in seen or priority_map.get(c.source, 0) > priority_map.get(seen[key].source, 0):
            seen[key] = c

    return list(seen.values())


# ---------------------------------------------------------------------------
# Command identity
# ---------------------------------------------------------------------------


def get_command_name(argv: list[str]) -> str | None:
    if not argv:
        return None
    first = argv[0]
    stripped = first.strip("'\"")
    return os.path.basename(stripped)


_GIT_GLOBAL_VALUE_OPTS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix", "--config-env",
})

_GIT_GLOBAL_INLINE_OPTS = (
    "--git-dir=", "--work-tree=", "--exec-path=", "--super-prefix=", "--config-env=",
)


def get_git_subcommand(argv: list[str]) -> str | None:
    """Extract the git subcommand, skipping global options."""
    if not argv or get_command_name(argv) != "git":
        return None

    i = 1
    while i < len(argv):
        arg = argv[i]

        # Global options that take a value
        if arg in _GIT_GLOBAL_VALUE_OPTS:
            i += 2
            continue

        # Inline-value globals
        if any(arg.startswith(opt) for opt in _GIT_GLOBAL_INLINE_OPTS):
            i += 1
            continue

        # Other flags
        if arg.startswith("-"):
            i += 1
            continue

        return arg

    return None


_FILE_INSPECTION_COMMANDS = frozenset({
    "cat", "sed", "head", "tail", "nl", "bat", "batcat", "jq", "yq",
})


def is_file_inspection_command(input_: ToolExecutionInput) -> bool:
    """Check if the command is a file content inspection tool."""
    argv = input_.argv or (tokenize_command(input_.command) if input_.command else [])
    name = get_command_name(argv)
    if name in _FILE_INSPECTION_COMMANDS:
        return True
    # git show with blob specifier
    if name == "git" and get_git_subcommand(argv) == "show":
        for arg in argv[2:]:
            if not arg.startswith("-") and re.match(r"^[^:]+:.+", arg):
                return True
    return False


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------


def normalize_execution_input(input_: ToolExecutionInput) -> ToolExecutionInput:
    """Fill in argv from command if missing."""
    if input_.argv is None and input_.command:
        input_.argv = tokenize_command(input_.command)
    return input_
