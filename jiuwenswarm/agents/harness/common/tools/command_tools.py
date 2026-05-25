# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Command execution tools implemented with openjiuwen @tool style."""

from __future__ import annotations

import asyncio
import json
import locale
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from openjiuwen.core.foundation.tool import tool

from jiuwenswarm.common.utils import get_agent_workspace_dir


_DANGEROUS_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "blocked pattern: rm -rf"),
    (re.compile(r"\bdel\s+/[a-z]*[fsq][a-z]*\b", re.IGNORECASE), "blocked pattern: del /f /s /q"),
    (re.compile(r"\brd\s+/s\s+/q\b", re.IGNORECASE), "blocked pattern: rd /s /q"),
    (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), "blocked pattern: format drive"),
    (re.compile(r"\bshutdown\b", re.IGNORECASE), "blocked pattern: shutdown"),
    (re.compile(r"\breboot\b", re.IGNORECASE), "blocked pattern: reboot"),
    (re.compile(r"\bdiskpart\b", re.IGNORECASE), "blocked pattern: diskpart"),
    (re.compile(r"\bmkfs\b", re.IGNORECASE), "blocked pattern: mkfs"),
    (re.compile(r"\breg\s+delete\b", re.IGNORECASE), "blocked pattern: reg delete"),
    (
        re.compile(r"\bremove-item\b[^\n\r]*-recurse[^\n\r]*-force", re.IGNORECASE),
        "blocked pattern: Remove-Item -Recurse -Force",
    ),
]

_POWERSHELL_TOKENS = (
    "powershell ",
    "powershell.exe ",
    "pwsh ",
    "pwsh.exe ",
    "get-childitem",
    "set-location",
    "remove-item",
    "test-path",
    "join-path",
    "select-object",
    "where-object",
    "foreach-object",
    "invoke-webrequest",
    "invoke-restmethod",
    "out-file",
    "start-process",
    "$env:",
    "$psversiontable",
    "$null",
    "$true",
    "$false",
)

_VALID_SHELL_TYPES = {"auto", "cmd", "powershell", "bash", "sh"}
_POWERSHELL_EXECUTABLE_PATTERN = re.compile(r"^\s*(?:powershell(?:\.exe)?|pwsh(?:\.exe)?)\b", re.IGNORECASE)
_POWERSHELL_COMMAND_ARG_PATTERN = re.compile(r"(?is)(?:^|\s)-(?:command|c)\s+(?P<script>.+)\s*$")
_POSIX_COMMANDS = frozenset({
    "ls", "grep", "egrep", "fgrep", "cat", "head", "tail", "find", "rm",
    "cp", "mv", "touch", "chmod", "chown", "sed", "awk", "gawk", "cut",
    "sort", "uniq", "wc", "du", "df", "pwd", "which", "mkdir",
})
_QUOTED_WINDOWS_PATH_PATTERN = re.compile(r"(?P<quote>['\"])(?P<path>[A-Za-z]:\\[^'\"]+)(?P=quote)")
_UNQUOTED_WINDOWS_PATH_PATTERN = re.compile(r"(?<![\w/])(?P<path>[A-Za-z]:\\[^\s|&;]+)")


def _clip_text(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n...[truncated]"


def _check_command_safety(command: str) -> str | None:
    for pattern, message in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return message
    return None


def _context_cwd() -> Path:
    try:
        from openjiuwen.core.sys_operation.cwd import get_cwd

        return Path(get_cwd()).resolve()
    except Exception:
        return get_agent_workspace_dir().resolve()


def _context_project_root() -> Path:
    try:
        from openjiuwen.core.sys_operation.cwd import get_project_root

        return Path(get_project_root()).resolve()
    except Exception:
        return _context_cwd()


def _context_workspace_root() -> Path | None:
    try:
        from openjiuwen.core.sys_operation.cwd import get_workspace

        workspace = get_workspace()
        return Path(workspace).resolve() if workspace else None
    except Exception:
        return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_command_workdir(workdir: str) -> Path:
    current_cwd = _context_cwd()
    project_root = _context_project_root()
    candidate = Path(workdir) if workdir else current_cwd
    if not candidate.is_absolute():
        candidate = current_cwd / candidate
    candidate = candidate.resolve()

    allowed_roots = [project_root]
    workspace_root = _context_workspace_root()
    if workspace_root is not None:
        allowed_roots.append(workspace_root)
    allowed_roots.append(get_agent_workspace_dir().resolve())

    if not any(_is_relative_to(candidate, root) for root in allowed_roots):
        raise ValueError("workdir is outside project workspace")
    return candidate


def _normalize_shell_type(shell_type: str) -> str:
    value = (shell_type or "auto").strip().lower()
    return value if value in _VALID_SHELL_TYPES else "auto"


def _looks_like_powershell(command: str) -> bool:
    lowered = (command or "").strip().lower()
    if not lowered:
        return False
    if any(token in lowered for token in _POWERSHELL_TOKENS):
        return True
    if "@'" in command or '@"' in command:
        return True
    if re.search(r"(^|[\s;(])\$[A-Za-z_][A-Za-z0-9_]*", command):
        return True
    return False


def _available_powershell() -> str:
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        system_powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if system_powershell.exists():
            return str(system_powershell)

    for candidate in ("pwsh", "powershell", "powershell.exe"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "powershell"


def _is_wsl_bash_path(path: str) -> bool:
    normalized = os.path.normcase(os.path.normpath(path))
    system_root = os.path.normcase(os.path.normpath(os.environ.get("SystemRoot") or r"C:\Windows"))
    return normalized == os.path.join(system_root, "system32", "bash.exe") or (
        "\\microsoft\\windowsapps\\bash.exe" in normalized
    )


def _git_bash_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("GIT_BASH") or os.environ.get("GIT_BASH_PATH")
    if env_path:
        candidates.append(Path(env_path))

    for root in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData") and str(Path(os.environ["LocalAppData"]) / "Programs"),
    ):
        if root:
            candidates.append(Path(root) / "Git" / "bin" / "bash.exe")

    git_path = shutil.which("git")
    if git_path:
        git_exe = Path(git_path)
        candidates.append(git_exe.parent.parent / "bin" / "bash.exe")

    return candidates


def _available_git_bash() -> str | None:
    if os.name != "nt":
        return None
    for candidate in _git_bash_candidates():
        if candidate.exists():
            return str(candidate)
    return None


def _available_bash(*, allow_wsl: bool = True) -> str | None:
    if os.name == "nt":
        git_bash = _available_git_bash()
        if git_bash:
            return git_bash
    resolved = shutil.which("bash")
    if resolved and (allow_wsl or not _is_wsl_bash_path(resolved)):
        return resolved
    return None


def _available_sh() -> str | None:
    if os.name == "nt":
        git_bash = _available_git_bash()
        if git_bash:
            sh_path = Path(git_bash).parent.parent / "usr" / "bin" / "sh.exe"
            if sh_path.exists():
                return str(sh_path)
    return shutil.which("sh")


def _strip_matching_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _unwrap_powershell_command(command: str) -> str | None:
    if not _POWERSHELL_EXECUTABLE_PATTERN.match(command or ""):
        return None
    remainder = _POWERSHELL_EXECUTABLE_PATTERN.sub("", command, count=1).strip()
    match = _POWERSHELL_COMMAND_ARG_PATTERN.search(remainder)
    if not match:
        return None
    script = _strip_matching_quotes(match.group("script"))
    return script or None


def _split_shell_segments(command: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if char in {'"', "'"}:
            quote = None if quote == char else char if quote is None else quote
        if quote is None and command.startswith(("&&", "||"), index):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2
            continue
        if quote is None and char in {"|", ";", "\n", "\r"}:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def _segment_base_command(segment: str) -> str:
    try:
        tokens = shlex.split(segment, posix=False)
    except ValueError:
        return ""
    if not tokens:
        return ""
    base = _strip_matching_quotes(tokens[0]).rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _looks_like_posix(command: str) -> bool:
    return any(_segment_base_command(segment) in _POSIX_COMMANDS for segment in _split_shell_segments(command or ""))


def _normalize_windows_paths_for_bash(command: str) -> str:
    def replace_path(match: re.Match[str]) -> str:
        value = match.group("path").replace("\\", "/")
        quote = match.groupdict().get("quote")
        return f"{quote}{value}{quote}" if quote else value

    normalized = _QUOTED_WINDOWS_PATH_PATTERN.sub(replace_path, command)
    return _UNQUOTED_WINDOWS_PATH_PATTERN.sub(replace_path, normalized)


def _available_unix_shell(prefer_bash: bool) -> Sequence[str]:
    if prefer_bash:
        bash = shutil.which("bash")
        if bash:
            return [bash, "-lc"]
    sh = shutil.which("sh") or "/bin/sh"
    return [sh, "-lc" if prefer_bash else "-c"]


def _resolve_execution_plan(command: str, shell_type: str) -> tuple[list[str] | str, bool, str]:
    normalized = _normalize_shell_type(shell_type)
    is_windows = os.name == "nt"

    if is_windows:
        if normalized == "auto":
            powershell_command = _unwrap_powershell_command(command)
            if powershell_command is not None:
                exe = _available_powershell()
                return [exe, "-NoProfile", "-NonInteractive", "-Command", powershell_command], False, "powershell"
            if _looks_like_powershell(command):
                exe = _available_powershell()
                return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
            if _looks_like_posix(command):
                exe = _available_bash(allow_wsl=False)
                if exe:
                    return [exe, "-lc", _normalize_windows_paths_for_bash(command)], False, "bash"
            normalized = "cmd"
        if normalized == "powershell":
            exe = _available_powershell()
            command = _unwrap_powershell_command(command) or command
            return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
        if normalized == "cmd":
            return command, True, "cmd"
        if normalized in {"bash", "sh"}:
            exe = _available_bash() if normalized == "bash" else _available_sh()
            if not exe:
                raise RuntimeError(f"Requested shell '{normalized}' is not available on this system.")
            flag = "-lc" if normalized == "bash" else "-c"
            return [exe, flag, _normalize_windows_paths_for_bash(command)], False, normalized
        raise RuntimeError(f"Unsupported shell_type for Windows: {normalized}")

    if normalized == "auto":
        normalized = "bash" if shutil.which("bash") else "sh"
    if normalized == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            raise RuntimeError("Requested shell 'powershell' is not available on this system.")
        return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
    if normalized == "cmd":
        raise RuntimeError("shell_type 'cmd' is only supported on Windows.")
    if normalized == "bash":
        exe, flag = _available_unix_shell(prefer_bash=True)
        return [exe, flag, command], False, "bash"
    if normalized == "sh":
        exe, flag = _available_unix_shell(prefer_bash=False)
        return [exe, flag, command], False, "sh"
    raise RuntimeError(f"Unsupported shell_type: {normalized}")


def _resolve_encoding(resolved_shell: str) -> str:
    """Choose subprocess text encoding based on the resolved shell type.

    - bash / sh on Windows (e.g. Git Bash / MSYS2) output UTF-8 by default.
    - cmd uses the system code page (typically CP936/GBK on Chinese Windows).
    - PowerShell also uses the system code page by default; safest to use
      the system code page and rely on ``errors='replace'`` for edge cases.
    """
    if os.name == "nt" and resolved_shell in ("bash", "sh"):
        return "utf-8"
    return locale.getpreferredencoding(False) or "utf-8"


def _run_command_sync(
    command: str,
    timeout_seconds: int,
    workdir: Path,
    shell_type: str,
) -> tuple[subprocess.CompletedProcess[str], str]:
    plan, use_shell, resolved_shell = _resolve_execution_plan(command, shell_type)
    # bash/sh on Windows output UTF-8; cmd/PowerShell use system code page.
    encoding = _resolve_encoding(resolved_shell)
    popen_kw = {}
    if os.name != "nt":
        _jw_start_new_session = os.getenv("JW_START_NEW_SESSION", "true").strip().lower()
        if _jw_start_new_session not in ("0", "false", "no", "off"):
            popen_kw["start_new_session"] = True
    result = subprocess.run(
        plan,
        shell=use_shell,
        cwd=str(workdir),
        text=True,
        encoding=encoding,
        errors='replace',
        capture_output=True,
        timeout=timeout_seconds,
        **popen_kw,
    )
    return result, resolved_shell


def _run_command_background(
    command: str,
    workdir: Path,
    shell_type: str,
    grace_seconds: float = 5.0,
) -> tuple[int, str, str | None]:
    """Start command in background. Returns (pid, resolved_shell, error_msg).
    error_msg is None on success.
    """
    plan, use_shell, resolved_shell = _resolve_execution_plan(command, shell_type)
    popen_kw = {}
    if os.name != "nt":
        _jw_start_new_session = os.getenv("JW_START_NEW_SESSION", "true").strip().lower()
        if _jw_start_new_session not in ("0", "false", "no", "off"):
            popen_kw["start_new_session"] = True
    proc = subprocess.Popen(
        plan,
        shell=use_shell,
        cwd=str(workdir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        **popen_kw,
    )
    try:
        exit_code = proc.wait(timeout=grace_seconds)
        if exit_code != 0:
            return proc.pid, resolved_shell, f"Process exited with code {exit_code}"
    except subprocess.TimeoutExpired:
        pass  # Still running after grace period -> success
    return proc.pid, resolved_shell, None


@tool(
    name="mcp_exec_command",
    description=(
        "Execute simple cross-platform command-line command in project workspace. "
        "Supports Windows cmd/PowerShell and macOS/Linux bash/sh. "
        "Optional shell_type=auto|cmd|powershell|bash|sh. "
        "Set background=True to run non-blocking (e.g. start a server); "
        "returns immediately on success, error on failure. "
        "Set max_output_chars=0 to disable output clipping. "
        "Use a larger timeout_seconds for long-running commands. "
        "Returns JSON: exit_code/stdout/stderr (blocking) or pid/status (background)."
    ),
)
async def mcp_exec_command(
    command: str,
    timeout_seconds: int = 300,
    workdir: str = ".",
    max_output_chars: int = 0,
    shell_type: str = "auto",
    background: bool = False,
) -> str:
    command = (command or "").strip()
    if not command:
        return "[ERROR]: command cannot be empty."

    blocked_reason = _check_command_safety(command)
    if blocked_reason:
        return f"[ERROR]: command rejected for safety ({blocked_reason})."

    try:
        resolved_workdir = _resolve_command_workdir(workdir)
    except Exception:
        return "[ERROR]: workdir is outside project workspace."

    try:
        timeout_seconds = int(timeout_seconds)
    except (TypeError, ValueError):
        timeout_seconds = 300
    try:
        max_timeout_seconds = int(os.getenv("MCP_EXEC_COMMAND_MAX_TIMEOUT_SECONDS") or "600")
    except ValueError:
        max_timeout_seconds = 3600
    max_timeout_seconds = max(1, max_timeout_seconds)
    timeout_seconds = max(1, min(timeout_seconds, max_timeout_seconds))

    try:
        max_output_chars = int(max_output_chars)
    except (TypeError, ValueError):
        max_output_chars = 0
    if max_output_chars < 0:
        max_output_chars = 0
    normalized_shell_type = _normalize_shell_type(shell_type)

    if background:
        try:
            pid, resolved_shell, err = await asyncio.to_thread(
                _run_command_background,
                command,
                resolved_workdir,
                normalized_shell_type,
            )
        except Exception as exc:
            return f"[ERROR]: command failed to start: {exc}"
        if err:
            return f"[ERROR]: background command failed: {err}"
        payload = {
            "command": command,
            "cwd": str(resolved_workdir),
            "shell_type": normalized_shell_type,
            "resolved_shell": resolved_shell,
            "background": True,
            "pid": pid,
            "status": "started",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    try:
        result, resolved_shell = await asyncio.to_thread(
            _run_command_sync,
            command,
            timeout_seconds,
            resolved_workdir,
            normalized_shell_type,
        )
    except subprocess.TimeoutExpired:
        return f"[ERROR]: command timed out after {timeout_seconds}s."
    except Exception as exc:
        return f"[ERROR]: command execution failed: {exc}"

    payload = {
        "command": command,
        "cwd": str(resolved_workdir),
        "shell_type": normalized_shell_type,
        "resolved_shell": resolved_shell,
        "exit_code": result.returncode,
        "stdout": _clip_text(result.stdout or "", max_output_chars),
        "stderr": _clip_text(result.stderr or "", max_output_chars),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
