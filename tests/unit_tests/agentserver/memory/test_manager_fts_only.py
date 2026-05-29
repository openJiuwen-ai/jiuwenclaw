# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for MemoryIndexManager FTS-only mode behaviour.

When the embedding config is incomplete (missing api_key, base_url, or model),
the manager disables vector search and operates in FTS-only mode:

  - vector_enabled = False
  - minScore = 0.0  (no threshold, rely on BM25 ranking)
  - _initialize skips embedding provider init
  - _should_full_reindex tolerates provider=None
  - _run_reindex records "none" for provider/model
  - _index_chunk skips embedding call
  - _search uses weight (0.0, 1.0) instead of hybrid (0.7, 0.3)
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.memory.config import MemorySettings
from jiuwenclaw.agentserver.memory.manager import MemoryIndexManager
from jiuwenclaw.agentserver.memory.types import MemoryChunk


# ---------------------------------------------------------------------------
# Helper: create a manager stub without running async _initialize
# ---------------------------------------------------------------------------

def _create_manager_stub(settings, embed_config_result):
    """Build a MemoryIndexManager with _initialize mocked out so the
    constructor completes synchronously.

    Also patches get_embed_config so the constructor reads the supplied
    embed config instead of the real one.
    """
    with patch.object(MemoryIndexManager, "_initialize"), \
         patch("jiuwenclaw.agentserver.memory.manager.get_embed_config",
               return_value=embed_config_result):
        mgr = MemoryIndexManager(
            workspace_dir="/tmp/test_workspace_stub",
            agent_id="test_stub",
            settings=settings,
        )
    return mgr


def _default_settings(**overrides):
    """Create MemorySettings with sensible defaults for tests."""
    s = MemorySettings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_mock_provider(id_="openai_compatible", model="text-embedding-v3"):
    """Create a mock EmbeddingProvider with .id and .model attributes."""
    p = MagicMock()
    p.id = id_
    p.model = model
    return p


# ---------------------------------------------------------------------------
# TestFtsOnlyInit
# ---------------------------------------------------------------------------

class TestFtsOnlyInit:
    """Constructor behaviour when embedding config is incomplete."""

    @staticmethod
    def test_vector_disabled_when_no_embed_config():
        """Incomplete embed config => vector_enabled=False."""
        settings = _default_settings()
        embed_cfg = {"api_key": "", "base_url": "", "model": ""}
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.vector_enabled is False

    @staticmethod
    def test_vector_enabled_with_full_embed_config():
        """Complete embed config => vector_enabled=True, minScore stays default."""
        settings = _default_settings()
        embed_cfg = {
            "api_key": "sk-test",
            "base_url": "https://api.example.com",
            "model": "text-embedding-v3",
        }
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.vector_enabled is True
        # Default minScore should not have been overwritten to 0.0
        assert mgr.settings.query.get("minScore", 0.3) != 0.0

    @staticmethod
    def test_store_vector_enabled_false_overrides_embed():
        """store.vector.enabled=False overrides even with full embed config."""
        settings = _default_settings()
        settings.store = {
            "path": "memory.db",
            "vector": {"enabled": False},
            "fts": {"enabled": True},
        }
        embed_cfg = {
            "api_key": "sk-test",
            "base_url": "https://api.example.com",
            "model": "text-embedding-v3",
        }
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.vector_enabled is False

    @staticmethod
    def test_fts_only_sets_min_score_zero():
        """When vector disabled, minScore should be set to 0.0."""
        settings = _default_settings()
        embed_cfg = {"api_key": "", "base_url": "", "model": ""}
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.settings.query["minScore"] == 0.0


# ---------------------------------------------------------------------------
# TestNeedResync  (_should_full_reindex with provider=None)
# ---------------------------------------------------------------------------

# pylint: disable=protected-access
class TestNeedResync:
    """_should_full_reindex must not crash when provider is None."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_no_provider_passes_without_crash():
        """When provider=None, the `if self.provider:` block is skipped — no crash."""
        settings = _default_settings()
        embed_cfg = {"api_key": "", "base_url": "", "model": ""}
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.provider is None

        # Set up a minimal in-memory db with a meta row
        mgr.db = sqlite3.connect(":memory:")
        mgr.db.row_factory = sqlite3.Row
        mgr.db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        mgr.db.commit()

        # Insert existing meta so the function finds a row
        existing_meta = {
            "provider": "none",
            "model": "none",
            "chunkTokens": settings.chunking.get("tokens"),
        }
        mgr.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            ("memory_index_meta_v1", json.dumps(existing_meta)),
        )
        mgr.db.commit()

        result = await mgr._should_full_reindex()
        # chunkTokens matches, so no reindex needed
        assert result is False

    @pytest.mark.asyncio
    @staticmethod
    async def test_provider_check_returns_true_on_id_mismatch():
        """When provider exists and meta provider id differs => True."""
        settings = _default_settings()
        embed_cfg = {
            "api_key": "sk-test",
            "base_url": "https://api.example.com",
            "model": "text-embedding-v3",
        }
        mgr = _create_manager_stub(settings, embed_cfg)
        mgr.provider = _make_mock_provider(id_="different_provider")

        mgr.db = sqlite3.connect(":memory:")
        mgr.db.row_factory = sqlite3.Row
        mgr.db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        mgr.db.commit()

        existing_meta = {
            "provider": "original_provider",
            "model": "text-embedding-v3",
            "chunkTokens": settings.chunking.get("tokens"),
        }
        mgr.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            ("memory_index_meta_v1", json.dumps(existing_meta)),
        )
        mgr.db.commit()

        result = await mgr._should_full_reindex()
        assert result is True


# ---------------------------------------------------------------------------
# TestSyncMeta  (_run_reindex meta with provider=None)
# ---------------------------------------------------------------------------

# pylint: disable=protected-access
class TestSyncMeta:
    """_run_reindex must record 'none' when provider is None."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_meta_provider_none_fallback():
        """When provider=None, meta records 'none' for provider and model."""
        settings = _default_settings()
        embed_cfg = {"api_key": "", "base_url": "", "model": ""}
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.provider is None

        # Minimal db with schema
        mgr.db = sqlite3.connect(":memory:")
        mgr.db.row_factory = sqlite3.Row
        mgr.db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        mgr.db.commit()

        # Patch the sync sub-functions so _run_reindex doesn't try real IO
        mgr._sync_memory_files = AsyncMock()
        mgr._sync_session_files = AsyncMock()
        mgr._write_meta = MagicMock()

        await mgr._run_reindex()

        mgr._write_meta.assert_called_once()
        meta = mgr._write_meta.call_args[0][0]
        assert meta["provider"] == "none"
        assert meta["model"] == "none"

    @pytest.mark.asyncio
    @staticmethod
    async def test_meta_with_provider_real_values():
        """When provider exists, meta records actual id and model."""
        settings = _default_settings()
        embed_cfg = {
            "api_key": "sk-test",
            "base_url": "https://api.example.com",
            "model": "text-embedding-v3",
        }
        mgr = _create_manager_stub(settings, embed_cfg)
        mgr.provider = _make_mock_provider(
            id_="openai_compatible",
            model="text-embedding-v3",
        )

        mgr.db = sqlite3.connect(":memory:")
        mgr.db.row_factory = sqlite3.Row
        mgr.db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        mgr.db.commit()

        mgr._sync_memory_files = AsyncMock()
        mgr._sync_session_files = AsyncMock()
        mgr._write_meta = MagicMock()

        await mgr._run_reindex()

        mgr._write_meta.assert_called_once()
        meta = mgr._write_meta.call_args[0][0]
        assert meta["provider"] == "openai_compatible"
        assert meta["model"] == "text-embedding-v3"


# ---------------------------------------------------------------------------
# TestSearchWeights
# ---------------------------------------------------------------------------

# pylint: disable=protected-access
class TestSearchWeights:
    """Verify the weight vectors used in _merge_hybrid_results calls."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_fts_only_confirms_zero_vector_weight():
        """In FTS-only mode, _search delegates to _merge_hybrid_results([], kw, 0.0, 1.0)."""
        settings = _default_settings()
        embed_cfg = {"api_key": "", "base_url": "", "model": ""}
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.vector_enabled is False

        # Patch internal methods so _search runs without real db/embedding
        mgr.sync = AsyncMock()
        mgr.fts_available = True
        mgr.vector_available = False
        mgr.db = sqlite3.connect(":memory:")
        mgr.db.row_factory = sqlite3.Row

        # Minimal schema for chunks + FTS
        mgr.db.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT, source TEXT,
                start_line INTEGER, end_line INTEGER,
                hash TEXT, model TEXT,
                text TEXT, embedding BLOB,
                updated_at INTEGER
            )
        """)
        mgr.db.execute("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)
        """)
        mgr.db.commit()

        keyword_results = [
            {"id": "k1", "score": 0.8, "path": "/a", "source": "memory",
             "startLine": 1, "endLine": 10, "snippet": "hello"},
        ]

        mgr._search_keyword = AsyncMock(return_value=keyword_results)
        mgr._merge_hybrid_results = MagicMock(return_value=keyword_results)

        results = await mgr.search("hello")

        # _merge_hybrid_results should have been called with weight (0.0, 1.0)
        mgr._merge_hybrid_results.assert_called_once_with(
            [], keyword_results, 0.0, 1.0
        )

    @pytest.mark.asyncio
    @staticmethod
    async def test_vector_enabled_default_weights():
        """When vector_enabled=True, hybrid weights are (0.7, 0.3)."""
        settings = _default_settings()
        embed_cfg = {
            "api_key": "sk-test",
            "base_url": "https://api.example.com",
            "model": "text-embedding-v3",
        }
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.vector_enabled is True

        mgr.sync = AsyncMock()
        mgr.fts_available = True
        mgr.vector_available = True
        mgr.db = sqlite3.connect(":memory:")
        mgr.db.row_factory = sqlite3.Row
        mgr.db.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT, source TEXT,
                start_line INTEGER, end_line INTEGER,
                hash TEXT, model TEXT,
                text TEXT, embedding BLOB,
                updated_at INTEGER
            )
        """)
        mgr.db.execute("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)
        """)
        mgr.db.commit()

        keyword_results = [
            {"id": "k1", "score": 0.8, "path": "/a", "source": "memory",
             "startLine": 1, "endLine": 10, "snippet": "hello"},
        ]
        vector_results = [
            {"id": "v1", "score": 0.9, "path": "/b", "source": "memory",
             "startLine": 5, "endLine": 15, "snippet": "world"},
        ]

        mgr.provider = _make_mock_provider()
        mgr._search_keyword = AsyncMock(return_value=keyword_results)
        mgr._embed_query_with_timeout = AsyncMock(return_value=[0.1, 0.2])
        mgr._search_vector = AsyncMock(return_value=vector_results)
        mgr._merge_hybrid_results = MagicMock(return_value=[
            {"id": "v1", "score": 0.75},
            {"id": "k1", "score": 0.65},
        ])

        results = await mgr.search("hello")

        mgr._merge_hybrid_results.assert_called_once()
        call_args = mgr._merge_hybrid_results.call_args
        # vectorWeight=0.7, textWeight=0.3 from default hybrid settings
        assert call_args[0][2] == 0.7
        assert call_args[0][3] == 0.3


# ---------------------------------------------------------------------------
# TestIndexChunkFtsOnly
# ---------------------------------------------------------------------------

# pylint: disable=protected-access
class TestIndexChunkFtsOnly:
    """_index_chunk should skip embedding when vector_enabled=False."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_no_embedding_in_fts_only():
        """FTS-only mode: _get_embedding is never called for _index_chunk."""
        settings = _default_settings()
        embed_cfg = {"api_key": "", "base_url": "", "model": ""}
        mgr = _create_manager_stub(settings, embed_cfg)
        assert mgr.vector_enabled is False

        # Set up minimal db
        mgr.db = sqlite3.connect(":memory:")
        mgr.db.row_factory = sqlite3.Row

        mgr._ensure_schema()
        mgr.fts_available = False  # avoid FTS table writes for simplicity
        mgr.vector_available = False

        # Patch _get_embedding so we can verify it is never called
        mgr._get_embedding = AsyncMock(return_value=[0.1, 0.2])

        chunk = MemoryChunk(text="hello world", startLine=1, endLine=5)

        await mgr._index_chunk("/tmp/test.md", "memory", chunk)

        # _get_embedding should not have been called at all
        mgr._get_embedding.assert_not_called()