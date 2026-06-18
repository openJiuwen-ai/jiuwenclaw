# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillDev resource synchronization helpers."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skilldev.common_utils import safe_extract_zip
from jiuwenclaw.agentserver.skilldev.utils.download_file_from_url import download_file

logger = logging.getLogger(__name__)

STATE_FILENAME = "resource_state.json"
STATE_DIRECT_IMPORTED = "direct_imported_skills"
STATE_REF_FILES = "ref_files"
STATE_REF_SKILLS = "ref_skills"
STATE_TOOL_SPECS = "tool_specs"


def resource_state_path(task_workspace: str | Path) -> Path:
    return Path(task_workspace) / "resources" / STATE_FILENAME


def load_resource_state(task_workspace: str | Path) -> dict[str, Any]:
    """Load resource sync state, falling back to an empty state on errors."""
    path = resource_state_path(task_workspace)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[resource_sync] failed to read %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("[resource_sync] ignore non-object state: %s", path)
        return {}
    return data


def save_resource_state(task_workspace: str | Path, state: dict[str, Any]) -> None:
    """Persist resource sync state without interrupting the caller on failure."""
    path = resource_state_path(task_workspace)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("[resource_sync] failed to write %s: %s", path, exc)


def record_direct_imported_skills(task_workspace: str | Path, packages: list[dict[str, Any]]) -> None:
    """Record directImport package filenames so reference sync can skip them."""
    package_names = {
        _resource_filename(item)
        for item in packages
        if isinstance(item, dict) and _resource_filename(item)
    }
    if not package_names:
        return
    state = load_resource_state(task_workspace)
    existing = {
        str(item)
        for item in state.get(STATE_DIRECT_IMPORTED, [])
        if str(item).strip()
    }
    state[STATE_DIRECT_IMPORTED] = sorted(existing | package_names)
    save_resource_state(task_workspace, state)


async def write_uploaded_resources(
    task_workspace: Path,
    params: dict[str, Any],
) -> None:
    """Synchronize uploaded resources into the SkillDev workspace."""
    task_workspace = Path(task_workspace)
    state = load_resource_state(task_workspace)
    direct_imported = {
        str(item)
        for item in state.get(STATE_DIRECT_IMPORTED, [])
        if str(item).strip()
    }

    await _sync_resource_group(
        _resource_list(params.get("files")),
        task_workspace / "resources" / "ref-files",
        state=state,
        state_key=STATE_REF_FILES,
        extract_zip_to_subdir=True,
        exclude_names=direct_imported,
    )
    await _sync_resource_group(
        _resource_list(params.get("skill_packages") or params.get("skillPackages")),
        task_workspace / "resources" / "ref-skills",
        state=state,
        state_key=STATE_REF_SKILLS,
        extract_zip_to_subdir=True,
        allowed_suffixes=(".zip", ".skill"),
        exclude_names=direct_imported,
    )
    await _sync_tool_spec_files(
        _resource_list(params.get("tool_spec_files") or params.get("toolSpecFiles")),
        task_workspace / "resources" / "available-tools",
        state=state,
    )

    _write_agent_and_cli_definitions(task_workspace, params)
    save_resource_state(task_workspace, state)


async def _sync_resource_group(
    resources: list[dict[str, Any]],
    dest_dir: Path,
    *,
    state: dict[str, Any],
    state_key: str,
    extract_zip_to_subdir: bool,
    allowed_suffixes: tuple[str, ...] | None = None,
    exclude_names: set[str] | None = None,
) -> None:
    exclude_names = exclude_names or set()
    current_items: list[tuple[dict[str, Any], dict[str, str]]] = []

    for res in resources:
        name = _resource_filename(res)
        if not name or name in exclude_names:
            continue
        suffix = Path(name).suffix.lower()
        if allowed_suffixes and suffix not in allowed_suffixes:
            raise ValueError(f"不支持的文件类型: {name}")
        if not _resource_has_payload(res):
            continue
        current_items.append((res, _resource_state_entry(res, name)))

    old_entries = _state_entries(state.get(state_key, []))
    old_keys = {_entry_key(entry) for entry in old_entries}
    current_entries = [entry for _, entry in current_items]
    current_keys = {_entry_key(entry) for entry in current_entries}

    _delete_stale_resource_files(dest_dir, old_entries, current_keys)

    pending_resources = [
        res
        for res, entry in current_items
        if _entry_key(entry) not in old_keys
    ]
    if pending_resources:
        await write_resource_group(
            pending_resources,
            dest_dir,
            extract_zip_to_subdir=extract_zip_to_subdir,
            allowed_suffixes=allowed_suffixes,
        )

    state[state_key] = _sorted_entries(current_entries)


async def _sync_tool_spec_files(
    resources: list[dict[str, Any]],
    dest_dir: Path,
    *,
    state: dict[str, Any],
) -> None:
    from jiuwenclaw.agentserver.skilldev_agent.meta_tools.external_tool_registry import (
        resolve_tool_spec_identity,
        tool_spec_filename,
    )

    tool_defs = _collect_tool_definitions(resources)
    old_entries = _tool_state_entries(state.get(STATE_TOOL_SPECS, []))
    old_filenames = {
        entry["filename"]
        for entry in old_entries
        if entry.get("filename")
    }
    current_filenames: set[str] = set()
    current_state: list[dict[str, str]] = []
    pending_tool_defs: list[dict[str, Any]] = []

    for tool_def in tool_defs:
        plugin_id, tool_name = resolve_tool_spec_identity(tool_def)
        if not plugin_id or not tool_name:
            logger.warning("[resource_sync] skip tool_spec without identity: %s", tool_def)
            continue
        filename = tool_spec_filename(plugin_id, tool_name)
        current_filenames.add(filename)
        current_state.append(
            {"pluginId": plugin_id, "toolName": tool_name, "filename": filename}
        )
        if filename not in old_filenames:
            pending_tool_defs.append(tool_def)

    _delete_stale_tool_files(dest_dir, old_entries, current_filenames)

    if pending_tool_defs:
        await write_tool_spec_files(pending_tool_defs, dest_dir)

    state[STATE_TOOL_SPECS] = sorted(
        current_state,
        key=lambda item: (item.get("pluginId", ""), item.get("toolName", "")),
    )


def _resource_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _resource_filename(resource: dict[str, Any]) -> str:
    value = resource.get("filename") or resource.get("name") or ""
    return str(value).strip()


def _resource_has_payload(resource: dict[str, Any]) -> bool:
    return bool(
        str(resource.get("url") or "").strip()
        or str(resource.get("base64Data") or resource.get("base64") or "").strip()
    )


def _resource_state_entry(resource: dict[str, Any], name: str) -> dict[str, str]:
    entry = {"filename": name}
    url = str(resource.get("url") or "").strip()
    if url:
        entry["url"] = url
    return entry


def _state_entries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            filename = str(item.get("filename") or "").strip()
            if not filename:
                continue
            entry = {"filename": filename}
            url = str(item.get("url") or "").strip()
            if url:
                entry["url"] = url
            entries.append(entry)
        elif isinstance(item, str) and item.strip():
            entries.append({"filename": item.strip()})
    return entries


def _tool_state_entries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").strip()
        if not filename:
            continue
        entry = {"filename": filename}
        plugin_id = str(item.get("pluginId") or "").strip()
        tool_name = str(item.get("toolName") or "").strip()
        if plugin_id:
            entry["pluginId"] = plugin_id
        if tool_name:
            entry["toolName"] = tool_name
        entries.append(entry)
    return entries


def _entry_key(entry: dict[str, str]) -> tuple[str, str]:
    return (entry.get("filename", ""), entry.get("url", ""))


def _sorted_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(entries, key=lambda item: (item.get("filename", ""), item.get("url", "")))


def _safe_child_path(dest_dir: Path, filename: str) -> Path | None:
    path = dest_dir / filename
    try:
        path.resolve().relative_to(dest_dir.resolve())
    except ValueError:
        logger.warning("[resource_sync] skip path outside resource dir: %s", filename)
        return None
    return path


def _delete_stale_resource_files(
    dest_dir: Path,
    old_entries: list[dict[str, str]],
    current_keys: set[tuple[str, str]],
) -> None:
    for entry in old_entries:
        if _entry_key(entry) in current_keys:
            continue
        file_path = _safe_child_path(dest_dir, entry["filename"])
        if file_path is not None and file_path.is_file():
            file_path.unlink()


def _delete_stale_tool_files(
    dest_dir: Path,
    old_entries: list[dict[str, str]],
    current_filenames: set[str],
) -> None:
    if not dest_dir.is_dir():
        return
    for entry in old_entries:
        filename = entry.get("filename", "")
        if not filename or filename in current_filenames:
            continue
        json_file = _safe_child_path(dest_dir, filename)
        if json_file is not None and json_file.is_file():
            json_file.unlink()


def _collect_tool_definitions(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from jiuwenclaw.agentserver.skilldev_agent.meta_tools.external_tool_registry import (
        iter_tool_definitions_from_json,
        resolve_tool_spec_identity,
    )

    tool_defs: list[dict[str, Any]] = []
    for res in resources:
        content_b64 = str(res.get("base64Data") or res.get("base64") or "").strip()
        if content_b64:
            try:
                raw_bytes = base64.b64decode(content_b64)
                parsed = json.loads(raw_bytes.decode("utf-8"))
            except Exception as exc:
                fname = res.get("filename", "?")
                raise ValueError(f"工具定义文件 [{fname}] 解析失败: {exc}") from exc
            tool_defs.extend(iter_tool_definitions_from_json(parsed))
            continue

        plugin_id, tool_name = resolve_tool_spec_identity(res)
        if plugin_id and tool_name:
            tool_defs.append(res)
        else:
            logger.warning(
                "[resource_sync] skip tool_spec entry without base64 or identity: %s",
                res.get("filename", res),
            )
    return tool_defs


async def write_tool_spec_files(resources: list[dict[str, Any]], dest_dir: Path) -> None:
    """Write uploaded tool specs as ``<pluginId>__<toolName>.json`` (pass-through)."""
    from jiuwenclaw.agentserver.skilldev_agent.meta_tools.external_tool_registry import (
        iter_tool_definitions_from_json,
        resolve_tool_spec_identity,
        write_tool_spec_file,
    )

    if not resources:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for res in resources:
        content_b64 = res.get("base64Data") or res.get("base64") or ""
        if content_b64:
            try:
                raw_bytes = base64.b64decode(content_b64)
                parsed = json.loads(raw_bytes.decode("utf-8"))
            except Exception as exc:
                fname = res.get("filename", "?")
                raise ValueError(f"工具定义文件 [{fname}] 解析失败: {exc}") from exc
            for tool_def in iter_tool_definitions_from_json(parsed):
                write_tool_spec_file(dest_dir, tool_def)
        else:
            plugin_id, tool_name = resolve_tool_spec_identity(res)
            if plugin_id and tool_name:
                write_tool_spec_file(dest_dir, res)
            else:
                logger.warning(
                    "[SkillDevDeepAdapter] skip tool_spec entry without base64 or "
                    "pluginId/bundleName+toolName: %s",
                    res.get("filename", res),
                )


async def write_skill_searched(task_workspace: Path, skill_searched: dict[str, Any]) -> None:
    """Download a skill selected from search results into ref-skills."""
    skill_name = skill_searched.get("skillId") or skill_searched.get("skillName") or "unknown"
    url = skill_searched.get("url", "")
    if not url:
        logger.warning("[SkillDevDeepAdapter] skill_searched missing url: %s", skill_searched)
        return

    dest_dir = task_workspace / "resources" / "ref-skills"
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(url).suffix.lower() or ".skill"
    if ".skill" in suffix:
        suffix = ".skill"
    elif ".zip" in suffix:
        suffix = ".zip"
    file_path = dest_dir / f"{skill_name}{suffix}"
    await download_file(url, str(file_path))
    if suffix in (".zip", ".skill"):
        safe_extract_zip(file_path, dest_dir, extract_to_stem_dir=False)


async def write_resource_group(
    resources: list[dict[str, Any]],
    dest_dir: Path,
    *,
    extract_zip_to_subdir: bool,
    allowed_suffixes: tuple[str, ...] | None = None,
) -> None:
    if not resources:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)

    if resources[0].get("url", ""):
        for res in resources:
            name = str(res.get("filename") or res.get("name") or "unknown")
            suffix = Path(name).suffix.lower()
            if allowed_suffixes and suffix not in allowed_suffixes:
                raise ValueError(f"不支持的文件类型: {name}")
            download_url = str(res.get("url", ""))
            if not download_url:
                continue
            file_path = dest_dir / name
            await download_file(download_url, str(file_path))
            if suffix in (".zip", ".skill"):
                safe_extract_zip(file_path, dest_dir, extract_to_stem_dir=False)
        return

    for res in resources:
        name = str(res.get("filename") or res.get("name") or "unknown")
        suffix = Path(name).suffix.lower()
        if allowed_suffixes and suffix not in allowed_suffixes:
            raise ValueError(f"不支持的文件类型: {name}")
        content_b64 = res.get("base64Data") or res.get("base64") or ""
        if not content_b64:
            continue
        file_path = dest_dir / name
        file_path.write_bytes(base64.b64decode(content_b64))
        if suffix in (".zip", ".skill"):
            safe_extract_zip(file_path, dest_dir, extract_to_stem_dir=extract_zip_to_subdir)


def _write_agent_and_cli_definitions(task_workspace: Path, params: dict[str, Any]) -> None:
    agent_definitions = params.get("agent_definitions") or params.get("agentDefinitions")
    if agent_definitions:
        agents_dir = task_workspace / "resources" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "available_agents.json").write_text(
            json.dumps(agent_definitions, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    cli_definitions = params.get("cli_definitions") or params.get("cliDefinitions")
    if cli_definitions:
        clis_dir = task_workspace / "resources" / "clis"
        clis_dir.mkdir(parents=True, exist_ok=True)
        (clis_dir / "available_clis.json").write_text(
            json.dumps(cli_definitions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
