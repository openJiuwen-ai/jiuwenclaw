# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""project_store 模块单元测试 — projects.json 的持久化与 CRUD。

覆盖: 新建/按 ID 查找/按 project_path 查找/更新/列表、隐藏恢复、pin 紧凑重编号。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture: mock get_agent_root_dir 指向 tmp_path,清空 project_store 内存缓存
# ---------------------------------------------------------------------------
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


# ===========================================================================
# CRUD: 新建 / 按 ID 查找 / 按 project_path 查找
# ===========================================================================
class TestCreateProject:
    @staticmethod
    def test_create_persists_and_get_by_id(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, get_project_by_id,
        )

        proj = create_project("我的应用", "E:\\projA")
        assert proj.project_id.startswith("proj_")
        assert proj.name == "我的应用"
        assert proj.project_path == "E:\\projA"
        assert proj.hidden is False
        assert proj.pinned is False
        assert proj.pin_order == 0

        # 落盘
        records = _read_projects(project_store_dir)
        assert len(records) == 1
        assert records[0]["project_id"] == proj.project_id

        # 按 ID 查找
        found = get_project_by_id(proj.project_id, cache_bust=True)
        assert found is not None
        assert found.name == "我的应用"

    @staticmethod
    def test_get_by_id_returns_none_for_missing(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import get_project_by_id

        assert get_project_by_id("proj_nope", cache_bust=True) is None

    @staticmethod
    def test_get_by_path_finds_hidden_and_visible(project_store_dir):
        """get_project_by_path 不限 hidden 状态,由调用方判断。"""
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, get_project_by_path,
        )

        proj = create_project("P1", "E:\\path1")
        proj.hidden = True
        save_project(proj)

        found = get_project_by_path("E:\\path1", cache_bust=True)
        assert found is not None
        assert found.hidden is True
        assert found.project_id == proj.project_id

    @staticmethod
    def test_get_by_path_returns_none_for_missing(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import get_project_by_path

        assert get_project_by_path("E:\\nope", cache_bust=True) is None


# ===========================================================================
# 更新 (save_project upsert)
# ===========================================================================
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

        proj = Project(project_id="proj_manual", name="M", project_path="E:\\m")
        save_project(proj)

        found = get_project_by_id("proj_manual", cache_bust=True)
        assert found is not None
        assert found.name == "M"
        assert found.project_path == "E:\\m"


# ===========================================================================
# 列表 + 隐藏过滤
# ===========================================================================
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


# ===========================================================================
# 隐藏恢复: hidden=true 不被默认查询返回;取消 hidden 后恢复可见
# ===========================================================================
class TestHiddenRestore:
    @staticmethod
    def test_hidden_then_restore_visibility(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, list_projects, get_project_by_id,
        )

        proj = create_project("P", "E:\\p")
        # 软删除
        proj.hidden = True
        save_project(proj)
        assert get_project_by_id(proj.project_id, cache_bust=True).hidden is True
        assert proj.project_id not in [p.project_id for p in list_projects(cache_bust=True)]

        # 恢复
        proj.hidden = False
        save_project(proj)
        assert proj.project_id in [p.project_id for p in list_projects(cache_bust=True)]


# ===========================================================================
# pin 紧凑重编号: 反复置顶/取消, pin_order 始终 1..N 无间隙
# ===========================================================================
class TestPinReindex:
    @staticmethod
    def test_reindex_compact_after_unpin(project_store_dir):
        """3 个置顶,取消中间一个 → 剩余 1..2 紧凑。"""
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

        # 取消 p2
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
        """反复置顶/取消后 pin_order 不无限增长,无间隙。"""
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
            # 取消 p1
            p1.pinned = False
            p1.pin_order = 0
            save_project(p1)
            reindex_project_pin_orders()

        all_proj = list_projects(include_hidden=True, cache_bust=True)
        pinned = [p for p in all_proj if p.pinned]
        assert len(pinned) == 1
        assert pinned[0].project_id == p2.project_id
        assert pinned[0].pin_order == 1  # 紧凑,无间隙

    @staticmethod
    def test_reindex_unpinned_gets_zero(project_store_dir):
        from jiuwenswarm.server.runtime.session.project_store import (
            create_project, save_project, reindex_project_pin_orders,
            list_projects,
        )

        p1 = create_project("P1", "E:\\p1")
        p2 = create_project("P2", "E:\\p2")
        # p2 置顶,p1 非置顶但残留 pin_order
        p2.pinned = True
        p2.pin_order = 1
        save_project(p2)
        p1.pin_order = 99  # 残留脏值
        save_project(p1)

        reindex_project_pin_orders()
        all_proj = {p.project_id: p for p in list_projects(include_hidden=True, cache_bust=True)}
        assert all_proj[p1.project_id].pin_order == 0  # 非置顶清零
        assert all_proj[p2.project_id].pin_order == 1
