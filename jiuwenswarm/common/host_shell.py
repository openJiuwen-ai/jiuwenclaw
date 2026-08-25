# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""白名单 CLI 在宿主编 argv：简单命令直跑 exe，复合命令包本机 Git bash 或 PowerShell。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from jiuwenswarm.common.connectors import (
    command_uses_connector_cli,
    connector_bin_dirs,
    connector_host_argv,
    expand_connector_cli_tokens,
)

_POSIX_WRAP_RE = re.compile(
    r"(?:&&|\|\||/dev/null|(?:^|[\s;|&])(?:which|command\s+-v|type)\s)",
    re.IGNORECASE,
)
_POWERSHELL_HINTS = (
    "get-command",
    "select-object",
    "where-object",
    "foreach-object",
    "-erroraction",
    "$env:",
    "write-output",
)


def powershell_exe() -> str:
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        bundled = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if bundled.is_file():
            return str(bundled)
    for candidate in ("pwsh", "powershell", "powershell.exe"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "powershell"


def _is_wsl_bash_path(path: str) -> bool:
    normalized = os.path.normcase(os.path.normpath(path))
    system_root = os.path.normcase(
        os.path.normpath(os.environ.get("SystemRoot") or r"C:\Windows")
    )
    return normalized == os.path.join(system_root, "system32", "bash.exe") or (
        "\\microsoft\\windowsapps\\bash.exe" in normalized
    )


def host_bash_exe() -> str | None:
    """本机 Git bash，排除 WSL ``bash.exe``。"""
    if os.name != "nt":
        return "/bin/bash" if os.path.isfile("/bin/bash") else shutil.which("bash")
    for key in ("GIT_BASH", "GIT_BASH_PATH", "JIUWENBOX_BASH_PATH"):
        raw = (os.environ.get(key) or "").strip().strip('"')
        if raw:
            path = Path(raw)
            if path.is_file() and not _is_wsl_bash_path(str(path)):
                return str(path)
    candidates: list[Path] = []
    for root in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData") and str(Path(os.environ["LocalAppData"]) / "Programs"),
    ):
        if root:
            git_root = Path(root) / "Git"
            candidates.append(git_root / "bin" / "bash.exe")
            candidates.append(git_root / "usr" / "bin" / "bash.exe")
    git_path = shutil.which("git")
    if git_path:
        git_exe = Path(git_path)
        for parent in git_exe.parents:
            if parent.name.lower() == "git":
                candidates.append(parent / "bin" / "bash.exe")
                candidates.append(parent / "usr" / "bin" / "bash.exe")
                break
    for candidate in candidates:
        if candidate.is_file() and not _is_wsl_bash_path(str(candidate)):
            return str(candidate)
    resolved = shutil.which("bash")
    if resolved and not _is_wsl_bash_path(resolved):
        return resolved
    return None


def looks_like_powershell_script(command: str) -> bool:
    lowered = (command or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in _POWERSHELL_HINTS)


def looks_like_posix_script(command: str) -> bool:
    stripped = str(command or "").strip()
    if not stripped:
        return False
    return _POSIX_WRAP_RE.search(stripped) is not None


def connector_wrap_posix(command: str, shell_type: str | None = None) -> bool:
    if looks_like_powershell_script(command):
        return False
    if looks_like_posix_script(command):
        return True
    kind = str(shell_type or "").strip().lower()
    return kind in {"bash", "sh"}


def host_environ(*, connectors_dir: Path | None = None) -> dict[str, str]:
    env = {str(key): str(value) for key, value in os.environ.items() if value is not None}
    extras = connector_bin_dirs(connectors_dir=connectors_dir)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env["MSYS2_PATH_TYPE"] = "inherit"
    env["MSYS_PATH_TYPE"] = "inherit"
    if not extras:
        return env
    current = env.get("PATH") or env.get("Path") or ""
    env["PATH"] = os.pathsep.join([*extras, current] if current else extras)
    return env


def host_shell_wrap(command: str, *, posix: bool = False) -> list[str]:
    if os.name == "nt":
        if posix:
            bash = host_bash_exe()
            if bash:
                return [bash, "-c", command]
        return [powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/bash", "-lc", command]


def host_shell_argv(
    command: str,
    *,
    shell_type: str | None = None,
    connectors_dir: Path | None = None,
) -> list[str] | None:
    """连接器命令返回宿主 argv；非连接器且非 powershell/cmd 返回 None。"""
    stripped = str(command or "").strip()
    if not stripped:
        return None
    connector = connector_host_argv(stripped, connectors_dir=connectors_dir)
    if connector:
        return connector
    if command_uses_connector_cli(stripped, connectors_dir=connectors_dir):
        posix = connector_wrap_posix(stripped, shell_type)
        if posix:
            return host_shell_wrap(stripped, posix=True)
        expanded = expand_connector_cli_tokens(stripped, connectors_dir=connectors_dir)
        return host_shell_wrap(expanded, posix=False)
    kind = str(shell_type or "").strip().lower()
    if kind == "powershell":
        return [powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", stripped]
    if kind == "cmd":
        comspec = os.environ.get("ComSpec") or "cmd.exe"
        return [comspec, "/c", stripped]
    return None


def decode_cli_bytes(data: bytes | bytearray | str | None) -> str:
    """管道输出按 UTF-8 解码。"""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    blob = bytes(data)
    if not blob:
        return ""
    if blob.startswith(b"\xef\xbb\xbf"):
        blob = blob[3:]
    return blob.decode("utf-8", errors="replace")


EMPTY_HOST_SUCCESS = (
    "[connector] exit 0 with empty output. "
    "Command already completed; do not retry create/update/delete."
)


def format_host_cmd_output(stdout: str, stderr: str, exit_code: int) -> tuple[str, str]:
    """stdout 优先；空成功补一句避免模型当失败重试。"""
    out = stdout or ""
    err = stderr or ""
    if not out.strip() and err.strip():
        out, err = err, ""
    if not out.strip() and int(exit_code or 0) == 0:
        out = EMPTY_HOST_SUCCESS
    return out, err


def run_host_subprocess(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> dict[str, str | int | bool]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout if (timeout and timeout > 0) else None,
            check=False,
        )
        stdout, stderr = format_host_cmd_output(
            decode_cli_bytes(completed.stdout),
            decode_cli_bytes(completed.stderr),
            int(completed.returncode),
        )
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": int(completed.returncode),
            "local": True,
        }
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = format_host_cmd_output(
            decode_cli_bytes(exc.stdout),
            decode_cli_bytes(exc.stderr) + f"\n[local timeout after {timeout}s]",
            124,
        )
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": 124,
            "local": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "stdout": "",
            "stderr": f"local subprocess error: {exc}",
            "exit_code": 1,
            "local": True,
        }
