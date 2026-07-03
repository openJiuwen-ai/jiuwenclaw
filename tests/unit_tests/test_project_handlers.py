# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""项目接口 handler 单元测试 — project.list / get_sessions / create /
remove / restore / pinned_sessions + session.pin + 兼容性(session.create / rename)。

复用 test_session_metadata.py 的 _FakeWebChannel 桩模式,自包含 fixtures。
"""
from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
class _FakeWebChannel:
    channel_id = "test-web"

    def __init__(self):
        self.methods: dict[str, object] = {}
        self.responses: list[dict] = []

    def register_method(self, name, handler):
        self.methods[name] = handler

    def on_connect(self, handler):
        pass

    async def send_response(self, ws, req_id, *, ok, payload=None, error=None, code=None):
        self.responses.append(
            {"id": req_id, "ok": ok, "payload": payload, "error": error, "code": code}
        )


@pytest.fixture()
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_agent_sessions_dir",
        lambda: d,
    )
    # _session_create 等 handler 直接引用 app_web_handlers 模块内导入的副本,需一并 patch
    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.get_agent_sessions_dir",
        lambda: d,
    )
    from jiuwenswarm.server.runtime.session.session_metadata import _METADATA_CACHE
    _METADATA_CACHE.clear()
    return d


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


@pytest.fixture()
def registered_channel(sessions_dir, project_store_dir):
    from jiuwenswarm.gateway.channel_manager.web.app_web_handlers import (
        WebHandlersBindParams,
        _register_web_handlers,
    )
    channel = _FakeWebChannel()
    _register_web_handlers(WebHandlersBindParams(channel=channel))
    return channel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _call(channel, method, params, sid="sess-caller"):
    handler = channel.methods[method]
    await handler(object(), "req-1", params, sid)
    return channel.responses[-1]


def _drain():
    from jiuwenswarm.server.runtime.session.session_metadata import _METADATA_QUEUE
    _METADATA_QUEUE.join()


def _make_session(sid, *, project_path="", pinned=False, pin_order=0, last_user_message_at=None, model=""):
    """创建一个会话并写入指定元数据,flush 队列确保落盘。"""
    from jiuwenswarm.server.runtime.session.session_metadata import (
        init_session_metadata, update_session_metadata,
    )
    init_session_metadata(session_id=sid, project_path=project_path, model=model)
    if pinned or pin_order:
        update_session_metadata(session_id=sid, pinned=pinned, pin_order=pin_order)
    if last_user_message_at is not None:
        update_session_metadata(session_id=sid, last_user_message_at=last_user_message_at)
    _drain()


def _make_project(name, project_path, *, pinned=False, pin_order=0, hidden=False):
    from jiuwenswarm.server.runtime.session.project_store import (
        create_project, save_project,
    )
    proj = create_project(name, project_path)
    if pinned or pin_order:
        proj.pinned = pinned
        proj.pin_order = pin_order
    if hidden:
        proj.hidden = True
    save_project(proj)
    return proj


def _abspath(tmp_path, name):
    """平台无关的绝对路径,用于 project.create 的 isabs 校验。"""
    return str(tmp_path / name)


# ===========================================================================
# project.list
# ===========================================================================
class TestProjectList:
    @staticmethod
    @pytest.mark.asyncio
    async def test_filter_all_sorting_default_last(registered_channel, tmp_path):
        """all: 置顶在前 → 非置顶按 last_user_message_at 倒序 → 默认末位。"""
        pa = _abspath(tmp_path, "app")
        pb = _abspath(tmp_path, "backend")
        p_pinned = _make_project("置顶项目", pa, pinned=True, pin_order=1)
        p_normal = _make_project("普通项目", pb)
        # 普通项目下 1 个会话;默认项目下 1 个会话
        _make_session("s1", project_path=pb, last_user_message_at=1000.0)
        _make_session("s2", project_path="", last_user_message_at=2000.0)

        resp = await _call(registered_channel, "project.list", {"filter": "all"})
        assert resp["ok"] is True
        projects = resp["payload"]["projects"]
        ids = [p["project_id"] for p in projects]

        assert ids[0] == p_pinned.project_id  # 置顶在前
        assert ids[1] == p_normal.project_id  # 非置顶
        assert ids[-1] == "default"  # 默认项目末位
        # 统计:普通项目 1 个非置顶会话,默认 1 个
        normal_info = next(p for p in projects if p["project_id"] == p_normal.project_id)
        assert normal_info["session_count"] == 1
        default_info = next(p for p in projects if p["project_id"] == "default")
        assert default_info["session_count"] == 1
        assert default_info["is_default"] is True

    @staticmethod
    @pytest.mark.asyncio
    async def test_filter_pinned_only(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        pb = _abspath(tmp_path, "backend")
        _make_project("置顶", pa, pinned=True, pin_order=1)
        _make_project("普通", pb)
        resp = await _call(registered_channel, "project.list", {"filter": "pinned"})
        projects = resp["payload"]["projects"]
        assert len(projects) == 1
        assert projects[0]["pinned"] is True

    @staticmethod
    @pytest.mark.asyncio
    async def test_filter_unpinned_includes_default_last(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        _make_project("置顶", pa, pinned=True, pin_order=1)
        _make_project("普通", _abspath(tmp_path, "backend"))
        resp = await _call(registered_channel, "project.list", {"filter": "unpinned"})
        projects = resp["payload"]["projects"]
        assert all(not p["pinned"] for p in projects)
        assert projects[-1]["project_id"] == "default"
        assert not any(p["pinned"] for p in projects)

    @staticmethod
    @pytest.mark.asyncio
    async def test_include_hidden(registered_channel, tmp_path):
        ph = _abspath(tmp_path, "hidden")
        _make_project("隐藏项目", ph, hidden=True)
        resp = await _call(registered_channel, "project.list", {"filter": "all"})
        # 默认不含隐藏
        assert all(not p["hidden"] or p["is_default"] for p in resp["payload"]["projects"])
        resp2 = await _call(
            registered_channel, "project.list", {"filter": "all", "include_hidden": True}
        )
        assert any(p["hidden"] for p in resp2["payload"]["projects"])

    @staticmethod
    @pytest.mark.asyncio
    async def test_pinned_sessions_not_counted(registered_channel, tmp_path):
        """置顶会话不计入任何项目 session_count。"""
        pa = _abspath(tmp_path, "app")
        _make_project("P", pa)
        _make_session("s_normal", project_path=pa, last_user_message_at=100.0)
        _make_session("s_pinned", project_path=pa, pinned=True, pin_order=1, last_user_message_at=200.0)
        resp = await _call(registered_channel, "project.list", {"filter": "all"})
        p_info = next(p for p in resp["payload"]["projects"] if p["project_path"] == pa)
        assert p_info["session_count"] == 1  # 仅非置顶


# ===========================================================================
# project.get_sessions
# ===========================================================================
class TestProjectGetSessions:
    @staticmethod
    @pytest.mark.asyncio
    async def test_returns_non_pinned_sorted_desc(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        proj = _make_project("P", pa)
        _make_session("s1", project_path=pa, last_user_message_at=100.0)
        _make_session("s2", project_path=pa, last_user_message_at=300.0)
        _make_session("s3", project_path=pa, last_user_message_at=200.0)
        _make_session("s_pinned", project_path=pa, pinned=True, pin_order=1, last_user_message_at=999.0)

        resp = await _call(
            registered_channel, "project.get_sessions", {"project_id": proj.project_id}
        )
        assert resp["ok"] is True
        sessions = resp["payload"]["sessions"]
        ids = [s["session_id"] for s in sessions]
        # 倒序: s2(300) > s3(200) > s1(100); 置顶 s_pinned 不出现
        assert ids == ["s2", "s3", "s1"]
        assert "s_pinned" not in ids
        assert resp["payload"]["total"] == 3

    @staticmethod
    @pytest.mark.asyncio
    async def test_pagination(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        proj = _make_project("P", pa)
        for i in range(5):
            _make_session(f"s{i}", project_path=pa, last_user_message_at=float(i))

        resp = await _call(
            registered_channel, "project.get_sessions",
            {"project_id": proj.project_id, "limit": 2, "offset": 1},
        )
        sessions = resp["payload"]["sessions"]
        assert len(sessions) == 2
        assert resp["payload"]["total"] == 5  # 截断前全量
        # offset=1 跳过最新(4),取 3,2
        assert sessions[0]["session_id"] == "s3"
        assert sessions[1]["session_id"] == "s2"

    @staticmethod
    @pytest.mark.asyncio
    async def test_default_includes_hidden_project_sessions(registered_channel, tmp_path):
        """default: 含隐藏项目的非置顶会话(临时归属默认)。"""
        ph = _abspath(tmp_path, "hidden")
        _make_project("隐藏", ph, hidden=True)
        _make_session("s_hidden", project_path=ph, last_user_message_at=100.0)
        _make_session("s_default", project_path="", last_user_message_at=200.0)

        resp = await _call(
            registered_channel, "project.get_sessions", {"project_id": "default"}
        )
        ids = [s["session_id"] for s in resp["payload"]["sessions"]]
        assert "s_hidden" in ids  # 隐藏项目会话归默认
        assert "s_default" in ids

    @staticmethod
    @pytest.mark.asyncio
    async def test_not_found_for_hidden_project(registered_channel, tmp_path):
        ph = _abspath(tmp_path, "hidden")
        proj = _make_project("隐藏", ph, hidden=True)
        resp = await _call(
            registered_channel, "project.get_sessions", {"project_id": proj.project_id}
        )
        assert resp["ok"] is False
        assert resp["code"] == "NOT_FOUND"

    @staticmethod
    @pytest.mark.asyncio
    async def test_missing_project_id_bad_request(registered_channel):
        resp = await _call(registered_channel, "project.get_sessions", {"project_id": ""})
        assert resp["code"] == "BAD_REQUEST"


# ===========================================================================
# project.create
# ===========================================================================
class TestProjectCreate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_create_new(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "myapp")
        resp = await _call(
            registered_channel, "project.create", {"name": "我的应用", "project_path": pa}
        )
        assert resp["ok"] is True
        assert resp["payload"]["project_id"].startswith("proj_")
        assert resp["payload"]["restored"] is False

    @staticmethod
    @pytest.mark.asyncio
    async def test_conflict_on_visible_duplicate(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "dup")
        _make_project("P1", pa)
        resp = await _call(
            registered_channel, "project.create", {"name": "P2", "project_path": pa}
        )
        assert resp["ok"] is False
        assert resp["code"] == "CONFLICT"

    @staticmethod
    @pytest.mark.asyncio
    async def test_auto_restore_on_hidden(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "restored")
        existing = _make_project("旧名", pa, hidden=True)
        resp = await _call(
            registered_channel, "project.create", {"name": "新名", "project_path": pa}
        )
        assert resp["ok"] is True
        assert resp["payload"]["restored"] is True
        assert resp["payload"]["project_id"] == existing.project_id
        # 恢复后可见 + 名字更新
        from jiuwenswarm.server.runtime.session.project_store import get_project_by_id
        proj = get_project_by_id(existing.project_id, cache_bust=True)
        assert proj.hidden is False
        assert proj.name == "新名"

    @staticmethod
    @pytest.mark.asyncio
    async def test_bad_request_non_absolute_path(registered_channel):
        resp = await _call(
            registered_channel, "project.create", {"name": "P", "project_path": "relative/path"}
        )
        assert resp["code"] == "BAD_REQUEST"

    @staticmethod
    @pytest.mark.asyncio
    async def test_bad_request_empty_name(registered_channel, tmp_path):
        resp = await _call(
            registered_channel, "project.create",
            {"name": "", "project_path": _abspath(tmp_path, "x")},
        )
        assert resp["code"] == "BAD_REQUEST"


# ===========================================================================
# project.remove / project.restore
# ===========================================================================
class TestProjectRemoveRestore:
    @staticmethod
    @pytest.mark.asyncio
    async def test_remove_returns_affected_and_soft_deletes(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        proj = _make_project("P", pa)
        _make_session("s1", project_path=pa, last_user_message_at=100.0)
        _make_session("s2", project_path=pa, last_user_message_at=200.0)
        _make_session("s_pin", project_path=pa, pinned=True, pin_order=1, last_user_message_at=300.0)

        resp = await _call(
            registered_channel, "project.remove", {"project_id": proj.project_id}
        )
        assert resp["ok"] is True
        assert resp["payload"]["affected_sessions"] == 2  # 仅非置顶

        # 软删除后 get_sessions 返回 NOT_FOUND
        resp2 = await _call(
            registered_channel, "project.get_sessions", {"project_id": proj.project_id}
        )
        assert resp2["code"] == "NOT_FOUND"

        # 非置顶会话临时归入默认
        resp3 = await _call(
            registered_channel, "project.get_sessions", {"project_id": "default"}
        )
        ids = [s["session_id"] for s in resp3["payload"]["sessions"]]
        assert "s1" in ids and "s2" in ids

    @staticmethod
    @pytest.mark.asyncio
    async def test_remove_idempotent_on_hidden(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        proj = _make_project("P", pa, hidden=True)
        resp = await _call(
            registered_channel, "project.remove", {"project_id": proj.project_id}
        )
        assert resp["ok"] is True
        assert resp["payload"]["affected_sessions"] == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_remove_default_forbidden(registered_channel):
        resp = await _call(registered_channel, "project.remove", {"project_id": "default"})
        assert resp["code"] == "FORBIDDEN"

    @staticmethod
    @pytest.mark.asyncio
    async def test_restore_reattributes_sessions(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        proj = _make_project("P", pa)
        _make_session("s1", project_path=pa, last_user_message_at=100.0)
        _make_session("s2", project_path=pa, last_user_message_at=200.0)
        # 先移除
        await _call(registered_channel, "project.remove", {"project_id": proj.project_id})
        # 恢复
        resp = await _call(
            registered_channel, "project.restore", {"project_id": proj.project_id}
        )
        assert resp["ok"] is True
        assert resp["payload"]["affected_sessions"] == 2

        # 恢复后会话重新归属该项目
        resp2 = await _call(
            registered_channel, "project.get_sessions", {"project_id": proj.project_id}
        )
        ids = [s["session_id"] for s in resp2["payload"]["sessions"]]
        assert sorted(ids) == ["s1", "s2"]

    @staticmethod
    @pytest.mark.asyncio
    async def test_restore_conflict_on_visible(registered_channel, tmp_path):
        pa = _abspath(tmp_path, "app")
        proj = _make_project("P", pa)  # 可见
        resp = await _call(
            registered_channel, "project.restore", {"project_id": proj.project_id}
        )
        assert resp["code"] == "CONFLICT"

    @staticmethod
    @pytest.mark.asyncio
    async def test_restore_default_forbidden(registered_channel):
        resp = await _call(registered_channel, "project.restore", {"project_id": "default"})
        assert resp["code"] == "FORBIDDEN"


# ===========================================================================
# session.pin + project.pinned_sessions
# ===========================================================================
class TestSessionPin:
    @staticmethod
    @pytest.mark.asyncio
    async def test_pin_and_unpin_idempotent(registered_channel, sessions_dir):
        _make_session("s1", last_user_message_at=100.0)
        # 置顶
        resp = await _call(registered_channel, "session.pin", {"session_id": "s1", "pinned": True})
        _drain()
        assert resp["ok"] is True
        assert resp["payload"]["pinned"] is True
        assert resp["payload"]["pin_order"] == 1
        # 再次置顶(幂等)
        resp2 = await _call(registered_channel, "session.pin", {"session_id": "s1", "pinned": True})
        _drain()
        assert resp2["payload"]["pin_order"] == 1
        # 取消
        resp3 = await _call(registered_channel, "session.pin", {"session_id": "s1", "pinned": False})
        _drain()
        assert resp3["payload"]["pinned"] is False
        assert resp3["payload"]["pin_order"] == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_pin_reindex_compact(registered_channel, sessions_dir):
        _make_session("s1", last_user_message_at=100.0)
        _make_session("s2", last_user_message_at=200.0)
        _make_session("s3", last_user_message_at=300.0)
        await _call(registered_channel, "session.pin", {"session_id": "s1", "pinned": True})
        _drain()
        await _call(registered_channel, "session.pin", {"session_id": "s2", "pinned": True})
        _drain()
        await _call(registered_channel, "session.pin", {"session_id": "s3", "pinned": True})
        _drain()
        # 取消 s2 → s3,s1 重编号为 1,2(新置顶在最前: s3 最先 pin_order 最小)
        await _call(registered_channel, "session.pin", {"session_id": "s2", "pinned": False})
        _drain()
        resp = await _call(registered_channel, "project.pinned_sessions", {})
        sessions = resp["payload"]["sessions"]
        assert [s["session_id"] for s in sessions] == ["s3", "s1"]
        assert [s["pin_order"] for s in sessions] == [1, 2]

    @staticmethod
    @pytest.mark.asyncio
    async def test_pinned_excluded_from_get_sessions(registered_channel, tmp_path):
        """置顶会话从 get_sessions 消失,出现在 pinned_sessions。"""
        pa = _abspath(tmp_path, "app")
        proj = _make_project("P", pa)
        _make_session("s_normal", project_path=pa, last_user_message_at=100.0)
        _make_session("s_pin", project_path=pa, last_user_message_at=200.0)
        await _call(registered_channel, "session.pin", {"session_id": "s_pin", "pinned": True})
        _drain()

        resp_gs = await _call(
            registered_channel, "project.get_sessions", {"project_id": proj.project_id}
        )
        ids = [s["session_id"] for s in resp_gs["payload"]["sessions"]]
        assert "s_pin" not in ids
        assert "s_normal" in ids

        resp_pin = await _call(registered_channel, "project.pinned_sessions", {})
        pin_ids = [s["session_id"] for s in resp_pin["payload"]["sessions"]]
        assert "s_pin" in pin_ids

    @staticmethod
    @pytest.mark.asyncio
    async def test_pin_not_found(registered_channel):
        resp = await _call(registered_channel, "session.pin", {"session_id": "nope", "pinned": True})
        assert resp["code"] == "NOT_FOUND"

    @staticmethod
    @pytest.mark.asyncio
    async def test_pin_does_not_corrupt_last_message_at(registered_channel, sessions_dir):
        """置顶/取消置顶不应改变任何会话的 last_message_at(置顶不是消息)。

        回归:set_session_pinned 曾走 update_session_metadata,其末尾无条件刷新
        last_message_at,导致每次 pin/unpin 把所有置顶会话的最后消息时间腐蚀为当前时刻。
        """
        from jiuwenswarm.server.runtime.session.session_metadata import (
            get_session_metadata,
        )

        _make_session("s1", last_user_message_at=1000.0)
        _make_session("s2", last_user_message_at=2000.0)
        before_s1 = get_session_metadata("s1", cache_bust=True).get("last_message_at")
        before_s2 = get_session_metadata("s2", cache_bust=True).get("last_message_at")
        assert before_s1 is not None and before_s2 is not None

        # 置顶 s1 → 重编号(仅 s1),不应触碰 last_message_at
        await _call(registered_channel, "session.pin", {"session_id": "s1", "pinned": True})
        _drain()
        assert get_session_metadata("s1", cache_bust=True).get("last_message_at") == before_s1

        # 再置顶 s2 → 重编号(s1,s2),两个会话的 last_message_at 都应保持不变
        await _call(registered_channel, "session.pin", {"session_id": "s2", "pinned": True})
        _drain()
        assert get_session_metadata("s1", cache_bust=True).get("last_message_at") == before_s1
        assert get_session_metadata("s2", cache_bust=True).get("last_message_at") == before_s2

        # 取消 s1 → 重编号(仅 s2),last_message_at 仍不变
        await _call(registered_channel, "session.pin", {"session_id": "s1", "pinned": False})
        _drain()
        assert get_session_metadata("s1", cache_bust=True).get("last_message_at") == before_s1
        assert get_session_metadata("s2", cache_bust=True).get("last_message_at") == before_s2


# ===========================================================================
# 兼容性: 旧 session.create 不传 project_path + session.rename
# ===========================================================================
class TestCompat:
    @staticmethod
    @pytest.mark.asyncio
    async def test_session_create_without_project_path(registered_channel, sessions_dir):
        """不传 project_path → 归入默认项目,project_path="" 兜底,行为不变。"""
        resp = await _call(
            registered_channel, "session.create",
            {"session_id": "sess_compat_1", "title": "兼容", "mode": "code.normal"},
        )
        assert resp["ok"] is True
        assert resp["payload"]["session_id"] == "sess_compat_1"
        # metadata 中 project_path 为空
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata
        meta = get_session_metadata("sess_compat_1", cache_bust=True)
        assert meta["project_path"] == ""
        # 该会话出现在默认项目
        resp2 = await _call(
            registered_channel, "project.get_sessions", {"project_id": "default"}
        )
        ids = [s["session_id"] for s in resp2["payload"]["sessions"]]
        assert "sess_compat_1" in ids

    @staticmethod
    @pytest.mark.asyncio
    async def test_session_create_already_exists(registered_channel, sessions_dir):
        await _call(
            registered_channel, "session.create", {"session_id": "sess_dup"}
        )
        resp = await _call(
            registered_channel, "session.create", {"session_id": "sess_dup"}
        )
        assert resp["code"] == "ALREADY_EXISTS"

    @staticmethod
    @pytest.mark.asyncio
    async def test_session_rename_query_clear_set(registered_channel, sessions_dir):
        """session.rename 三种语义:查询/清除/设置。"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata, _METADATA_QUEUE,
        )
        init_session_metadata(session_id="sess_rn", title="原标题")
        _METADATA_QUEUE.join()

        # 查询(不传 title)
        resp = await _call(
            registered_channel, "session.rename", {"session_id": "sess_rn"}
        )
        assert resp["payload"]["title"] == "原标题"
        assert resp["payload"]["previous_title"] == "原标题"

        # 设置
        resp2 = await _call(
            registered_channel, "session.rename",
            {"session_id": "sess_rn", "title": "新标题"},
        )
        _drain()
        assert resp2["payload"]["title"] == "新标题"
        assert resp2["payload"]["previous_title"] == "原标题"

        # 清除(空串)
        resp3 = await _call(
            registered_channel, "session.rename",
            {"session_id": "sess_rn", "title": "   "},
        )
        _drain()
        assert resp3["payload"]["title"] == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_session_rename_missing_id_bad_request(registered_channel):
        # apply_session_rename 用 params.session_id 回退到 connection session_id,
        # 两者皆空时才 BAD_REQUEST
        resp = await _call(registered_channel, "session.rename", {}, sid="")
        assert resp["code"] == "BAD_REQUEST"
