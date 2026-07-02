"""session_metadata 模块单元测试"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: mock get_agent_sessions_dir 指向 tmp_path
# ---------------------------------------------------------------------------
@pytest.fixture()
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_agent_sessions_dir",
        lambda: d,
    )
    # 清空内存缓存，避免跨用例污染（不同用例可能复用同一 session_id）
    from jiuwenswarm.server.runtime.session.session_metadata import _METADATA_CACHE
    _METADATA_CACHE.clear()
    return d



def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# _auto_title
# ===========================================================================
class TestAutoTitle:
    @staticmethod
    def test_normal():
        from jiuwenswarm.server.runtime.session.session_metadata import _auto_title

        assert _auto_title("hello world") == "hello world"

    @staticmethod
    def test_truncate():
        from jiuwenswarm.server.runtime.session.session_metadata import _auto_title, _TITLE_MAX_LEN

        long_text = "a" * 100
        result = _auto_title(long_text)
        assert len(result) == _TITLE_MAX_LEN + 3  # +3 for "..."
        assert result.endswith("...")

    @staticmethod
    def test_strip_and_newline():
        from jiuwenswarm.server.runtime.session.session_metadata import _auto_title

        assert _auto_title("  line1\nline2  ") == "line1 line2"

    @staticmethod
    def test_empty():
        from jiuwenswarm.server.runtime.session.session_metadata import _auto_title

        assert _auto_title("") == ""
        assert _auto_title("   ") == ""


# ===========================================================================
# init_session_metadata
# ===========================================================================
class TestInitSessionMetadata:
    @staticmethod
    def test_creates_metadata_file(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import init_session_metadata

        init_session_metadata(
            session_id="sess_001",
            channel_id="web",
            user_id="user_1",
            title="test title",
        )
        meta_path = sessions_dir / "sess_001" / "metadata.json"
        assert meta_path.exists()

        data = _read_json(meta_path)
        assert data["session_id"] == "sess_001"
        assert data["channel_id"] == "web"
        assert data["user_id"] == "user_1"
        assert data["title"] == "test title"
        assert data["message_count"] == 0
        assert data["mode"] == "unknown"
        assert data["round_id"] == 0
        assert isinstance(data["created_at"], float)
        assert isinstance(data["last_message_at"], float)

    @staticmethod
    def test_default_empty_fields(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import init_session_metadata

        init_session_metadata(session_id="sess_002")
        data = _read_json(sessions_dir / "sess_002" / "metadata.json")
        assert data["channel_id"] == ""
        assert data["user_id"] == ""
        assert data["title"] == ""
        assert data["mode"] == "unknown"
        assert data["round_id"] == 0

    @staticmethod
    def test_init_new_fields(sessions_dir):
        """init 写入新增字段：project_path / model / last_user_message_at / status"""
        from jiuwenswarm.server.runtime.session.session_metadata import init_session_metadata

        init_session_metadata(
            session_id="sess_new",
            project_path="E:\\myproj",
            model="glm-5",
        )
        data = _read_json(sessions_dir / "sess_new" / "metadata.json")
        assert data["project_path"] == "E:\\myproj"
        assert data["model"] == "glm-5"
        assert data["status"] == "idle"
        assert isinstance(data["last_user_message_at"], float)
        # created_at / last_message_at / last_user_message_at 各自独立取时间戳，
        # 允许微秒级差异，仅断言三者都在创建时刻附近
        assert abs(data["last_user_message_at"] - data["created_at"]) < 1.0

    @staticmethod
    def test_init_new_fields_default_empty(sessions_dir):
        """init 不传新字段时为空默认值"""
        from jiuwenswarm.server.runtime.session.session_metadata import init_session_metadata

        init_session_metadata(session_id="sess_def")
        data = _read_json(sessions_dir / "sess_def" / "metadata.json")
        assert data["project_path"] == ""
        assert data["model"] == ""


# ===========================================================================
# update_session_metadata
# ===========================================================================
class TestUpdateSessionMetadata:
    @staticmethod
    def test_update_existing(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_u1", channel_id="web")

        update_session_metadata(
            session_id="sess_u1",
            channel_id="feishu",
            increment_message_count=True,
        )
        # 等待异步队列写入完成
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_u1" / "metadata.json")
        assert data["channel_id"] == "feishu"
        assert data["message_count"] == 1

    @staticmethod
    def test_fallback_create_when_no_metadata(sessions_dir):
        """外部渠道隐式创建 session 时,metadata 不存在,应自动创建"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        # 不调用 init,直接 update — 模拟外部渠道场景
        (sessions_dir / "sess_ext").mkdir()
        update_session_metadata(
            session_id="sess_ext",
            channel_id="telegram",
            user_id="tg_user",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_ext" / "metadata.json")
        assert data["session_id"] == "sess_ext"
        assert data["channel_id"] == "telegram"
        assert data["user_id"] == "tg_user"
        assert data["message_count"] == 0

    @staticmethod
    def test_auto_title_on_first_user_message(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_at")  # title 为空

        update_session_metadata(
            session_id="sess_at",
            user_content="帮我写一个排序算法",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_at" / "metadata.json")
        assert data["title"] == "帮我写一个排序算法"

    @staticmethod
    def test_no_overwrite_existing_title(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_nt", title="原始标题")

        update_session_metadata(
            session_id="sess_nt",
            user_content="新消息内容",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_nt" / "metadata.json")
        assert data["title"] == "原始标题"  # 不被覆盖

    @staticmethod
    def test_increment_message_count_multiple(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_mc")
        for _ in range(3):
            update_session_metadata(
                session_id="sess_mc", increment_message_count=True
            )
            _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_mc" / "metadata.json")
        assert data["message_count"] == 3

    @staticmethod
    def test_project_path_first_lock_not_overwritten(sessions_dir):
        """project_path 首次锁定后，后续传入不同值不覆盖"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_pp")
        # 首次锁定
        update_session_metadata(session_id="sess_pp", project_path="E:\\projA")
        _METADATA_QUEUE.join()
        # 二次传入不同值
        update_session_metadata(session_id="sess_pp", project_path="E:\\projB")
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_pp" / "metadata.json")
        assert data["project_path"] == "E:\\projA", "project_path 锁定后不可改"

    @staticmethod
    def test_model_overwrites_each_request(sessions_dir):
        """model 覆盖式：每次请求刷新为本次模型"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_m", model="glm-5")
        update_session_metadata(session_id="sess_m", model="glm-5.2")
        _METADATA_QUEUE.join()
        update_session_metadata(session_id="sess_m", model="glm-5.3")
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_m" / "metadata.json")
        assert data["model"] == "glm-5.3", "model 应被最后一次请求覆盖"

    @staticmethod
    def test_last_user_message_at_overwrites_when_passed(sessions_dir):
        """last_user_message_at 覆盖式：传入则刷新，不传(None)则保留旧值"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_lum")
        # 传入时间戳 → 写入
        update_session_metadata(
            session_id="sess_lum",
            last_user_message_at=1000.0,
            user_content="hi",
        )
        _METADATA_QUEUE.join()
        # 不传 last_user_message_at → 保留旧值
        update_session_metadata(session_id="sess_lum")
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_lum" / "metadata.json")
        assert data["last_user_message_at"] == 1000.0, "不传时应保留上次的用户最后输入时间"

    @staticmethod
    def test_update_new_fields_fallback_create(sessions_dir):
        """update 兜底新建分支也写入新字段"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        (sessions_dir / "sess_fb").mkdir()
        update_session_metadata(
            session_id="sess_fb",
            channel_id="web",
            project_path="E:\\fb",
            model="glm-5",
            last_user_message_at=2000.0,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_fb" / "metadata.json")
        assert data["project_path"] == "E:\\fb"
        assert data["model"] == "glm-5"
        assert data["last_user_message_at"] == 2000.0
        assert data["status"] == "idle"


# ===========================================================================
# get_session_metadata
# ===========================================================================
class TestGetSessionMetadata:
    @staticmethod
    def test_returns_data(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="sess_g1", channel_id="web")
        data = get_session_metadata("sess_g1")
        assert data["channel_id"] == "web"

    @staticmethod
    def test_returns_empty_when_missing(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata

        data = get_session_metadata("nonexistent")
        assert data == {}

    @staticmethod
    def test_backfill_new_fields_for_legacy_session(sessions_dir):
        """存量会话（无新字段）读取时 setdefault 兜底，前端拿到稳定 schema"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            _write_metadata_sync,
            get_session_metadata,
        )

        # 模拟旧版本会话：只有老字段，没有 project_path/model/last_user_message_at/status
        _write_metadata_sync("sess_legacy", {
            "session_id": "sess_legacy",
            "channel_id": "web",
            "user_id": "",
            "created_at": 1000.0,
            "last_message_at": 1000.0,
            "title": "old",
            "message_count": 0,
            "mode": "unknown",
            "team_name": "",
            "round_id": 0,
        })
        # 清缓存确保从磁盘读
        from jiuwenswarm.server.runtime.session.session_metadata import _METADATA_CACHE
        _METADATA_CACHE.pop("sess_legacy", None)

        data = get_session_metadata("sess_legacy", cache_bust=True)
        assert data["project_path"] == ""
        assert data["model"] == ""
        assert data["status"] == "idle"
        assert data["last_user_message_at"] == 1000.0  # 回退到 created_at


# ===========================================================================
# increment_session_round_count
# ===========================================================================
class TestIncrementSessionRoundCount:
    @staticmethod
    def test_first_increment_returns_1(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            increment_session_round_count,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_round1")
        result = increment_session_round_count("sess_round1")
        _METADATA_QUEUE.join()

        assert result == 1
        data = _read_json(sessions_dir / "sess_round1" / "metadata.json")
        assert data["round_id"] == 1

    @staticmethod
    def test_increments_sequentially(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            increment_session_round_count,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_round_seq")
        for expected in range(1, 4):
            result = increment_session_round_count("sess_round_seq")
            _METADATA_QUEUE.join()
            assert result == expected

        data = _read_json(sessions_dir / "sess_round_seq" / "metadata.json")
        assert data["round_id"] == 3

    @staticmethod
    def test_defaults_to_0_when_no_metadata(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            increment_session_round_count,
            _METADATA_QUEUE,
        )

        (sessions_dir / "sess_no_meta").mkdir()
        result = increment_session_round_count("sess_no_meta")
        _METADATA_QUEUE.join()

        assert result == 1
        data = _read_json(sessions_dir / "sess_no_meta" / "metadata.json")
        assert data["round_id"] == 1

    @staticmethod
    def test_persists_across_restarts(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            increment_session_round_count,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_persist")
        increment_session_round_count("sess_persist")
        _METADATA_QUEUE.join()

        # Simulate restart: re-import and read from disk
        from jiuwenswarm.server.runtime.session.session_metadata import (
            increment_session_round_count,
        )
        result = increment_session_round_count("sess_persist")
        _METADATA_QUEUE.join()

        assert result == 2
        data = _read_json(sessions_dir / "sess_persist" / "metadata.json")
        assert data["round_id"] == 2


# ===========================================================================
class TestGetAllSessionsMetadata:
    @staticmethod
    def test_basic_list(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_all_sessions_metadata,
        )

        init_session_metadata(session_id="s1", channel_id="web")
        init_session_metadata(session_id="s2", channel_id="feishu")

        sessions, total = get_all_sessions_metadata()
        assert total == 2
        assert len(sessions) == 2
        ids = {s["session_id"] for s in sessions}
        assert ids == {"s1", "s2"}

    @staticmethod
    def test_sorted_by_last_message_at(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            _write_metadata_sync,
            get_all_sessions_metadata,
        )

        now = time.time()
        _write_metadata_sync("old", {
            "session_id": "old", "last_message_at": now - 100,
            "channel_id": "", "user_id": "", "created_at": now - 100,
            "title": "", "message_count": 0, "round_id": 0,
        })
        _write_metadata_sync("new", {
            "session_id": "new", "last_message_at": now,
            "channel_id": "", "user_id": "", "created_at": now,
            "title": "", "message_count": 0, "round_id": 0,
        })

        sessions, _ = get_all_sessions_metadata()
        assert sessions[0]["session_id"] == "new"
        assert sessions[1]["session_id"] == "old"

    @staticmethod
    def test_pagination_limit(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_all_sessions_metadata,
        )

        for i in range(5):
            init_session_metadata(session_id=f"p{i}")

        sessions, total = get_all_sessions_metadata(limit=2)
        assert total == 5
        assert len(sessions) == 2

    @staticmethod
    def test_pagination_offset(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            _write_metadata_sync,
            get_all_sessions_metadata,
        )

        now = time.time()
        for i in range(5):
            _write_metadata_sync(f"o{i}", {
                "session_id": f"o{i}", "last_message_at": now - i,
                "channel_id": "", "user_id": "", "created_at": now - i,
                "title": "", "message_count": 0, "round_id": 0,
            })

        sessions, total = get_all_sessions_metadata(limit=2, offset=2)
        assert total == 5
        assert len(sessions) == 2
        # offset=2 跳过前2个,取第3和第4个(按 last_message_at 倒序)
        assert sessions[0]["session_id"] == "o2"
        assert sessions[1]["session_id"] == "o3"

    @staticmethod
    def test_fallback_for_old_sessions(sessions_dir):
        """没有 metadata.json 的旧会话应用目录时间戳构造最小信息"""
        from jiuwenswarm.server.runtime.session.session_metadata import get_all_sessions_metadata

        (sessions_dir / "legacy_sess").mkdir()
        # 不写 metadata.json

        sessions, total = get_all_sessions_metadata()
        assert total == 1
        assert sessions[0]["session_id"] == "legacy_sess"
        assert sessions[0]["title"] == ""
        assert sessions[0]["mode"] == "unknown"
        assert sessions[0]["created_at"] > 0

    @staticmethod
    def test_empty_dir(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import get_all_sessions_metadata

        sessions, total = get_all_sessions_metadata()
        assert total == 0
        assert sessions == []

    @staticmethod
    def test_excludes_heartbeat_sessions(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_all_sessions_metadata,
        )

        init_session_metadata(session_id="sess_a")
        init_session_metadata(session_id="heartbeat_abc123_deadbeef")
        init_session_metadata(session_id="sess_b")

        sessions, total = get_all_sessions_metadata(limit=20)
        assert total == 2
        ids = {s["session_id"] for s in sessions}
        assert ids == {"sess_a", "sess_b"}
        assert len(sessions) == 2


# ===========================================================================
# _read_metadata 容错
# ===========================================================================
class TestReadMetadataRobustness:
    @staticmethod
    def test_corrupted_json(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata

        d = sessions_dir / "sess_bad"
        d.mkdir()
        (d / "metadata.json").write_text("not valid json", encoding="utf-8")

        data = get_session_metadata("sess_bad")
        assert data == {}

    @staticmethod
    def test_non_dict_json(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata

        d = sessions_dir / "sess_list"
        d.mkdir()
        (d / "metadata.json").write_text("[1,2,3]", encoding="utf-8")

        data = get_session_metadata("sess_list")
        assert data == {}


# ===========================================================================
# channel_metadata
# ===========================================================================
class TestChannelMetadata:
    @staticmethod
    def test_first_request_metadata_stored(sessions_dir):
        """首次请求的 metadata 应写入 channel_metadata"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        update_session_metadata(
            session_id="sess_meta",
            channel_id="web",
            channel_metadata={"traceparent": "00-abc-123-01", "feishu_chat_id": "oc_xxx"},
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_meta" / "metadata.json")
        assert data["channel_metadata"]["traceparent"] == "00-abc-123-01"
        assert data["channel_metadata"]["feishu_chat_id"] == "oc_xxx"

    @staticmethod
    def test_no_overwrite_existing_metadata(sessions_dir):
        """已存在的 channel_metadata 不应被覆盖"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            _write_metadata_sync,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        _write_metadata_sync("sess_no", {
            "session_id": "sess_no",
            "channel_id": "web",
            "user_id": "",
            "created_at": 1000.0,
            "last_message_at": 1000.0,
            "title": "",
            "message_count": 0,
            "round_id": 0,
            "channel_metadata": {"traceparent": "original"},
        })

        update_session_metadata(
            session_id="sess_no",
            channel_metadata={"traceparent": "new_value"},
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_no" / "metadata.json")
        assert data["channel_metadata"]["traceparent"] == "original"  # 未被覆盖

    @staticmethod
    def test_empty_metadata_not_stored(sessions_dir):
        """空 metadata 不写入字段"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        update_session_metadata(
            session_id="sess_empty",
            channel_id="web",
            channel_metadata=None,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_empty" / "metadata.json")
        assert "channel_metadata" not in data

    @staticmethod
    def test_backfill_when_missing(sessions_dir):
        """首次未写入时，后续可补充"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        # 首次不带 metadata
        update_session_metadata(session_id="sess_backfill", channel_id="web")
        _METADATA_QUEUE.join()

        # 二次补充 metadata
        update_session_metadata(
            session_id="sess_backfill",
            channel_metadata={"traceparent": "backfilled"},
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_backfill" / "metadata.json")
        assert data["channel_metadata"]["traceparent"] == "backfilled"


# ===========================================================================
# delivery_context
# ===========================================================================
class TestDeliveryContext:
    @staticmethod
    def test_delivery_context_can_refresh_route_metadata(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            _METADATA_QUEUE,
            get_session_delivery_context,
            set_session_delivery_context,
        )

        set_session_delivery_context(
            session_id="sess_delivery",
            channel_id="feishu",
            source_request_id="req-1",
            route_metadata={"feishu_chat_id": "oc_old"},
        )
        _METADATA_QUEUE.join()

        set_session_delivery_context(
            session_id="sess_delivery",
            channel_id="feishu",
            source_request_id="req-2",
            route_metadata={"feishu_chat_id": "oc_new"},
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_delivery" / "metadata.json")
        context = get_session_delivery_context("sess_delivery")

        assert data["delivery_context"]["source_request_id"] == "req-2"
        assert data["delivery_context"]["route_metadata"]["feishu_chat_id"] == "oc_new"
        assert context is not None
        assert context["channel_id"] == "feishu"
        assert context["route_metadata"]["feishu_chat_id"] == "oc_new"

    @staticmethod
    def test_delivery_context_keeps_previous_route_metadata_when_new_request_has_none(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            _METADATA_QUEUE,
            get_session_delivery_context,
            set_session_delivery_context,
        )

        set_session_delivery_context(
            session_id="sess_delivery_keep",
            channel_id="wecom",
            source_request_id="req-1",
            route_metadata={"conversation_id": "conv-1"},
        )
        _METADATA_QUEUE.join()

        set_session_delivery_context(
            session_id="sess_delivery_keep",
            channel_id="wecom",
            source_request_id="req-2",
            route_metadata=None,
        )
        _METADATA_QUEUE.join()

        context = get_session_delivery_context("sess_delivery_keep")
        assert context is not None
        assert context["source_request_id"] == "req-2"
        assert context["route_metadata"]["conversation_id"] == "conv-1"

    @staticmethod
    def test_build_server_push_message_uses_saved_delivery_context(sessions_dir):
        from jiuwenswarm.server.runtime.session.session_metadata import (
            _METADATA_QUEUE,
            build_server_push_message,
            set_session_delivery_context,
        )

        set_session_delivery_context(
            session_id="sess_push",
            channel_id="telegram",
            source_request_id="req-origin",
            route_metadata={"telegram_chat_id": "chat-1"},
        )
        _METADATA_QUEUE.join()

        push = build_server_push_message(
            session_id="sess_push",
            request_id="push-1",
            payload={"event_type": "chat.ask_user_question"},
            fallback_channel_id="web",
        )

        assert push["channel_id"] == "telegram"
        assert push["session_id"] == "sess_push"
        assert push["metadata"]["telegram_chat_id"] == "chat-1"


# ===========================================================================
# 需求验证: 会话标题稳定性
# ===========================================================================
class TestTitleStability:
    """验证两个核心需求:
    1. 首条用户消息自动生成标题，后续消息不改变
    2. 标题一旦创建就不再变化
    """

    @staticmethod
    def test_req1_first_message_sets_title_second_does_not(sessions_dir):
        """需求1: 首条消息设置标题，第二条消息不改变标题"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        # 模拟 web 前端创建会话(无标题)
        init_session_metadata(session_id="sess_req1")

        # 第一条用户消息
        update_session_metadata(
            session_id="sess_req1",
            channel_id="web",
            increment_message_count=True,
            user_content="第一条消息应该成为标题",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_req1" / "metadata.json")
        assert data["title"] == "第一条消息应该成为标题"

        # 第一条助手回复
        update_session_metadata(
            session_id="sess_req1",
            channel_id="web",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_req1" / "metadata.json")
        assert data["title"] == "第一条消息应该成为标题", "助手回复不应覆盖标题"

        # 第二条用户消息(模拟隔1分钟后)
        update_session_metadata(
            session_id="sess_req1",
            channel_id="web",
            increment_message_count=True,
            user_content="第二条消息不应改变标题",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_req1" / "metadata.json")
        assert data["title"] == "第一条消息应该成为标题", "第二条用户消息不应覆盖标题"
        assert data["message_count"] == 3

    @staticmethod
    def test_req1_rapid_user_then_assistant_no_race(sessions_dir):
        """需求1(竞态): 用户消息和助手消息快速连续到达时，标题不被覆盖"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_race")

        # 模拟真实场景: 用户消息和助手消息不等异步写入就连续调用
        # 不调用 _METADATA_QUEUE.join()，模拟异步写入未完成
        update_session_metadata(
            session_id="sess_race",
            channel_id="web",
            increment_message_count=True,
            user_content="用户的第一条消息",
        )
        # 助手立即回复(不等用户消息的异步写入落盘)
        update_session_metadata(
            session_id="sess_race",
            channel_id="web",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_race" / "metadata.json")
        assert data["title"] == "用户的第一条消息", \
            "竞态条件: 助手消息的异步写入不应覆盖用户消息生成的标题"
        assert data["message_count"] == 2

    @staticmethod
    def test_req2_title_immutable_after_creation(sessions_dir):
        """需求2: 标题一旦创建就不再改变，即使后续多轮对话"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_immut")

        # 第1轮
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
            user_content="最初的标题",
        )
        _METADATA_QUEUE.join()
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        # 第2轮
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
            user_content="第二轮消息",
        )
        _METADATA_QUEUE.join()
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        # 第3轮
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
            user_content="第三轮消息",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_immut" / "metadata.json")
        assert data["title"] == "最初的标题", "多轮对话后标题仍保持不变"
        assert data["message_count"] == 5

    @staticmethod
    def test_req2_explicit_empty_title_does_not_clear(sessions_dir):
        """需求2: 即使传入空字符串 title 参数，也不应清除已有标题"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_noclear", title="已有标题")

        # 模拟某处传入 title=""
        update_session_metadata(
            session_id="sess_noclear",
            title="",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_noclear" / "metadata.json")
        assert data["title"] == "已有标题", "空字符串不应清除已有标题"


# ===========================================================================
# sync_session_request_metadata —— 请求参数 → 会话元数据校验/同步入口
# ===========================================================================
def _drain_queue():
    from jiuwenswarm.server.runtime.session.session_metadata import _METADATA_QUEUE
    _METADATA_QUEUE.join()


class TestSyncSessionRequestMetadata:
    """sync_session_request_metadata：校验请求参数 vs 磁盘 metadata，按字段语义写入。"""

    @staticmethod
    def test_project_path_first_lock_writes_and_returns(sessions_dir):
        """project_path 首次锁定：磁盘为空 → 写入请求值并返回"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1")  # project_path 为空
        effective = sync_session_request_metadata(
            session_id="s1", project_path="E:\\projA"
        )
        _drain_queue()
        assert effective == "E:\\projA"
        assert get_session_metadata("s1")["project_path"] == "E:\\projA"

    @staticmethod
    def test_project_path_locked_ignores_inconsistent_request_value(
        sessions_dir, monkeypatch
    ):
        """已锁定 project_path 时，请求带不同值 → 告警 + 不覆盖 + 返回锁定值"""
        import jiuwenswarm.server.runtime.session.session_metadata as sm
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1")
        sync_session_request_metadata(session_id="s1", project_path="E:\\locked")
        _drain_queue()

        # 拦截 logger.warning，避免依赖 logging propagation
        warnings: list[str] = []
        original_warning = sm.logger.warning

        def _capture_warning(msg, *args, **kwargs):
            warnings.append(msg % args if args else msg)
            original_warning(msg, *args, **kwargs)

        monkeypatch.setattr(sm.logger, "warning", _capture_warning)

        effective = sync_session_request_metadata(
            session_id="s1", project_path="E:\\other"
        )
        _drain_queue()

        assert effective == "E:\\locked", "应返回锁定值而非请求值"
        assert get_session_metadata("s1")["project_path"] == "E:\\locked", "不应被覆盖"
        assert any("已锁定" in w for w in warnings), "应记告警"

    @staticmethod
    def test_project_path_locked_returns_locked_value_when_request_none(sessions_dir):
        """已锁定后，请求不带 project_path → 返回锁定值"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
        )

        init_session_metadata(session_id="s1")
        sync_session_request_metadata(session_id="s1", project_path="E:\\locked")
        _drain_queue()

        effective = sync_session_request_metadata(session_id="s1")  # 不传 project_path
        assert effective == "E:\\locked"

    @staticmethod
    def test_sync_empty_session_id_returns_none(sessions_dir):
        """空 session_id → 直接返回 None，不做任何操作"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            sync_session_request_metadata,
        )
        assert sync_session_request_metadata(session_id="", project_path="E:\\x") is None
        assert sync_session_request_metadata(session_id="   ", project_path="E:\\x") is None

    @staticmethod
    def test_sync_none_project_path_when_unlocked(sessions_dir):
        """未锁定且请求不带 project_path → 返回 None，不写入"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1")
        effective = sync_session_request_metadata(session_id="s1")
        _drain_queue()
        assert effective is None
        assert get_session_metadata("s1")["project_path"] == ""

    @staticmethod
    def test_sync_model_overwritten_each_call(sessions_dir):
        """model：覆盖式，每次请求刷新"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1", model="glm-5")
        sync_session_request_metadata(session_id="s1", model="glm-5.1")
        _drain_queue()
        assert get_session_metadata("s1")["model"] == "glm-5.1"
        sync_session_request_metadata(session_id="s1", model="deepseek-v4")
        _drain_queue()
        assert get_session_metadata("s1")["model"] == "deepseek-v4"

    @staticmethod
    def test_sync_model_none_keeps_existing(sessions_dir):
        """model=None 不更新（保留上次）"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1", model="glm-5")
        sync_session_request_metadata(session_id="s1")  # 不传 model
        _drain_queue()
        assert get_session_metadata("s1")["model"] == "glm-5"

    @staticmethod
    def test_sync_last_user_message_at_overwritten_when_provided(sessions_dir):
        """last_user_message_at：覆盖式，传入则刷新"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1")
        sync_session_request_metadata(session_id="s1", last_user_message_at=1000.0)
        _drain_queue()
        assert get_session_metadata("s1")["last_user_message_at"] == 1000.0
        sync_session_request_metadata(session_id="s1", last_user_message_at=2000.0)
        _drain_queue()
        assert get_session_metadata("s1")["last_user_message_at"] == 2000.0

    @staticmethod
    def test_sync_last_user_message_at_kept_when_not_provided(sessions_dir):
        """last_user_message_at：不传则保留旧值"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1")
        original = get_session_metadata("s1")["last_user_message_at"]
        sync_session_request_metadata(session_id="s1")  # 不传
        _drain_queue()
        assert get_session_metadata("s1")["last_user_message_at"] == original

    @staticmethod
    def test_sync_mode_overwritten(sessions_dir):
        """mode：覆盖式"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            sync_session_request_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="s1", mode="code")
        sync_session_request_metadata(session_id="s1", mode="agent")
        _drain_queue()
        assert get_session_metadata("s1")["mode"] == "agent"

    @staticmethod
    def test_sync_creates_when_missing(sessions_dir):
        """会话元数据不存在 → 兜底新建分支补齐字段"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            sync_session_request_metadata,
            get_session_metadata,
        )

        effective = sync_session_request_metadata(
            session_id="s_new",
            channel_id="web",
            mode="code",
            model="glm-5",
            project_path="E:\\newproj",
            last_user_message_at=1234.0,
        )
        _drain_queue()
        assert effective == "E:\\newproj"
        meta = get_session_metadata("s_new")
        assert meta["project_path"] == "E:\\newproj"
        assert meta["model"] == "glm-5"
        assert meta["mode"] == "code"
        assert meta["last_user_message_at"] == 1234.0
        assert meta["status"] == "idle"

    @staticmethod
    def test_sync_creates_with_defaults_when_minimal(sessions_dir):
        """兜底新建：全默认参数"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            sync_session_request_metadata,
            get_session_metadata,
        )

        effective = sync_session_request_metadata(session_id="s_min")  # 全默认
        _drain_queue()
        assert effective is None  # 无 project_path
        meta = get_session_metadata("s_min")
        assert meta["project_path"] == ""
        assert meta["model"] == ""
        assert meta["mode"] == "unknown"
        assert meta["status"] == "idle"
        assert meta["last_user_message_at"] > 0


# ===========================================================================
# _sync_chat_request_metadata —— AgentServer 进程层薄封装（模块级函数）
# ===========================================================================
@pytest.fixture()
def clean_model_env(monkeypatch):
    """默认 MODEL_NAME 不设，避免环境污染；需要时再 monkeypatch.setenv"""
    monkeypatch.delenv("MODEL_NAME", raising=False)


def _make_agent_request(params=None, metadata=None, session_id="sess_1", channel_id="web"):
    from jiuwenswarm.common.schema.agent import AgentRequest

    return AgentRequest(
        request_id="req-1",
        channel_id=channel_id,
        session_id=session_id,
        params=params or {},
        metadata=metadata,
    )


class TestSyncChatRequestMetadata:
    """_sync_chat_request_metadata：从 AgentRequest 采集参数 + 委托 sync 写盘。

    覆盖 model_name 缺失回退 MODEL_NAME、无 session_id 不写盘、
    异常退化为返回请求候选值、兜底新建等场景。
    """

    @staticmethod
    def test_collects_and_persists(sessions_dir, clean_model_env):
        """正常路径：采集 model_name/project_dir/mode → 写盘 → 返回生效 project_path"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="sess_1")  # project_path 为空
        req = _make_agent_request(
            params={"model_name": "glm-5", "mode": "code", "project_dir": "E:\\projA"},
        )
        effective = _sync_chat_request_metadata(req, "E:\\projA", "code")
        _drain_queue()

        assert effective == "E:\\projA"
        meta = get_session_metadata("sess_1")
        assert meta["model"] == "glm-5"
        assert meta["mode"] == "code"
        assert meta["project_path"] == "E:\\projA"
        assert meta["channel_id"] == "web"
        # last_user_message_at 被刷新为当前时刻
        assert abs(meta["last_user_message_at"] - time.time()) < 5.0

    @staticmethod
    def test_project_dir_passed_through_to_sync(sessions_dir, clean_model_env):
        """project_dir 参数透传给 sync，由 sync 决定锁定/告警"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="sess_1")
        update_session_metadata(session_id="sess_1", project_path="E:\\locked")
        _drain_queue()

        # 请求带不同 project_dir，但磁盘已锁定 → 返回锁定值
        req = _make_agent_request(params={"model_name": "glm-5"})
        effective = _sync_chat_request_metadata(req, "E:\\other", "code")
        _drain_queue()
        assert effective == "E:\\locked"

    @staticmethod
    def test_falls_back_to_env_model_name(sessions_dir, monkeypatch):
        """params 不带 model_name → 用 os.getenv("MODEL_NAME")"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_session_metadata,
        )

        monkeypatch.setenv("MODEL_NAME", "env-glm-5")
        init_session_metadata(session_id="sess_1")
        req = _make_agent_request(params={})  # 不带 model_name
        _sync_chat_request_metadata(req, None, "agent")
        _drain_queue()

        assert get_session_metadata("sess_1")["model"] == "env-glm-5"

    @staticmethod
    def test_empty_model_name_falls_back_to_env(sessions_dir, monkeypatch):
        """params.model_name 为空字符串 → 也回退 env"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_session_metadata,
        )

        monkeypatch.setenv("MODEL_NAME", "env-glm-5")
        init_session_metadata(session_id="sess_1")
        req = _make_agent_request(params={"model_name": "   "})
        _sync_chat_request_metadata(req, None, "agent")
        _drain_queue()

        assert get_session_metadata("sess_1")["model"] == "env-glm-5"

    @staticmethod
    def test_no_model_no_env_keeps_existing(sessions_dir, clean_model_env):
        """params 不带 model_name 且 env 也没设 → model=None → 不覆盖"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="sess_1", model="original-model")
        req = _make_agent_request(params={})
        _sync_chat_request_metadata(req, None, "agent")
        _drain_queue()

        assert get_session_metadata("sess_1")["model"] == "original-model"

    @staticmethod
    def test_no_session_id_returns_project_dir_without_writing(
        sessions_dir, clean_model_env
    ):
        """session_id 为空 → 返回 project_dir，不调 sync（不写盘）"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata

        req = _make_agent_request(
            params={"model_name": "glm-5"}, session_id=None,
        )
        result = _sync_chat_request_metadata(req, "E:\\reqproj", "code")
        assert result == "E:\\reqproj"

    @staticmethod
    def test_empty_session_id_returns_project_dir(sessions_dir, clean_model_env):
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata

        req = _make_agent_request(params={"model_name": "glm-5"}, session_id="   ")
        assert _sync_chat_request_metadata(req, "E:\\p", "code") == "E:\\p"

    @staticmethod
    def test_returns_project_dir_on_sync_failure(
        sessions_dir, clean_model_env, monkeypatch
    ):
        """sync 抛 OSError → 返回 project_dir，不抛"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        import jiuwenswarm.server.runtime.session.session_metadata as sm

        def _boom(**kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(sm, "sync_session_request_metadata", _boom)

        req = _make_agent_request(params={"model_name": "glm-5"}, session_id="sess_1")
        result = _sync_chat_request_metadata(req, "E:\\reqproj", "code")
        assert result == "E:\\reqproj", "异常时应退化为返回请求候选值"

    @staticmethod
    def test_returns_project_dir_on_value_error(
        sessions_dir, clean_model_env, monkeypatch
    ):
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        import jiuwenswarm.server.runtime.session.session_metadata as sm

        def _boom(**kwargs):
            raise ValueError("bad data")

        monkeypatch.setattr(sm, "sync_session_request_metadata", _boom)

        req = _make_agent_request(params={"model_name": "glm-5"}, session_id="sess_1")
        assert _sync_chat_request_metadata(req, "E:\\p", "code") == "E:\\p"

    @staticmethod
    def test_creates_metadata_when_missing(sessions_dir, clean_model_env):
        """不先 init，直接 _sync → 经 sync 兜底新建分支创建"""
        from jiuwenswarm.server.agent_ws_server import _sync_chat_request_metadata
        from jiuwenswarm.server.runtime.session.session_metadata import (
            get_session_metadata,
        )

        req = _make_agent_request(
            params={"model_name": "glm-5", "project_dir": "E:\\newproj"},
            session_id="s_new",
        )
        effective = _sync_chat_request_metadata(req, "E:\\newproj", "code")
        _drain_queue()

        assert effective == "E:\\newproj"
        meta = get_session_metadata("s_new")
        assert meta["model"] == "glm-5"
        assert meta["mode"] == "code"
        assert meta["project_path"] == "E:\\newproj"
        assert meta["status"] == "idle"


# ===========================================================================
# session.get_metadata RPC handler —— Gateway 层只读出口
# ===========================================================================
class _FakeWebChannel:
    """最小 WebChannel 桩，记录 register_method / send_response 调用。"""

    def __init__(self):
        self.methods: dict[str, object] = {}
        self.responses: list[dict] = []

    def register_method(self, name, handler):
        self.methods[name] = handler

    def on_connect(self, handler):
        pass

    async def send_response(self, ws, req_id, *, ok, payload=None, error=None, code=None):
        self.responses.append(
            {
                "id": req_id,
                "ok": ok,
                "payload": payload,
                "error": error,
                "code": code,
            }
        )


@pytest.fixture()
def registered_channel(sessions_dir):
    """注册所有 web handler，返回 _FakeWebChannel（含 session.get_metadata）"""
    from jiuwenswarm.gateway.channel_manager.web.app_web_handlers import (
        WebHandlersBindParams,
        _register_web_handlers,
    )

    channel = _FakeWebChannel()
    _register_web_handlers(
        WebHandlersBindParams(
            channel=channel,
        )
    )
    return channel


async def _call_method(method_table, method, params):
    """调用 handler 并返回最后一个响应"""
    handler = method_table.methods[method]
    await handler(object(), "req-1", params, "sess-caller")
    return method_table.responses[-1]


class TestSessionGetMetadataHandler:
    """session.get_metadata：按 session_id 返回单个会话元数据（只读出口）。"""

    @staticmethod
    @pytest.mark.asyncio
    async def test_returns_metadata_for_existing_session(registered_channel, sessions_dir):
        """存在的会话返回完整 metadata（含新字段）"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(
            session_id="sess_x",
            channel_id="web",
            project_path="E:\\myproj",
            model="glm-5",
        )
        update_session_metadata(
            session_id="sess_x",
            mode="agent.plan",
            model="glm-5",
            project_path="E:\\myproj",
            last_user_message_at=1234.0,
        )
        _METADATA_QUEUE.join()

        resp = await _call_method(
            registered_channel, "session.get_metadata", {"session_id": "sess_x"}
        )

        assert resp["ok"] is True
        payload = resp["payload"]
        assert payload["session_id"] == "sess_x"
        assert payload["mode"] == "agent.plan"
        assert payload["model"] == "glm-5"
        assert payload["project_path"] == "E:\\myproj"
        assert payload["last_user_message_at"] == 1234.0
        assert payload["status"] == "idle"

    @staticmethod
    @pytest.mark.asyncio
    async def test_missing_session_id_returns_bad_request(registered_channel):
        """session_id 缺失 → BAD_REQUEST"""
        resp = await _call_method(
            registered_channel, "session.get_metadata", {"session_id": ""}
        )
        assert resp["ok"] is False
        assert resp["code"] == "BAD_REQUEST"

        # params 不是 dict
        resp2 = await _call_method(registered_channel, "session.get_metadata", None)
        assert resp2["ok"] is False
        assert resp2["code"] == "BAD_REQUEST"

    @staticmethod
    @pytest.mark.asyncio
    async def test_nonexistent_session_returns_not_found(registered_channel):
        """不存在的会话 → NOT_FOUND"""
        resp = await _call_method(
            registered_channel, "session.get_metadata", {"session_id": "no_such_session"}
        )
        assert resp["ok"] is False
        assert resp["code"] == "NOT_FOUND"

    @staticmethod
    @pytest.mark.asyncio
    async def test_method_registered(registered_channel):
        """handler 已注册为 session.get_metadata"""
        assert "session.get_metadata" in registered_channel.methods

    @staticmethod
    @pytest.mark.asyncio
    async def test_single_session_isolation(registered_channel, sessions_dir):
        """单会话隔离：A 会话的查询不返回 B 会话的数据"""
        from jiuwenswarm.server.runtime.session.session_metadata import (
            init_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_A", model="modelA", project_path="E:\\A")
        init_session_metadata(session_id="sess_B", model="modelB", project_path="E:\\B")
        _METADATA_QUEUE.join()

        resp_a = await _call_method(
            registered_channel, "session.get_metadata", {"session_id": "sess_A"}
        )
        resp_b = await _call_method(
            registered_channel, "session.get_metadata", {"session_id": "sess_B"}
        )

        assert resp_a["payload"]["model"] == "modelA"
        assert resp_a["payload"]["project_path"] == "E:\\A"
        assert resp_b["payload"]["model"] == "modelB"
        assert resp_b["payload"]["project_path"] == "E:\\B"
