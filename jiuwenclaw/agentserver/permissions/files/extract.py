# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Extract path-like tokens from shell commands (shlex + redirects)."""

from __future__ import annotations

import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.permissions.shell_ast import parse_shell_for_permission

logger = logging.getLogger(__name__)

# Split ``cd .. && dir`` / ``a || b`` into per-invocation segments for path extraction (L1).
_CHAIN_SPLIT_RE = re.compile(r"\s+(?:&&|\|\|)\s+")

_PATH_AWARE_COMMANDS = frozenset({
    "cd", "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown", "cat",
    "ls", "dir", "type", "del", "rd", "copy", "move", "md",
    "head", "tail", "more", "less", "vim", "nano", "gedit", "notepad",
})

_INTERPRETER_BASENAMES = frozenset({
    "python", "python3", "pythonw", "py",
    "node", "nodejs", "bash", "sh", "dash", "zsh", "fish",
    "pwsh", "powershell",
})


# cmd.exe / dir：``/A:D``（仅目录）、``/W``、``/OG:N`` 等；不是 Unix 路径。
_NT_CMD_SWITCH_BODY = re.compile(r"^[A-Za-z]{1,2}(?::[^\s/\\]+)?$")


def _nt_cmd_exe_switch_token(stripped: str) -> bool:
    """True if ``stripped`` looks like cmd.exe ``/switch`` or ``/a:d``, not a path."""
    if os.name != "nt" or not stripped.startswith("/") or stripped.startswith("//"):
        return False
    if "\\" in stripped:
        return False
    if stripped.count("/") != 1:
        return False
    body = stripped[1:]
    return bool(_NT_CMD_SWITCH_BODY.match(body))


def _looks_like_path(token: str) -> bool:
    t = token.strip().strip('"').strip("'")
    if _nt_cmd_exe_switch_token(t):
        return False
    if t in (".", ".."):
        return True
    if t.startswith(("\\\\", "./", "../")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", t):
        return True
    return "\\" in t or "/" in t


def _is_shell_flag_token(tok: str) -> bool:
    """Skip ``-f`` / cmd.exe ``/d`` / ``dir /a:d`` style switches when scanning for path operands."""
    s = tok.strip().strip('"').strip("'")
    if not s:
        return True
    if s.startswith("-"):
        return True
    if _nt_cmd_exe_switch_token(s):
        return True
    return False


def _segments_for_extract(command: str) -> list[str]:
    """Split compound commands into simple invocations (``&&`` / ``||``).

    Prefer ``parse_shell_for_permission`` when it yields multiple command nodes; otherwise
    fall back to regex splitting so ``cd .. && dir`` is still analyzed under tree-sitter
    fallback / ``parse_unavailable``.
    """
    text = (command or "").strip()
    if not text:
        return []
    pr = parse_shell_for_permission(text)
    inv = [x.strip() for x in (pr.all_invocations or ()) if x.strip()]
    heuristic = [p.strip() for p in _CHAIN_SPLIT_RE.split(text) if p.strip()]
    if len(inv) >= 2:
        return inv
    if len(heuristic) >= 2:
        return heuristic
    if len(inv) == 1:
        return inv
    return [text]


def _basename_lower(cmd: str) -> str:
    base = Path(cmd.replace("\\", "/")).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


_READ_CMDS = frozenset({
    "cat", "ls", "dir", "type", "head", "tail", "more", "less",
})
_WRITE_CMDS = frozenset({
    "rm", "mkdir", "touch", "chmod", "chown", "del", "rd", "md",
})
_TRANSFER_CMDS = frozenset({"cp", "copy", "mv", "move"})


def extract_paths_from_command(command: str, workdir: str | Path) -> list[Path]:
    """Backward-compatible: path-only list from path-aware commands."""
    return [p for p, _ in extract_path_aware_command_accesses(command, workdir)]


def _path_aware_one_segment(segment: str, cwd: Path) -> tuple[list[tuple[Path, str]], Path | None]:
    """Extract accesses for one shell invocation; optionally return new cwd after ``cd``."""
    try:
        tokens = shlex.split(segment.strip(), posix=False)
    except ValueError:
        tokens = segment.strip().split()
    if not tokens:
        return [], None
    cmd0 = _basename_lower(tokens[0])
    logger.debug(
        "[file_guard.extract] segment=%s cmd0=%s path_aware=%s",
        segment[:120],
        cmd0,
        cmd0 in _PATH_AWARE_COMMANDS,
    )
    if cmd0 not in _PATH_AWARE_COMMANDS:
        return [], None
    base = cwd.resolve()
    path_tokens: list[tuple[Path, int]] = []
    for idx, tok in enumerate(tokens[1:]):
        tok = tok.strip().strip('"').strip("'")
        if not tok or _is_shell_flag_token(tok):
            continue
        if not _looks_like_path(tok):
            continue
        p = Path(tok)
        if not p.is_absolute():
            p = base / tok
        try:
            path_tokens.append((p.resolve(), idx))
        except (OSError, RuntimeError):
            continue

    results: list[tuple[Path, str]] = []
    new_cwd: Path | None = None

    if cmd0 in _TRANSFER_CMDS and len(path_tokens) >= 2:
        results.append((path_tokens[0][0], "read"))
        for p, _ in path_tokens[1:]:
            results.append((p, "write"))
    elif cmd0 in _WRITE_CMDS:
        for p, _ in path_tokens:
            results.append((p, "write"))
    elif cmd0 in _READ_CMDS:
        if not path_tokens and cmd0 in ("dir", "ls"):
            try:
                results.append((base.resolve(), "read"))
            except (OSError, RuntimeError):
                pass
        else:
            for p, _ in path_tokens:
                results.append((p, "read"))
    elif cmd0 == "cd":
        if path_tokens:
            for p, _ in path_tokens:
                results.append((p, "read"))
            new_cwd = path_tokens[-1][0]
        else:
            try:
                home = Path.home().resolve()
                results.append((home, "read"))
                new_cwd = home
            except (OSError, RuntimeError):
                pass
    else:
        # Default: treat as write (e.g. vim path — user may write; conservative)
        for p, _ in path_tokens:
            results.append((p, "write"))
    logger.debug("[file_guard.extract] path_aware_accesses=%s new_cwd=%s", results, new_cwd)
    return results, new_cwd


def extract_path_aware_command_accesses(
        command: str,
        workdir: str | Path,
) -> list[tuple[Path, str]]:
    """Extract (path, read|write) from path-aware shell commands.

    Compound commands (``&&`` / ``||``) are split; ``cd`` updates the effective cwd for
    later segments so ``cd .. && dir`` lists the parent directory.
    """
    if not command or not isinstance(command, str):
        return []
    cwd = Path(workdir).resolve()
    combined: list[tuple[Path, str]] = []
    for seg in _segments_for_extract(command):
        part, new_cwd = _path_aware_one_segment(seg, cwd)
        combined.extend(part)
        if new_cwd is not None:
            cwd = new_cwd
    return combined


def extract_shell_path_accesses(
        command: str,
        workdir: str | Path,
) -> list[tuple[Path, str]]:
    """Return list of (resolved_path, action) for shell inspection.

    action is ``read`` or ``write`` (redirect targets are writes; ``<`` is read).
    """
    if not command or not isinstance(command, str):
        return []
    base = Path(workdir).resolve()
    results: list[tuple[Path, str]] = []

    def _resolve(tok: str) -> Path | None:
        tok = tok.strip().strip('"').strip("'")
        if not tok or not _looks_like_path(tok):
            return None
        p = Path(tok)
        if not p.is_absolute():
            p = base / tok
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return None

    for p, act in extract_path_aware_command_accesses(command, workdir):
        results.append((p, act))

    # Redirects: crude classify by operator before token
    for m in re.finditer(r"(?:^|[\s;|&])(\d*>>?|\d*<|&>)\s*([^\s;|&<>]+)", command):
        op, target = m.group(1), m.group(2)
        rp = _resolve(target)
        if rp is None:
            continue
        if "<" in op and ">" not in op:
            results.append((rp, "read"))
        else:
            results.append((rp, "write"))

    # Interpreter + script: exec on script path
    try:
        tokens = shlex.split(command.strip(), posix=False)
    except ValueError:
        tokens = command.strip().split()
    if len(tokens) >= 2:
        cmd0 = _basename_lower(tokens[0])
        if cmd0 in _INTERPRETER_BASENAMES:
            script_tok = tokens[1].strip('"').strip("'")
            if script_tok and not script_tok.startswith("-"):
                rp = _resolve(script_tok)
                if rp is not None:
                    results.append((rp, "exec"))

    return results


def iter_config_tool_bindings(file_guard_cfg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return tool_bindings map from config ``permissions.file_guard.tool_bindings``."""
    raw = (file_guard_cfg or {}).get("tool_bindings") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if isinstance(spec, dict):
            out[name.strip()] = [spec]
        elif isinstance(spec, list):
            out[name.strip()] = [x for x in spec if isinstance(x, dict)]
    return out
