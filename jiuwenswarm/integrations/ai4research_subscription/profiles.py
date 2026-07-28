"""Canonical, instance-scoped Codex profile management."""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from jiuwenswarm.common.utils import get_user_workspace_dir

from .errors import CodexProviderError, auth_required


_MANAGED_CONFIG = """cli_auth_credentials_store = "file"
forced_login_method = "chatgpt"
project_root_markers = []
check_for_update_on_startup = false

[analytics]
enabled = false
"""


@dataclass(frozen=True)
class CodexProfile:
    root: Path
    runtime_home: Path
    sqlite_home: Path
    turns_dir: Path
    lock_path: Path
    config_path: Path
    quarantine_path: Path


def _assert_private_directory(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise CodexProviderError("unsafe_profile", "The managed Codex profile path is unsafe.")
    if os.name == "posix":
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise CodexProviderError(
                "unsafe_profile",
                "The managed Codex profile must be owned by the service user and private.",
            )


def _ensure_private_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _assert_private_directory(path)
        return
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    _assert_private_directory(path)


def _write_managed_config(path: Path) -> None:
    if path.is_symlink():
        raise CodexProviderError("unsafe_profile", "The managed Codex configuration path is unsafe.")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise CodexProviderError("unsafe_profile", "The managed Codex configuration is not a file.")
        if os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise CodexProviderError("unsafe_profile", "The managed Codex configuration is not private.")
        if path.read_text(encoding="utf-8") == _MANAGED_CONFIG:
            return

    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(_MANAGED_CONFIG)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def ensure_codex_profile() -> CodexProfile:
    """Create or verify the provider-owned profile for the active Jiuwen instance."""

    if os.name == "nt":
        raise CodexProviderError(
            "unsupported_platform",
            "Codex subscription process isolation is not supported on Windows in this release.",
        )

    workspace = get_user_workspace_dir().expanduser().resolve(strict=False)
    managed_root = workspace / "private" / "subscription-providers" / "codex"
    profile_root = managed_root / "codex-home"
    runtime_home = managed_root / "runtime-home"
    sqlite_home = managed_root / "sqlite"
    turns_dir = managed_root / "turns"

    current = workspace
    if not current.exists():
        raise CodexProviderError("unsafe_profile", "The Jiuwen instance workspace does not exist.")
    for component in (workspace / "private", workspace / "private" / "subscription-providers", managed_root,
                      profile_root, runtime_home, sqlite_home, turns_dir):
        parent = component.parent
        if parent != workspace and not parent.exists():
            raise CodexProviderError("unsafe_profile", "The managed Codex profile parent is unavailable.")
        _ensure_private_directory(component)

    config_path = profile_root / "config.toml"
    _write_managed_config(config_path)
    return CodexProfile(
        root=profile_root,
        runtime_home=runtime_home,
        sqlite_home=sqlite_home,
        turns_dir=turns_dir,
        lock_path=managed_root / "operation.lock",
        config_path=config_path,
        quarantine_path=managed_root / "ownership-quarantine.json",
    )


def build_codex_environment(profile: CodexProfile, *, binary: Path, temporary_dir: Path) -> dict[str, str]:
    """Return an allowlisted environment with no API/custom-provider credentials."""

    path_value = os.pathsep.join(dict.fromkeys((str(binary.parent), os.defpath)))
    environment = {
        "PATH": path_value,
        "HOME": str(profile.runtime_home),
        "CODEX_HOME": str(profile.root),
        "CODEX_SQLITE_HOME": str(profile.sqlite_home),
        "TMPDIR": str(temporary_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
        "RUST_BACKTRACE": "0",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "PATHEXT"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
    return environment


def verify_codex_auth_file(profile: CodexProfile) -> None:
    """Verify credential-file presence and metadata without reading its contents."""
    auth_path = profile.root / "auth.json"
    if not auth_path.exists() and not auth_path.is_symlink():
        raise auth_required()
    info = auth_path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CodexProviderError("unsafe_profile", "The managed Codex credential path is unsafe.")
    if os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise CodexProviderError("unsafe_profile", "The managed Codex credential file is not private.")
