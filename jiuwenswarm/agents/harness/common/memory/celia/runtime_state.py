"""Concurrent-safe OpenClaw compatible ``.xiaoyiruntime`` storage."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_TRUE = {"true", "1"}
_FALSE = {"false", "0"}


def resolve_runtime_state_path(explicit_path: str = "") -> Path:
    """Resolve the one shared state file used by Xiaoyi, Jiuwen and Celia."""
    configured = explicit_path or os.getenv("CELIA_XIAOYI_RUNTIME_PATH", "")
    if configured:
        return Path(configured).expanduser()
    config_dir = os.getenv("CELIA_CONFIG_DIR", "")
    if config_dir:
        return Path(config_dir).expanduser() / ".xiaoyiruntime"
    return Path.home() / ".openclaw" / ".xiaoyiruntime"


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _assignment(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    return key.strip(), value.strip().strip("\"'")


def _parse_bool(value: str | None) -> bool | None:
    normalized = (value or "").strip().strip("\"'").lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    return None


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


@contextmanager
def _process_lock(path: Path):
    """Small cross-process mkdir lock matching the OpenClaw lock strategy."""
    lock_dir = path.with_name(f".{path.name}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    for _ in range(200):
        try:
            lock_dir.mkdir()
            acquired = True
            break
        except FileExistsError:
            try:
                if time.time() - lock_dir.stat().st_mtime > 30:
                    lock_dir.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                pass
            time.sleep(0.01)
    if not acquired:
        raise TimeoutError(f"timed out locking runtime state: {path}")
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass


def _atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines).rstrip("\n") + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def read_runtime_values(explicit_path: str = "") -> dict[str, str]:
    path = resolve_runtime_state_path(explicit_path)
    with _lock_for(path):
        values: dict[str, str] = {}
        for raw in _read_lines(path):
            parsed = _assignment(raw)
            if parsed:
                values[parsed[0]] = parsed[1]
        return values


def read_memory_state(explicit_path: str = "") -> bool:
    """Read MEMORYSTATE. Missing and malformed values are closed by default."""
    value = read_runtime_values(explicit_path).get("MEMORYSTATE")
    return _parse_bool(value) is True


def update_runtime_values(values: Mapping[str, object], explicit_path: str = "") -> Path:
    """Atomically update selected keys while preserving every unrelated line."""
    path = resolve_runtime_state_path(explicit_path)
    normalized = {str(key): str(value) for key, value in values.items()}
    with _lock_for(path):
        with _process_lock(path):
            original = _read_lines(path)
            output: list[str] = []
            written: set[str] = set()
            for raw in original:
                parsed = _assignment(raw)
                key = parsed[0] if parsed else None
                if key in normalized:
                    if key not in written:
                        output.append(f"{key}={normalized[key]}")
                        written.add(key)
                    continue
                output.append(raw)
            for key, value in normalized.items():
                if key not in written:
                    output.append(f"{key}={value}")
            _atomic_write(path, output)
    return path


def ensure_runtime_state(explicit_path: str = "") -> Path:
    path = resolve_runtime_state_path(explicit_path)
    with _lock_for(path):
        with _process_lock(path):
            if path.is_file():
                return path
            _atomic_write(path, ["MEMORYSTATE=false"])
    return path


def set_memory_state(value: bool, explicit_path: str = "") -> Path:
    if not isinstance(value, bool):
        raise TypeError("memory state must be a boolean")
    return update_runtime_values(
        {"MEMORYSTATE": "true" if value else "false"}, explicit_path
    )


def update_runtime_info(
    session_id: str,
    conversation_id: str,
    task_id: str,
    explicit_path: str = "",
) -> Path:
    return update_runtime_values(
        {
            "SESSION_ID": session_id,
            "CONVERSATION_ID": conversation_id,
            "TASK_ID": task_id,
        },
        explicit_path,
    )
