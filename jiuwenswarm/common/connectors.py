# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""连接器 CLI 白名单：命中则宿主执行，不进沙箱。

入口名只改 ``resources/connect_cli_config.json``（可叠加 env / ``<connectors>/connect_cli_config.json``）。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

CLI_NAMES_FILENAME = "cli-names.json"
CLI_CONFIG_FILENAME = "connect_cli_config.json"
CLI_CONFIG_ENV = "JIUWENSWARM_CONNECTOR_CLI_CONFIG"
CONNECTORS_DIR_ENV = "JIUWENSWARM_CONNECTORS_DIR"


@dataclass(frozen=True)
class ConnectorCliSpec:
    id: str
    commands: tuple[str, ...]


_CLI_CONFIG_CACHE: dict[
    tuple[tuple[str, int], ...],
    tuple[tuple[ConnectorCliSpec, ...], tuple[str, ...]],
] = {}


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def expand_cli_exclude_globs(command: str) -> tuple[str, ...]:
    """入口名 → sandbox.excluded_commands glob。"""
    name = str(command).strip()
    if not name or any(ch in name for ch in '*?[]\\/'):
        return ()
    exe = f"{name}.exe"
    return (
        name,
        f"{name} *",
        exe,
        f"{exe} *",
        f"*\\{exe}",
        f"*\\{exe} *",
        f"*/{name}",
        f"*/{name} *",
        f"*/{exe}",
        f"*/{exe} *",
    )


def _add_connectors_candidate(out: list[Path], seen: set[str], path: Path) -> None:
    key = os.path.normcase(str(path))
    if key in seen:
        return
    seen.add(key)
    out.append(path)


def iter_connectors_dirs() -> list[Path]:
    """连接器根目录候选：env、DATA_DIR 旁、%APPDATA%/claw-desktop。"""
    seen: set[str] = set()
    out: list[Path] = []
    for key in (CONNECTORS_DIR_ENV, "CLAW_CONNECTORS_DIR"):
        raw = os.environ.get(key, "").strip()
        if raw:
            _add_connectors_candidate(out, seen, Path(raw))
            break
    data = os.environ.get("JIUWENSWARM_DATA_DIR", "").strip()
    if data:
        data_path = Path(data)
        _add_connectors_candidate(out, seen, data_path / "connectors")
        if data_path.name.lower() == "jiuwenswarm":
            _add_connectors_candidate(out, seen, data_path.parent / "connectors")
    for env_key, vendor in (("APPDATA", "claw-desktop"), ("LOCALAPPDATA", "claw-desktop")):
        root = os.environ.get(env_key, "").strip()
        if root:
            _add_connectors_candidate(out, seen, Path(root) / vendor / "connectors")
    return out


def resolve_connectors_dir() -> Path | None:
    """第一个存在的连接器根目录。"""
    dirs = iter_connectors_dirs()
    if not dirs:
        return None
    for path in dirs:
        if path.is_dir():
            return path
    return dirs[0]


def connector_bin_dirs(*, connectors_dir: Path | None = None) -> list[str]:
    """PATH 前置目录：``connectors/`` 及其子目录。"""
    roots = [connectors_dir] if connectors_dir is not None else iter_connectors_dirs()
    dirs: list[str] = []
    for root in roots:
        if root is None or not root.is_dir():
            continue
        dirs.append(str(root))
        try:
            children = sorted(
                (p for p in root.iterdir() if p.is_dir()),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            continue
        for child in children:
            dirs.append(str(child))
    return _unique(dirs)


def bundled_cli_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / CLI_CONFIG_FILENAME


def _cli_config_paths(*, connectors_dir: Path | None = None) -> list[Path]:
    paths = [bundled_cli_config_path()]
    env = os.environ.get(CLI_CONFIG_ENV, "").strip()
    if env:
        paths.append(Path(env))
    root = resolve_connectors_dir() if connectors_dir is None else connectors_dir
    if root is not None:
        paths.append(Path(root) / CLI_CONFIG_FILENAME)
    return paths


def _read_json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError):
        return None


def _normalize_cli_names(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return _unique([str(x).strip() for x in raw if str(x).strip()])


def _parse_cli_config(data) -> tuple[tuple[ConnectorCliSpec, ...], tuple[str, ...]]:
    if isinstance(data, list):
        return (), tuple(_normalize_cli_names(data))
    if not isinstance(data, dict):
        return (), ()
    loose: list[str] = []
    for key in ("cli", "commands", "names"):
        loose.extend(_normalize_cli_names(data.get(key)))
    specs: list[ConnectorCliSpec] = []
    connectors = data.get("connectors")
    if isinstance(connectors, dict):
        for cid, value in connectors.items():
            cid_s = str(cid).strip()
            if isinstance(value, dict):
                cmds = _normalize_cli_names(value.get("commands") or value.get("cli"))
            else:
                cmds = _normalize_cli_names(value)
            if cid_s and cmds:
                specs.append(ConnectorCliSpec(id=cid_s, commands=tuple(cmds)))
    elif isinstance(connectors, list):
        for item in connectors:
            if not isinstance(item, dict):
                continue
            cid_s = str(item.get("id") or "").strip()
            cmds = _normalize_cli_names(item.get("commands") or item.get("cli"))
            if cid_s and cmds:
                specs.append(ConnectorCliSpec(id=cid_s, commands=tuple(cmds)))
            elif cmds:
                loose.extend(cmds)
    return tuple(specs), tuple(_unique(loose))


def _merge_specs(
    base: list[ConnectorCliSpec], extra: list[ConnectorCliSpec]
) -> list[ConnectorCliSpec]:
    by_id: dict[str, list[str]] = {}
    order: list[str] = []
    for spec in (*base, *extra):
        if spec.id not in by_id:
            order.append(spec.id)
            by_id[spec.id] = []
        for name in spec.commands:
            if name not in by_id[spec.id]:
                by_id[spec.id].append(name)
    return [ConnectorCliSpec(id=item, commands=tuple(by_id[item])) for item in order]


def load_connector_cli_config(
    *, connectors_dir: Path | None = None
) -> tuple[tuple[ConnectorCliSpec, ...], tuple[str, ...]]:
    paths = _cli_config_paths(connectors_dir=connectors_dir)
    fingerprint = tuple(
        (str(path), path.stat().st_mtime_ns if path.is_file() else 0) for path in paths
    )
    cached = _CLI_CONFIG_CACHE.get(fingerprint)
    if cached is not None:
        return cached
    specs: list[ConnectorCliSpec] = []
    loose: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        file_specs, file_loose = _parse_cli_config(_read_json_file(path))
        specs = _merge_specs(specs, list(file_specs))
        for name in file_loose:
            if name not in loose:
                loose.append(name)
    result = (tuple(specs), tuple(loose))
    _CLI_CONFIG_CACHE[fingerprint] = result
    return result


def connector_cli_registry(
    *, connectors_dir: Path | None = None
) -> tuple[ConnectorCliSpec, ...]:
    specs, _ = load_connector_cli_config(connectors_dir=connectors_dir)
    return specs


def _read_cli_names_file(directory: Path) -> list[str]:
    path = directory / CLI_NAMES_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError):
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, dict) and isinstance(raw.get("commands"), list):
        return [str(x).strip() for x in raw["commands"] if str(x).strip()]
    return []


def collect_connector_cli_names(*, connectors_dir: Path | None = None) -> tuple[str, ...]:
    names: list[str] = []
    specs, loose = load_connector_cli_config(connectors_dir=connectors_dir)
    for spec in specs:
        names.extend(spec.commands)
    names.extend(loose)
    roots = [connectors_dir] if connectors_dir is not None else iter_connectors_dirs()
    for root in roots:
        if root is None or not root.is_dir():
            continue
        try:
            children = sorted(
                (p for p in root.iterdir() if p.is_dir()),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            children = []
        for child in children:
            names.extend(_read_cli_names_file(child))
    return tuple(_unique(names))


def connector_excluded_commands(*, connectors_dir: Path | None = None) -> tuple[str, ...]:
    globs: list[str] = []
    for name in collect_connector_cli_names(connectors_dir=connectors_dir):
        globs.extend(expand_cli_exclude_globs(name))
    return tuple(_unique(globs))


def is_connector_exclude_glob(pattern: str, *, connectors_dir: Path | None = None) -> bool:
    item = str(pattern).strip()
    return bool(item) and item in connector_excluded_commands(connectors_dir=connectors_dir)


# 管道 / && / 重定向等不能拆成 argv 直跑。
_COMPOUND_SHELL_RE = re.compile(r"(?:&&|\|\||[|;\n\r<>`]|\$\()")


def cli_basename(token: str) -> str:
    name = Path(str(token).strip().strip("\"'").replace("\\", "/")).name
    lower = name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if lower.endswith(suffix):
            return name[: -len(suffix)].lower()
    return lower


def is_connector_cli_name(token: str, *, connectors_dir: Path | None = None) -> bool:
    name = cli_basename(token)
    if not name:
        return False
    known = {item.lower() for item in collect_connector_cli_names(connectors_dir=connectors_dir)}
    return name in known


def command_uses_connector_cli(command: str, *, connectors_dir: Path | None = None) -> bool:
    stripped = str(command or "").strip()
    if not stripped:
        return False
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        tokens = stripped.split()
    for token in tokens:
        if not token or token.startswith("-"):
            continue
        if is_connector_cli_name(token, connectors_dir=connectors_dir):
            return True
    return False


def _alias_cli_names(name: str, *, connectors_dir: Path | None = None) -> tuple[str, ...]:
    names = [name]
    for spec in connector_cli_registry(connectors_dir=connectors_dir):
        known = {item.lower() for item in spec.commands}
        if name.lower() not in known:
            continue
        for item in spec.commands:
            if item.lower() not in {n.lower() for n in names}:
                names.append(item)
    return tuple(names)


def resolve_connector_executable(
    token: str, *, connectors_dir: Path | None = None
) -> Path | None:
    raw = str(token).strip().strip("\"'")
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        return candidate
    name = cli_basename(raw)
    if not name:
        return None
    aliases = _alias_cli_names(name, connectors_dir=connectors_dir)
    roots = [connectors_dir] if connectors_dir is not None else iter_connectors_dirs()
    for root in roots:
        if root is None or not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir():
                continue
            for alias in aliases:
                for filename in (f"{alias}.exe", alias, f"{alias}.cmd", f"{alias}.bat"):
                    hit = child / filename
                    if hit.is_file():
                        return hit
    for alias in aliases:
        which = shutil.which(alias) or shutil.which(f"{alias}.exe")
        if which:
            return Path(which)
    return None


def connector_host_argv(
    command: str, *, connectors_dir: Path | None = None
) -> list[str] | None:
    """简单命令 → ``[exe, ...args]``；复合命令返回 None，改走宿主 shell。"""
    stripped = str(command or "").strip()
    if not stripped or _COMPOUND_SHELL_RE.search(stripped):
        return None
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        tokens = stripped.split()
    if not tokens:
        return None
    if not is_connector_cli_name(tokens[0], connectors_dir=connectors_dir):
        return None
    exe = resolve_connector_executable(tokens[0], connectors_dir=connectors_dir)
    if exe is None:
        return None
    return [str(exe), *tokens[1:]]


_SHELL_METACHARS = {"|", "||", "&&", ";", ">", ">>", "<", "2>", "2>>", "1>", "&"}


def _quote_for_host_shell(token: str) -> str:
    if token in _SHELL_METACHARS:
        return token
    if os.name == "nt":
        if not token or any(ch.isspace() or ch in '&()[]{}^=;!\'+,`~"' for ch in token):
            return '"' + token.replace('"', '\\"') + '"'
        return token
    return shlex.quote(token)


def expand_connector_cli_tokens(
    command: str, *, connectors_dir: Path | None = None
) -> str:
    """复合命令里把 CLI 名换成 exe 全路径。"""
    stripped = str(command or "").strip()
    if not stripped:
        return stripped
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        tokens = stripped.split()
    if not tokens:
        return stripped
    out: list[str] = []
    changed = False
    for token in tokens:
        if is_connector_cli_name(token, connectors_dir=connectors_dir):
            exe = resolve_connector_executable(token, connectors_dir=connectors_dir)
            if exe is not None:
                out.append(str(exe))
                changed = True
                continue
        out.append(token)
    if not changed:
        return stripped
    return " ".join(_quote_for_host_shell(item) for item in out)
