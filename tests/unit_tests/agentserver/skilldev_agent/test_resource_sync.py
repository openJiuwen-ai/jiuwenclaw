# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from jiuwenclaw.agentserver.skilldev_agent.adapter import SkillDevDeepAdapter
from jiuwenclaw.agentserver.skilldev_agent.utils import resource_sync
from jiuwenclaw.agentserver.skilldev_agent.utils.resource_sync import (
    build_current_ref_file_hint_lines,
    build_current_ref_skill_hint_lines,
    build_current_tool_spec_hint_lines,
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


def _zip_b64(files: dict[str, str] | None = None) -> str:
    files = files or {"readme.txt": "hello"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_build_current_ref_file_hint_lines_single_and_multiple(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"
    ref_dir.mkdir(parents=True)

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [
            {"filename": "requirement.md", "base64Data": _b64("# req")},
            {"name": "notes.txt", "base64": _b64("note")},
        ],
    )

    assert added_lines == [
        f"- requirement.md -> 本地路径: {(ref_dir / 'requirement.md').resolve()}",
        f"- notes.txt -> 本地路径: {(ref_dir / 'notes.txt').resolve()}",
    ]
    assert removed_lines == []


def test_build_current_ref_file_hint_lines_zip_includes_extract_dir(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [{"filename": "demo.zip", "base64Data": _zip_b64()}],
    )

    assert added_lines == [
        f"- demo.zip -> 本地路径: {(ref_dir / 'demo.zip').resolve()}",
        f"  - 解压目录 -> {(ref_dir / 'demo').resolve()}",
    ]
    assert removed_lines == []


def test_build_current_ref_file_hint_lines_url_zip_extracts_to_ref_dir(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [{"filename": "remote.zip", "url": "https://example.com/remote.zip"}],
    )

    assert added_lines == [
        f"- remote.zip -> 本地路径: {(ref_dir / 'remote.zip').resolve()}",
        f"  - 解压目录 -> {ref_dir.resolve()}",
    ]
    assert removed_lines == []


def test_build_current_ref_file_hint_lines_skips_direct_import_and_empty_payload(
    tmp_path: Path,
) -> None:
    record_direct_imported_skills(tmp_path, [{"filename": "imported.skill"}])

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [
            {"filename": "imported.skill", "base64Data": _b64("x")},
            {"filename": "empty.txt"},
            "not-a-dict",
        ],
    )

    assert added_lines == []
    assert removed_lines == []


def test_build_current_ref_file_hint_lines_image_appends_source_url(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"
    ref_dir.mkdir(parents=True)
    image_url = "https://xiaoyi.example.com/files/222.jpg"

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [
            {
                "filename": "222.jpg",
                "url": image_url,
                "mime": "image/jpeg",
            }
        ],
    )

    assert added_lines == [
        f"- 222.jpg -> 本地路径: {(ref_dir / '222.jpg').resolve()} ; 可下载url: {image_url}",
    ]
    assert removed_lines == []


def test_build_current_ref_file_hint_lines_non_image_does_not_append_url(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"
    ref_dir.mkdir(parents=True)

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [
            {
                "filename": "notes.txt",
                "url": "https://xiaoyi.example.com/files/notes.txt",
                "mime": "text/plain",
            }
        ],
    )

    assert added_lines == [f"- notes.txt -> 本地路径: {(ref_dir / 'notes.txt').resolve()}"]
    assert removed_lines == []


def test_build_resource_hint_header_and_file_lines(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"
    ref_dir.mkdir(parents=True)
    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [{"filename": "requirement.md", "base64Data": _b64("req")}],
    )

    hint = SkillDevDeepAdapter._build_resource_hint(
        tmp_path,
        {"files": [{"filename": "requirement.md", "base64Data": _b64("req")}]},
        "task-1",
        added_file_lines=added_lines,
        removed_file_lines=removed_lines,
    )

    assert "【用户需求】" not in hint
    assert "【工作区信息】" in hint
    assert "【本轮上传资源索引（已落盘，可直接读取）】" in hint
    assert "【本轮已移除资源】" not in hint
    assert "【执行要求】" in hint
    assert "用户上传资源已写入" not in hint
    assert f"- requirement.md -> 本地路径: {(ref_dir / 'requirement.md').resolve()}" in hint


def test_build_current_ref_file_hint_lines_skips_reuploaded_files(tmp_path: Path) -> None:
    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [{"filename": "a.txt", "base64Data": _b64("same")}],
        previous_ref_files=[{"filename": "a.txt"}],
    )

    assert added_lines == []
    assert removed_lines == []


def test_build_current_ref_file_hint_lines_only_adds_new_files(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [
            {"filename": "a.txt", "base64Data": _b64("a")},
            {"filename": "b.txt", "base64Data": _b64("b")},
        ],
        previous_ref_files=[{"filename": "a.txt"}],
    )

    assert added_lines == [f"- b.txt -> 本地路径: {(ref_dir / 'b.txt').resolve()}"]
    assert removed_lines == []


def test_build_current_ref_file_hint_lines_only_reports_removed_files(tmp_path: Path) -> None:
    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [],
        previous_ref_files=[{"filename": "a.txt"}],
    )

    assert added_lines == []
    assert removed_lines == ["- a.txt -> 已移除（用户本轮未再上传）"]


def test_build_current_ref_file_hint_lines_add_and_remove_together(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-files"

    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [
            {"filename": "a.txt", "base64Data": _b64("a")},
            {"filename": "c.txt", "base64Data": _b64("c")},
        ],
        previous_ref_files=[
            {"filename": "a.txt"},
            {"filename": "b.txt"},
        ],
    )

    assert added_lines == [f"- c.txt -> 本地路径: {(ref_dir / 'c.txt').resolve()}"]
    assert removed_lines == ["- b.txt -> 已移除（用户本轮未再上传）"]


def test_build_current_ref_file_hint_lines_zip_removal_has_no_extract_dir(tmp_path: Path) -> None:
    added_lines, removed_lines = build_current_ref_file_hint_lines(
        tmp_path,
        [],
        previous_ref_files=[{"filename": "demo.zip"}],
    )

    assert added_lines == []
    assert removed_lines == ["- demo.zip -> 已移除（用户本轮未再上传）"]


def test_build_resource_hint_includes_removed_section_when_only_deletions(tmp_path: Path) -> None:
    hint = SkillDevDeepAdapter._build_resource_hint(
        tmp_path,
        {},
        "task-1",
        added_file_lines=[],
        removed_file_lines=["- drop.txt -> 已移除（用户本轮未再上传）"],
        added_skill_lines=[],
        removed_skill_lines=[],
        added_tool_lines=[],
        removed_tool_lines=[],
    )

    assert "【工作区信息】" in hint
    assert "【本轮上传资源索引（已落盘，可直接读取）】" not in hint
    assert "【本轮已移除资源】" in hint
    assert "- drop.txt -> 已移除（用户本轮未再上传）" in hint
    assert "【执行要求】" in hint


def test_build_current_ref_skill_hint_lines_add_and_remove(tmp_path: Path) -> None:
    ref_dir = tmp_path / "resources" / "ref-skills"
    added_lines, removed_lines = build_current_ref_skill_hint_lines(
        tmp_path,
        [{"filename": "new.skill", "base64Data": _b64("x")}],
        previous_ref_skills=[{"filename": "old.skill"}],
    )

    assert added_lines == [
        f"- new.skill -> 本地路径: {(ref_dir / 'new.skill').resolve()}",
        f"  - 解压目录 -> {(ref_dir / 'new').resolve()}",
    ]
    assert removed_lines == ["- old.skill -> 已移除（用户本轮未再上传）"]


def test_build_current_ref_skill_hint_lines_skips_reuploaded(tmp_path: Path) -> None:
    added_lines, removed_lines = build_current_ref_skill_hint_lines(
        tmp_path,
        [{"filename": "keep.zip", "base64Data": _zip_b64()}],
        previous_ref_skills=[{"filename": "keep.zip"}],
    )

    assert added_lines == []
    assert removed_lines == []


def test_build_current_tool_spec_hint_lines_add_and_remove(tmp_path: Path) -> None:
    tools_dir = tmp_path / "resources" / "available-tools"
    added_lines, removed_lines = build_current_tool_spec_hint_lines(
        tmp_path,
        [{"pluginId": "new", "toolName": "search"}],
        previous_tool_specs=[{"filename": "old__tool.json", "pluginId": "old", "toolName": "tool"}],
    )

    assert added_lines == [f"- new/search -> 本地路径: {(tools_dir / 'new__search.json').resolve()}"]
    assert removed_lines == ["- old/tool -> 已移除（用户本轮未再上传）"]


def test_build_resource_hint_includes_skill_and_tool_sections(tmp_path: Path) -> None:
    hint = SkillDevDeepAdapter._build_resource_hint(
        tmp_path,
        {},
        "task-1",
        added_file_lines=[],
        removed_file_lines=[],
        added_skill_lines=["- demo.skill -> 本地路径: X", "  - 解压目录 -> Y"],
        removed_skill_lines=["- old.skill -> 已移除（用户本轮未再上传）"],
        added_tool_lines=["- a/b -> 本地路径: Z"],
        removed_tool_lines=["- c/d -> 已移除（用户本轮未再上传）"],
    )

    assert "【本轮新增参考 Skill 包】" in hint
    assert "【本轮已移除参考 Skill 包】" in hint
    assert "【本轮新增可用工具】" in hint
    assert "【本轮已移除可用工具】" in hint
