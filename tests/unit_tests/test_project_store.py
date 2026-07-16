# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def project_store_dir(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    root.mkdir()
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.project_store.get_agent_root_dir",
        lambda: root,
    )
    from jiuwenswarm.server.runtime.session import project_store
    project_store.invalidate_cache()
    return root


def _read_projects(path: Path) -> list[dict]:
    p = path / "projects.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("projects", [])


class TestCreateProject:
    @staticmethod
    def test_create_persists_and_get_by_id(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, get_project_by_id,
        )

        proj = create_project("我的应用", "E:\\projA")
        assert proj.project_id.startswith("proj_")
        assert proj.name == "我的应用"
        assert proj.project_dir == "E:\\projA"
        assert proj.hidden is False
        assert proj.pinned is False
        assert proj.pin_order == 0

        records = _read_projects(project_store_dir)
        assert len(records) == 1
        assert records[0]["project_id"] == proj.project_id

        found = get_project_by_id(proj.project_id, cache_bust=True)
        assert found is not None
        assert found.name == "我的应用"

    @staticmethod
    def test_get_by_id_returns_none_for_missing(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import get_project_by_id

        assert get_project_by_id("proj_nope", cache_bust=True) is None

    @staticmethod
    def test_get_by_dir_finds_hidden_and_visible(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, get_project_by_dir,
        )

        proj = create_project("P1", "E:\\path1")
        proj.hidden = True
        save_project(proj)

        found = get_project_by_dir("E:\\path1", cache_bust=True)
        assert found is not None
        assert found.hidden is True
        assert found.project_id == proj.project_id

    @staticmethod
    def test_get_by_dir_returns_none_for_missing(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import get_project_by_dir

        assert get_project_by_dir("E:\\nope", cache_bust=True) is None


class TestSaveProject:
    @staticmethod
    def test_update_existing_fields(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, get_project_by_id,
        )

        proj = create_project("P", "E:\\p")
        proj.name = "renamed"
        proj.pinned = True
        proj.pin_order = 5
        save_project(proj)

        found = get_project_by_id(proj.project_id, cache_bust=True)
        assert found.name == "renamed"
        assert found.pinned is True
        assert found.pin_order == 5

    @staticmethod
    def test_save_appends_when_id_not_found(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            save_project, get_project_by_id, Project,
        )

        proj = Project(project_id="proj_manual", name="M", project_dir="E:\\m")
        save_project(proj)

        found = get_project_by_id("proj_manual", cache_bust=True)
        assert found is not None
        assert found.name == "M"
        assert found.project_dir == "E:\\m"


class TestListProjects:
    @staticmethod
    def test_list_excludes_hidden_by_default(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, list_projects,
        )

        p1 = create_project("P1", "E:\\p1")
        p2 = create_project("P2", "E:\\p2")
        p2.hidden = True
        save_project(p2)

        visible = list_projects(cache_bust=True)
        ids = [p.project_id for p in visible]
        assert p1.project_id in ids
        assert p2.project_id not in ids

    @staticmethod
    def test_list_includes_hidden_when_flag(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, list_projects,
        )

        p1 = create_project("P1", "E:\\p1")
        p2 = create_project("P2", "E:\\p2")
        p2.hidden = True
        save_project(p2)

        all_proj = list_projects(include_hidden=True, cache_bust=True)
        ids = [p.project_id for p in all_proj]
        assert p1.project_id in ids
        assert p2.project_id in ids


class TestCronProjectResolution:
    @staticmethod
    def test_resolve_cron_project_id_bypasses_stale_cache(project_store_dir, tmp_path):
        from jiuwenswarm.server.runtime.session.project_store import (
            list_projects,
            resolve_cron_project_id,
        )

        assert list_projects() == []
        project_dir = str(tmp_path / "project-a")
        projects_file = project_store_dir / "projects.json"
        projects_file.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": "proj_external",
                            "name": "external",
                            "project_dir": project_dir,
                            "hidden": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        assert resolve_cron_project_id(project_dir) == "proj_external"


class TestHiddenRestore:
    @staticmethod
    def test_hidden_then_restore_visibility(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, list_projects, get_project_by_id,
        )

        proj = create_project("P", "E:\\p")
        proj.hidden = True
        save_project(proj)
        assert get_project_by_id(proj.project_id, cache_bust=True).hidden is True
        assert proj.project_id not in [p.project_id for p in list_projects(cache_bust=True)]

        proj.hidden = False
        save_project(proj)
        assert proj.project_id in [p.project_id for p in list_projects(cache_bust=True)]


class TestPinReindex:
    @staticmethod
    def test_reindex_compact_after_unpin(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, reindex_project_pin_orders,
            list_projects,
        )

        p1 = create_project("P1", "E:\\p1")
        p2 = create_project("P2", "E:\\p2")
        p3 = create_project("P3", "E:\\p3")
        for p, order in [(p1, 1), (p2, 2), (p3, 3)]:
            p.pinned = True
            p.pin_order = order
            save_project(p)

        p2.pinned = False
        p2.pin_order = 0
        save_project(p2)
        reindex_project_pin_orders()

        all_proj = {p.project_id: p for p in list_projects(include_hidden=True, cache_bust=True)}
        pinned = sorted([p for p in all_proj.values() if p.pinned], key=lambda x: x.pin_order)
        assert [p.pin_order for p in pinned] == [1, 2]
        assert all_proj[p2.project_id].pin_order == 0
        assert all_proj[p2.project_id].pinned is False

    @staticmethod
    def test_reindex_no_gap_on_repeated_toggle(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, reindex_project_pin_orders,
            list_projects,
        )

        p1 = create_project("P1", "E:\\p1")
        p2 = create_project("P2", "E:\\p2")

        for _ in range(3):
            p1.pinned = True
            p1.pin_order = 0
            save_project(p1)
            p2.pinned = True
            p2.pin_order = 0
            save_project(p2)
            reindex_project_pin_orders()
            p1.pinned = False
            p1.pin_order = 0
            save_project(p1)
            reindex_project_pin_orders()

        all_proj = list_projects(include_hidden=True, cache_bust=True)
        pinned = [p for p in all_proj if p.pinned]
        assert len(pinned) == 1
        assert pinned[0].project_id == p2.project_id
        assert pinned[0].pin_order == 1

    @staticmethod
    def test_reindex_unpinned_gets_zero(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, reindex_project_pin_orders,
            list_projects,
        )

        p1 = create_project("P1", "E:\\p1")
        p2 = create_project("P2", "E:\\p2")
        p2.pinned = True
        p2.pin_order = 1
        save_project(p2)
        p1.pin_order = 99
        save_project(p1)

        reindex_project_pin_orders()
        all_proj = {p.project_id: p for p in list_projects(include_hidden=True, cache_bust=True)}
        assert all_proj[p1.project_id].pin_order == 0
        assert all_proj[p2.project_id].pin_order == 1


class TestCreateOrRestoreProject:
    @staticmethod
    def test_create_new_returns_not_restored(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_or_restore_project,
        )

        proj, restored = create_or_restore_project("P", "E:\\p")
        assert proj.project_id.startswith("proj_")
        assert restored is False
        assert proj.name == "P"
        assert proj.project_dir == "E:\\p"

    @staticmethod
    def test_restore_hidden_by_dir(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, create_or_restore_project,
            get_project_by_id,
        )

        proj = create_project("旧名", "E:\\p")
        proj.hidden = True
        save_project(proj)

        restored_proj, restored = create_or_restore_project("新名", "E:\\p")
        assert restored is True
        assert restored_proj.project_id == proj.project_id
        assert restored_proj.name == "新名"
        assert restored_proj.hidden is False
        assert get_project_by_id(proj.project_id, cache_bust=True).name == "新名"

    @staticmethod
    def test_path_conflict_on_visible(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_or_restore_project, ProjectDirConflict,
        )

        create_or_restore_project("P1", "E:\\p")
        with pytest.raises(ProjectDirConflict):
            create_or_restore_project("P2", "E:\\p")

    @staticmethod
    def test_name_conflict_on_create(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_or_restore_project, ProjectNameConflict,
        )

        create_or_restore_project("P1", "E:\\p1")
        with pytest.raises(ProjectNameConflict):
            create_or_restore_project("P1", "E:\\p2")

    @staticmethod
    def test_name_conflict_with_hidden_project_on_create(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, create_or_restore_project,
            ProjectNameConflict,
        )

        proj = create_project("P", "E:\\p1")
        proj.hidden = True
        save_project(proj)
        with pytest.raises(ProjectNameConflict):
            create_or_restore_project("P", "E:\\p2")

    @staticmethod
    def test_name_conflict_excludes_path_match_on_restore(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, create_or_restore_project,
        )

        proj = create_project("P", "E:\\p")
        proj.hidden = True
        save_project(proj)
        restored_proj, restored = create_or_restore_project("P", "E:\\p")
        assert restored is True
        assert restored_proj.name == "P"

    @staticmethod
    def test_name_conflict_on_restore_with_other_visible(project_store_dir):
        # setup 走底层 create_project:公开 API create_or_restore_project 现会拦截隐藏项目同名
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, create_or_restore_project,
            ProjectNameConflict,
        )

        proj = create_project("P", "E:\\p")
        proj.hidden = True
        save_project(proj)
        create_project("P", "E:\\p2")
        with pytest.raises(ProjectNameConflict):
            create_or_restore_project("P", "E:\\p")


class TestRenameProject:
    @staticmethod
    def test_rename_to_unique(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, rename_project, get_project_by_id,
        )

        proj = create_project("P1", "E:\\p1")
        updated = rename_project(proj.project_id, "新名")
        assert updated is not None
        assert updated.name == "新名"
        assert get_project_by_id(proj.project_id, cache_bust=True).name == "新名"

    @staticmethod
    def test_rename_to_self_name_ok(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, rename_project,
        )

        proj = create_project("P", "E:\\p")
        updated = rename_project(proj.project_id, "P")
        assert updated is not None
        assert updated.name == "P"

    @staticmethod
    def test_rename_conflict_with_other_visible(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, rename_project, ProjectNameConflict,
        )

        create_project("P1", "E:\\p1")
        p2 = create_project("P2", "E:\\p2")
        with pytest.raises(ProjectNameConflict):
            rename_project(p2.project_id, "P1")

    @staticmethod
    def test_rename_conflicts_with_hidden_project(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, rename_project, ProjectNameConflict,
        )

        p1 = create_project("P", "E:\\p1")
        p1.hidden = True
        save_project(p1)
        p2 = create_project("P2", "E:\\p2")
        with pytest.raises(ProjectNameConflict):
            rename_project(p2.project_id, "P")

    @staticmethod
    def test_rename_not_found_returns_none(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import rename_project

        assert rename_project("proj_nope", "X") is None


class TestRestoreProject:
    @staticmethod
    def test_restore_hidden(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, restore_project, get_project_by_id,
        )

        proj = create_project("P", "E:\\p")
        proj.hidden = True
        save_project(proj)

        restored = restore_project(proj.project_id)
        assert restored is not None
        assert restored.hidden is False
        assert get_project_by_id(proj.project_id, cache_bust=True).hidden is False

    @staticmethod
    def test_restore_already_visible_returns_none(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, restore_project,
        )

        proj = create_project("P", "E:\\p")
        assert restore_project(proj.project_id) is None

    @staticmethod
    def test_restore_not_found_returns_none(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import restore_project

        assert restore_project("proj_nope") is None

    @staticmethod
    def test_restore_conflict_on_name(project_store_dir):
        # setup 走底层 create_project:公开 API create_or_restore_project 现会拦截隐藏项目同名
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, restore_project, ProjectNameConflict,
        )

        proj = create_project("P", "E:\\p")
        proj.hidden = True
        save_project(proj)
        create_project("P", "E:\\p2")
        with pytest.raises(ProjectNameConflict):
            restore_project(proj.project_id)


class TestCrossModeCoexistence:
    """work/code 双模式隔离:同名同路径可在两模式各自独立存在,同模式内仍冲突。"""

    @staticmethod
    def test_cross_mode_coexist_and_same_mode_conflict(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_or_restore_project, list_projects,
            ProjectDirConflict, ProjectNameConflict,
        )

        # 同名同路径、不同 work_mode → 两个独立项目
        proj_work, restored_w = create_or_restore_project("App", "E:\\proj", work_mode="work")
        proj_code, restored_c = create_or_restore_project("App", "E:\\proj", work_mode="code")
        assert restored_w is False and restored_c is False
        assert proj_work.project_id != proj_code.project_id
        ids = {p.project_id for p in list_projects()}
        assert proj_work.project_id in ids and proj_code.project_id in ids

        # 同模式同路径 → 冲突;同模式同名不同路径 → 冲突
        with pytest.raises(ProjectDirConflict):
            create_or_restore_project("P2", "E:\\proj", work_mode="work")
        create_or_restore_project("P", "E:\\d1", work_mode="work")
        with pytest.raises(ProjectNameConflict):
            create_or_restore_project("P", "E:\\d2", work_mode="work")

    @staticmethod
    def test_mode_isolation_for_get_rename_hide_restore(project_store_dir):
        """按 (dir, mode) 查询 / 重命名 / 隐藏恢复 均不跨模式影响。"""
        from jiuwenswarm.server.runtime.session.project_store import (
            create_or_restore_project, get_project_by_dir_and_mode,
            rename_project, get_project_by_id, hide_project, restore_project,
            save_project, create_project, ProjectNameConflict,
        )

        proj_work, _ = create_or_restore_project("App", "E:\\proj", work_mode="work")
        proj_code, _ = create_or_restore_project("App", "E:\\proj", work_mode="code")

        # get_by_dir_and_mode 各自命中
        assert get_project_by_dir_and_mode("E:\\proj", "work").project_id == proj_work.project_id
        assert get_project_by_dir_and_mode("E:\\proj", "code").project_id == proj_code.project_id

        # rename 跨模式不冲突:work 已有 "AppB",code 改名 "AppB" 应成功
        create_or_restore_project("AppB", "E:\\wb", work_mode="work")
        assert rename_project(proj_code.project_id, "AppB") is not None
        # 同模式内冲突:再创建一个 code 项目,改名为 "AppB" → 冲突
        proj_code_c, _ = create_or_restore_project("AppC", "E:\\cc", work_mode="code")
        with pytest.raises(ProjectNameConflict):
            rename_project(proj_code_c.project_id, "AppB")

        # hide work 不影响 code;restore work 不波及 code
        hide_project(proj_work.project_id)
        assert get_project_by_id(proj_work.project_id, cache_bust=True).hidden is True
        assert get_project_by_id(proj_code.project_id, cache_bust=True).hidden is False
        restore_project(proj_work.project_id)
        assert get_project_by_id(proj_work.project_id, cache_bust=True).hidden is False

        # create_or_restore 不跨模式恢复:hidden 的 work 项目不阻碍 code 创建
        work_hidden = create_project("AppX", "E:\\projx")
        work_hidden.work_mode = "work"
        work_hidden.hidden = True
        save_project(work_hidden)
        proj_code_x, restored = create_or_restore_project("AppX", "E:\\projx", work_mode="code")
        assert restored is False
        assert proj_code_x.project_id != work_hidden.project_id
        assert get_project_by_id(work_hidden.project_id, cache_bust=True).hidden is True

    @staticmethod
    def test_cron_resolve_project_dir_mode_aware(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_or_restore_project, resolve_cron_project_id,
        )

        # 用真实绝对路径,确保 resolve_cron_project_id 的 isabs 校验跨平台通过
        proj_dir = str(project_store_dir.parent / "proj")
        proj_work, _ = create_or_restore_project("App", proj_dir, work_mode="work")
        proj_code, _ = create_or_restore_project("App", proj_dir, work_mode="code")

        assert resolve_cron_project_id(proj_dir, work_mode="work") == proj_work.project_id
        assert resolve_cron_project_id(proj_dir, work_mode="code") == proj_code.project_id
