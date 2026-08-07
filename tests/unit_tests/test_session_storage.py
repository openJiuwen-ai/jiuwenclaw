"""session_storage 模块单元测试（SessionMap 存储抽象层）"""
from __future__ import annotations

import json

import pytest

from jiuwenswarm.gateway.routing.session_map import Session, invoke_service_id


def _sess(session_id: str, *, chat_id: str = "c", bot_id: str = "b", agent_id: str | None = None) -> Session:
    return Session(
        session_id=session_id,
        service_id=invoke_service_id(chat_id, bot_id),
        agent_id=agent_id,
    )


# ---------------------------------------------------------------------------
# Fixture：重定向存储路径到 tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def storage_dir(tmp_path):
    """重定向 LocalSessionStorage 路径到 tmp_path。"""
    storage_path = tmp_path / "storage"
    storage_path.mkdir()
    return storage_path


# ---------------------------------------------------------------------------
# LocalSessionStorage load/save/get/set/remove/get_all
# ---------------------------------------------------------------------------

class TestLocalSessionStorageBasic:
    """LocalSessionStorage 基本 CRUD 测试."""

    @staticmethod
    def test_load_empty(storage_dir):
        """空文件加载应正常返回。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        store_path.write_text("{}", encoding="utf-8")

        storage = LocalSessionStorage(store_path=store_path)
        assert storage.get_all() == {}

    @staticmethod
    def test_set_and_get(storage_dir):
        """设置后应能获取。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _sess("sess_001"))
        result = storage.get("key1")
        assert result is not None
        assert result.session_id == "sess_001"

    @staticmethod
    def test_get_nonexistent(storage_dir):
        """获取不存在的 key 应返回 None。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        assert storage.get("nonexistent") is None

    @staticmethod
    def test_remove(storage_dir):
        """删除后应无法获取。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _sess("sess_001"))
        storage.remove("key1")
        assert storage.get("key1") is None

    @staticmethod
    def test_get_all(storage_dir):
        """获取所有数据。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _sess("sess_001"))
        storage.set("key2", _sess("sess_002"))
        all_data = storage.get_all()
        assert len(all_data) == 2
        assert all_data["key1"].session_id == "sess_001"
        assert all_data["key2"].session_id == "sess_002"


# ---------------------------------------------------------------------------
# LocalSessionStorage load/save 持久化
# ---------------------------------------------------------------------------

class TestLocalSessionStoragePersist:
    """LocalSessionStorage load/save 持久化测试."""

    @staticmethod
    def test_save_single_session(storage_dir):
        """set 应把 Session 对象持久化为 JSON dict。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _sess("sess_001"))
        storage.set("key2", _sess("sess_002"))

        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["key1"]["session_id"] == "sess_001"
        assert data["key1"]["service_id"]

    @staticmethod
    def test_save_nonexistent_session(storage_dir):
        """save 不在 mapping 中的 Session 应无操作（不抛异常）。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.save(_sess("nonexistent_session_id"))

    @staticmethod
    def test_load_from_existing_file(storage_dir):
        """从已存在的文件加载（兼容旧版纯字符串）。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        store_path.write_text(
            json.dumps({"key1": "sess_old"}, ensure_ascii=False),
            encoding="utf-8",
        )

        storage = LocalSessionStorage(store_path=store_path)

        result = storage.get("key1")
        assert result is not None
        assert result.session_id == "sess_old"
        assert result.service_id


# ---------------------------------------------------------------------------
# claw_id 隔离
# ---------------------------------------------------------------------------

class TestClawIdIsolation:
    """不同 claw_id 应使用不同的存储路径。"""

    @staticmethod
    def test_local_storage_path(storage_dir):
        """LocalSessionStorage 使用固定路径 session_map.json。"""
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        storage = LocalSessionStorage(store_path=storage_dir / "test_map.json")
        assert "test_map.json" in str(storage.store_path)


# ---------------------------------------------------------------------------
# SessionMap 使用存储抽象
# ---------------------------------------------------------------------------

class TestSessionMapWithStorage:
    """SessionMap 通过存储抽象层操作。"""

    @staticmethod
    def test_session_map_set_and_find(storage_dir, monkeypatch):
        """set_session_id / find_session_id 应读写 Session 并持久化。"""
        from jiuwenswarm.gateway.routing.session_map import SessionMap, SessionMapScope
        from jiuwenswarm.gateway.routing.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        monkeypatch.setattr(
            "jiuwenswarm.gateway.routing.session_map.LocalSessionStorage",
            lambda: LocalSessionStorage(store_path=store_path),
        )
        monkeypatch.delenv("AGENT_RUNTIME", raising=False)

        sm = SessionMap(scope=SessionMapScope.PER_CHAT_BOT)
        sm.set_session_id("provider1", "chat1", "bot1", "user1", "provider1::chat1::bot1::ts1::suffix1")
        assert sm.find_session_id("provider1", "chat1", "bot1", "user1") == (
            "provider1::chat1::bot1::ts1::suffix1"
        )
        sess = sm.find_session("provider1", "chat1", "bot1", "user1")
        assert sess is not None
        assert sess.service_id == invoke_service_id("chat1", "bot1")

        storage2 = LocalSessionStorage(store_path=store_path)
        result = storage2.get("provider1::chat1::bot1")
        assert result is not None
        assert result.session_id == "provider1::chat1::bot1::ts1::suffix1"

    @staticmethod
    def test_invoke_ids_helpers():
        from jiuwenswarm.gateway.routing.session_map import (
            SessionMapScope,
            invoke_ids_from_identity,
            invoke_ids_from_session_id_string,
        )

        svc, aid = invoke_ids_from_identity("chat", "bot", "user", SessionMapScope.PER_CHAT_BOT)
        assert svc == invoke_service_id("chat", "bot")
        assert aid is None

        svc2, aid2 = invoke_ids_from_identity("chat", "bot", "user", SessionMapScope.PER_CHAT_BOT_USER)
        assert svc2 == svc
        assert aid2 == "user"

        sid = "feishu::chatX::botY::aabb::cc"
        s3, a3 = invoke_ids_from_session_id_string(sid)
        assert s3 == invoke_service_id("chatX", "botY")
        assert a3 is None
