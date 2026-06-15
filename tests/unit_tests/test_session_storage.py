"""session_storage 模块单元测试（SessionMap 存储抽象层）"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from jiuwenclaw.gateway.message_handler import MessageHandler
from jiuwenclaw.gateway.session_map import SessionMap
from jiuwenclaw.gateway.session_storage import RedisSessionStorage

if TYPE_CHECKING:
    from jiuwenclaw.gateway.agent_client import AgentServerClient
    from jiuwenclaw.gateway.session_map import Session


def _make_session(session_id: str, agent_id: str | None = None) -> Session:
    from jiuwenclaw.gateway.session_map import Session, invoke_ids_from_session_id_string

    svc, derived_aid = invoke_ids_from_session_id_string(session_id)
    return Session(
        session_id=session_id,
        service_id=svc,
        agent_id=agent_id if agent_id is not None else derived_aid,
    )


class _AsyncDictRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def seed(self, key: str, value: str) -> None:
        self._data[key] = value

    def peek(self, key: str) -> str | None:
        return self._data.get(key)

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self._data[key] = value
        return True

    async def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._data.get(key) for key in keys]

    async def scan_keys(self, pattern: str) -> list[str]:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [key for key in self._data if key.startswith(prefix)]
        return [key for key in self._data if key == pattern]


class _TestRedisSessionStorage(RedisSessionStorage):
    def __init__(self, redis_client: _AsyncDictRedis, claw_id: str) -> None:
        super().__init__()
        self._redis = redis_client
        self._claw_id = claw_id

    async def seed_session(self, identity_key: str, raw_session: dict[str, str | None]) -> None:
        assert self._redis is not None
        await self._redis.set(self._redis_key(identity_key), json.dumps(raw_session))

    def has_identity_key(self, identity_key: str) -> bool:
        return bool(self._redis and self._redis.peek(self._redis_key(identity_key)))


class _TestSessionMap(SessionMap):
    def __init__(self, storage: _TestRedisSessionStorage) -> None:
        super().__init__()
        self._storage = storage


class _TestMessageHandler(MessageHandler):
    @classmethod
    def build(cls, shared_redis: _AsyncDictRedis, claw_id: str):
        cls._instance = None
        handler = cls(cast("AgentServerClient", object()))
        storage = _TestRedisSessionStorage(shared_redis, claw_id)
        handler._session_map = _TestSessionMap(storage)
        return handler

    def apply_channel_state_for_test(self, msg) -> None:
        self._apply_channel_state(msg)

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


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
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        store_path.write_text("{}", encoding="utf-8")

        storage = LocalSessionStorage(store_path=store_path)
        assert storage.get_all() == {}

    @staticmethod
    def test_set_and_get(storage_dir):
        """设置后应能获取。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _make_session("sess_001"))
        result = storage.get("key1")
        assert result is not None
        assert result.session_id == "sess_001"

    @staticmethod
    def test_get_nonexistent(storage_dir):
        """获取不存在的 key 应返回 None。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        assert storage.get("nonexistent") is None

    @staticmethod
    def test_remove(storage_dir):
        """删除后应无法获取。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _make_session("sess_001"))
        storage.remove("key1")
        assert storage.get("key1") is None

    @staticmethod
    def test_get_all(storage_dir):
        """获取所有数据。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _make_session("sess_001"))
        storage.set("key2", _make_session("sess_002"))
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
        """save(session_id) 应只保存单条数据到文件。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.set("key1", _make_session("sess_001"))
        storage.set("key2", _make_session("sess_002"))

        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["key1"]["session_id"] == "sess_001"

    @staticmethod
    def test_save_nonexistent_session(storage_dir):
        """save 不存在的 session_id 应无操作。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        storage.save(_make_session("orphan_not_in_mapping"))

    @staticmethod
    def test_load_from_existing_file(storage_dir):
        """从已存在的文件加载。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        # 预写文件（简化格式：直接存 session_id 字符串）
        store_path.write_text(
            json.dumps({"key1": "sess_old"}, ensure_ascii=False),
            encoding="utf-8",
        )

        storage = LocalSessionStorage(store_path=store_path)

        result = storage.get("key1")
        assert result is not None
        assert result.session_id == "sess_old"


# ---------------------------------------------------------------------------
# claw_id 隔离
# ---------------------------------------------------------------------------

class TestClawIdIsolation:
    """不同 claw_id 应使用不同的存储路径。"""

    @staticmethod
    def test_local_storage_path(storage_dir):
        """LocalSessionStorage 使用固定路径 session_map.json。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        storage = LocalSessionStorage(store_path=storage_dir / "test_map.json")
        assert "test_map.json" in str(storage.store_path)


# ---------------------------------------------------------------------------
# SessionMap 使用存储抽象
# ---------------------------------------------------------------------------

class TestSessionMapWithStorage:
    """SessionMap 通过存储抽象层操作。"""

    @staticmethod
    def test_session_map_get_session(storage_dir, monkeypatch):
        """get_session 应创建并返回正确的 Session。"""
        from jiuwenclaw.gateway.session_storage import LocalSessionStorage

        store_path = storage_dir / "session_map_default.json"
        storage = LocalSessionStorage(store_path=store_path)

        identity_key = "provider1::chat1::bot1"
        session_id = "provider1::chat1::bot1::ts1::suffix1"
        storage.set(identity_key, _make_session(session_id))

        storage2 = LocalSessionStorage(store_path=store_path)
        result = storage2.get(identity_key)
        assert result is not None
        assert result.session_id == session_id


@pytest.mark.asyncio
async def test_redis_session_storage_fetches_existing_session_without_local_cache() -> None:
    from jiuwenclaw.gateway.session_map import invoke_service_id

    test_storage = _TestRedisSessionStorage(_AsyncDictRedis(), "gateway-1")
    storage = test_storage

    identity_key = "feishu::chat-1::bot-1"
    raw_session = {
        "session_id": "feishu::chat-1::bot-1::abc123::def456",
        "service_id": invoke_service_id("chat-1", "bot-1"),
        "agent_id": None,
    }
    await test_storage.seed_session(identity_key, raw_session)

    session = storage.get(identity_key)

    assert session is not None
    assert session.session_id == raw_session["session_id"]
    assert storage.get_all()[identity_key].session_id == raw_session["session_id"]


def test_message_handler_reuses_redis_backed_session_after_failover() -> None:
    from jiuwenclaw.schema.message import Message, ReqMethod

    shared_redis = _AsyncDictRedis()

    def _make_feishu_message() -> Message:
        return Message(
            id="req-1",
            type="req",
            channel_id="feishu",
            session_id="external-chat-1",
            params={"query": "你好"},
            timestamp=123.0,
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            provider="feishu",
            chat_id="chat-1",
            bot_id="bot-1",
            user_id="user-1",
        )

    primary_handler = _TestMessageHandler.build(shared_redis, "gateway-1")
    first_msg = _make_feishu_message()
    primary_handler.apply_channel_state_for_test(first_msg)
    first_session_id = first_msg.session_id

    assert _TestRedisSessionStorage(shared_redis, "gateway-1").has_identity_key("feishu::chat-1::bot-1")

    standby_promoted_handler = _TestMessageHandler.build(shared_redis, "gateway-1")
    second_msg = _make_feishu_message()
    standby_promoted_handler.reload_session_map()
    standby_promoted_handler.apply_channel_state_for_test(second_msg)

    assert second_msg.session_id == first_session_id

    _TestMessageHandler.reset()