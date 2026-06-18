# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from jiuwenclaw.agentserver.skilldev_agent.utils import resource_sync
from jiuwenclaw.agentserver.skilldev_agent.utils.resource_sync import (
    load_resource_state,
    record_direct_imported_skills,
    resource_state_path,
    save_resource_state,
    write_uploaded_resources,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _state(workspace: Path) -> dict:
    return json.loads(resource_state_path(workspace).read_text(encoding="utf-8"))


def test_resource_state_path_and_bad_json_fallback(tmp_path: Path) -> None:
    path = resource_state_path(tmp_path)
    assert path == tmp_path / "resources" / "resource_state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{bad json", encoding="utf-8")

    assert load_resource_state(tmp_path) == {}


def test_record_direct_imported_skills_deduplicates(tmp_path: Path) -> None:
    record_direct_imported_skills(
        tmp_path,
        [
            {"filename": "example.skill"},
            {"name": "example.skill"},
            {"filename": "other.zip"},
        ],
    )

    assert _state(tmp_path)["direct_imported_skills"] == ["example.skill", "other.zip"]


def test_ref_files_are_synchronized_and_stale_files_removed(tmp_path: Path) -> None:
    asyncio.run(
        write_uploaded_resources(
            tmp_path,
            {
                "files": [
                    {"filename": "keep.txt", "base64Data": _b64("keep")},
                    {"filename": "drop.txt", "base64Data": _b64("drop")},
                ]
            },
        )
    )
    ref_dir = tmp_path / "resources" / "ref-files"
    assert (ref_dir / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (ref_dir / "drop.txt").is_file()

    asyncio.run(
        write_uploaded_resources(
            tmp_path,
            {"files": [{"filename": "keep.txt", "base64Data": _b64("keep")}]},
        )
    )

    assert (ref_dir / "keep.txt").is_file()
    assert not (ref_dir / "drop.txt").exists()
    assert _state(tmp_path)["ref_files"] == [{"filename": "keep.txt"}]


def test_ref_skills_reject_unsupported_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不支持的文件类型"):
        asyncio.run(
            write_uploaded_resources(
                tmp_path,
                {"skill_packages": [{"filename": "bad.txt", "base64Data": _b64("x")}]},
            )
        )


def test_direct_imported_packages_are_not_written_as_references(tmp_path: Path) -> None:
    record_direct_imported_skills(tmp_path, [{"filename": "imported.skill"}])

    asyncio.run(
        write_uploaded_resources(
            tmp_path,
            {
                "files": [{"filename": "imported.skill", "base64Data": _b64("not a zip")}],
                "skill_packages": [{"filename": "imported.skill", "base64Data": _b64("not a zip")}],
            },
        )
    )

    assert not (tmp_path / "resources" / "ref-files" / "imported.skill").exists()
    assert not (tmp_path / "resources" / "ref-skills" / "imported.skill").exists()
    state = _state(tmp_path)
    assert state["direct_imported_skills"] == ["imported.skill"]
    assert state["ref_files"] == []
    assert state["ref_skills"] == []


def test_existing_url_resource_skips_download_and_records_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"
    ref_dir.mkdir(parents=True)
    (ref_dir / "remote.txt").write_text("existing", encoding="utf-8")
    save_resource_state(
        tmp_path,
        {"ref_files": [{"filename": "remote.txt", "url": "https://example.com/remote.txt"}]},
    )
    calls: list[tuple[str, str]] = []

    async def fake_download(url: str, dest: str) -> None:
        calls.append((url, dest))
        Path(dest).write_text("downloaded", encoding="utf-8")

    monkeypatch.setattr(resource_sync, "download_file", fake_download)

    asyncio.run(
        write_uploaded_resources(
            tmp_path,
            {"files": [{"filename": "remote.txt", "url": "https://example.com/remote.txt"}]},
        )
    )

    assert calls == []
    assert (ref_dir / "remote.txt").read_text(encoding="utf-8") == "existing"
    assert _state(tmp_path)["ref_files"] == [
        {"filename": "remote.txt", "url": "https://example.com/remote.txt"}
    ]


def test_existing_file_without_state_is_written_from_param(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"
    ref_dir.mkdir(parents=True)
    (ref_dir / "remote.txt").write_text("stale", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    async def fake_download(url: str, dest: str) -> None:
        calls.append((url, dest))
        Path(dest).write_text("fresh", encoding="utf-8")

    monkeypatch.setattr(resource_sync, "download_file", fake_download)

    asyncio.run(
        write_uploaded_resources(
            tmp_path,
            {"files": [{"filename": "remote.txt", "url": "https://example.com/remote.txt"}]},
        )
    )

    assert calls == [
        ("https://example.com/remote.txt", str(ref_dir / "remote.txt"))
    ]
    assert (ref_dir / "remote.txt").read_text(encoding="utf-8") == "fresh"


def test_tool_specs_are_synchronized(tmp_path: Path) -> None:
    tools_dir = tmp_path / "resources" / "available-tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "old__tool.json").write_text("{}", encoding="utf-8")
    save_resource_state(
        tmp_path,
        {"tool_specs": [{"filename": "old__tool.json", "pluginId": "old", "toolName": "tool"}]},
    )

    asyncio.run(
        write_uploaded_resources(
            tmp_path,
            {
                "tool_spec_files": [
                    {
                        "pluginId": "new",
                        "toolName": "search",
                        "description": "Search",
                        "arguments": {"type": "object", "properties": {}},
                    }
                ]
            },
        )
    )

    assert not (tools_dir / "old__tool.json").exists()
    assert (tools_dir / "new__search.json").is_file()
    assert _state(tmp_path)["tool_specs"] == [
        {"filename": "new__search.json", "pluginId": "new", "toolName": "search"}
    ]

    asyncio.run(write_uploaded_resources(tmp_path, {}))

    assert not (tools_dir / "new__search.json").exists()
    assert _state(tmp_path)["tool_specs"] == []
