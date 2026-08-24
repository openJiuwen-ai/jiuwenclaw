# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tests for WebSocket message chunking."""

from __future__ import annotations

import asyncio
import json

import pytest

from jiuwenswarm.common.ws_chunking import (
    CHUNKING_META_KEY,
    CHUNK_CONTENT_KEY,
    ChunkMetadata,
    WireChunkBuffer,
    is_chunked_message,
    get_chunk_metadata,
    split_wire_payload_for_chunking,
)


# ---------------------------------------------------------------------------
# split_wire_payload_for_chunking
# ---------------------------------------------------------------------------


class TestSplitWirePayload:
    """Tests for split_wire_payload_for_chunking."""

    def test_small_payload_returns_single_element(self):
        """Payload under limit should not be chunked."""
        wire = {"request_id": "r1", "body": {"result": "ok"}}
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)
        assert len(chunks) == 1
        assert chunks[0] == wire

    def test_large_payload_is_chunked(self):
        """Payload over limit should be split into multiple chunks."""
        wire = {
            "request_id": "r1",
            "session_id": "s1",
            "body": {"content": "x" * 5000},
        }
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)
        assert len(chunks) > 1

    def test_all_chunks_fit_within_budget(self):
        """Each chunk must not exceed the budget."""
        wire = {
            "request_id": "r1",
            "session_id": "s1",
            "body": {"content": "y" * 10000},
        }
        budget = 2048
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=budget)
        for i, chunk in enumerate(chunks):
            serialized = json.dumps(chunk, ensure_ascii=False)
            size = len(serialized.encode("utf-8"))
            assert size <= budget, f"Chunk {i} exceeds budget: {size} > {budget}"

    def test_chunks_have_metadata(self):
        """Each chunk should have chunking metadata."""
        wire = {"request_id": "r1", "body": {"content": "z" * 5000}}
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)

        for i, chunk in enumerate(chunks):
            assert CHUNKING_META_KEY in chunk.get("metadata", {})
            meta = chunk["metadata"][CHUNKING_META_KEY]
            assert meta["chunk_index"] == i
            assert meta["total_chunks"] == len(chunks)
            assert "chunk_id" in meta

    def test_chunks_have_content_field(self):
        """Each chunk should have a _content field."""
        wire = {"request_id": "r1", "body": {"content": "a" * 5000}}
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)

        for chunk in chunks:
            assert CHUNK_CONTENT_KEY in chunk
            assert isinstance(chunk[CHUNK_CONTENT_KEY], str)

    def test_chunks_preserve_routing_keys(self):
        """Routing keys should be preserved in all chunks."""
        wire = {
            "request_id": "r1",
            "session_id": "s1",
            "channel_id": "web",
            "response_id": "resp1",
            "sequence": 5,
            "body": {"content": "b" * 5000},
        }
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)

        for chunk in chunks:
            assert chunk.get("request_id") == "r1"
            assert chunk.get("session_id") == "s1"
            assert chunk.get("channel_id") == "web"
            assert chunk.get("response_id") == "resp1"
            assert chunk.get("sequence") == 5

    def test_reassembled_chunks_match_original(self):
        """Concatenating all chunk contents should reconstruct the original JSON."""
        wire = {
            "request_id": "r1",
            "session_id": "s1",
            "body": {"content": "c" * 5000},
        }
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)

        # Concatenate all _content fields
        reassembled_json = "".join(chunk[CHUNK_CONTENT_KEY] for chunk in chunks)
        reassembled = json.loads(reassembled_json)

        assert reassembled == wire

    def test_chunk_id_is_unique(self):
        """Each chunking operation should produce a unique chunk_id."""
        wire = {"request_id": "r1", "body": {"content": "d" * 5000}}
        chunks1 = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)
        chunks2 = split_wire_payload_for_chunking(wire, max_chunk_bytes=1024)

        id1 = chunks1[0]["metadata"][CHUNKING_META_KEY]["chunk_id"]
        id2 = chunks2[0]["metadata"][CHUNKING_META_KEY]["chunk_id"]
        assert id1 != id2

    def test_unicode_content(self):
        """Chunking should handle multi-byte UTF-8 characters correctly."""
        wire = {
            "request_id": "r1",
            "body": {"content": "你好世界" * 1000},
        }
        budget = 2048
        chunks = split_wire_payload_for_chunking(wire, max_chunk_bytes=budget)

        for chunk in chunks:
            serialized = json.dumps(chunk, ensure_ascii=False)
            size = len(serialized.encode("utf-8"))
            assert size <= budget

        # Reassemble and verify
        reassembled_json = "".join(chunk[CHUNK_CONTENT_KEY] for chunk in chunks)
        reassembled = json.loads(reassembled_json)
        assert reassembled == wire


# ---------------------------------------------------------------------------
# is_chunked_message / get_chunk_metadata
# ---------------------------------------------------------------------------


class TestChunkDetection:
    """Tests for chunk detection helpers."""

    def test_is_chunked_message_true(self):
        """Should detect chunked messages."""
        wire = {
            "request_id": "r1",
            "metadata": {CHUNKING_META_KEY: {"chunk_id": "c1", "chunk_index": 0, "total_chunks": 3}},
            CHUNK_CONTENT_KEY: "some content",
        }
        assert is_chunked_message(wire) is True

    def test_is_chunked_message_false_no_metadata(self):
        """Should return False for messages without metadata."""
        wire = {"request_id": "r1", "body": "data"}
        assert is_chunked_message(wire) is False

    def test_is_chunked_message_false_other_metadata(self):
        """Should return False for messages with metadata but no chunking key."""
        wire = {"request_id": "r1", "metadata": {"other_key": "value"}}
        assert is_chunked_message(wire) is False

    def test_get_chunk_metadata_valid(self):
        """Should extract chunk metadata correctly."""
        wire = {
            "metadata": {
                CHUNKING_META_KEY: {
                    "chunk_id": "c1",
                    "chunk_index": 2,
                    "total_chunks": 5,
                }
            }
        }
        meta = get_chunk_metadata(wire)
        assert meta is not None
        assert meta.chunk_id == "c1"
        assert meta.chunk_index == 2
        assert meta.total_chunks == 5

    def test_get_chunk_metadata_missing(self):
        """Should return None when chunking metadata is missing."""
        wire = {"request_id": "r1"}
        assert get_chunk_metadata(wire) is None

    def test_get_chunk_metadata_invalid(self):
        """Should return None when chunking metadata is malformed."""
        wire = {"metadata": {CHUNKING_META_KEY: {"chunk_id": "c1"}}}  # missing fields
        assert get_chunk_metadata(wire) is None


# ---------------------------------------------------------------------------
# WireChunkBuffer
# ---------------------------------------------------------------------------


class TestWireChunkBuffer:
    """Tests for WireChunkBuffer."""

    @pytest.fixture
    def buffer(self):
        return WireChunkBuffer()

    def _make_chunk(self, chunk_id: str, chunk_index: int, total_chunks: int, content: str, request_id: str = "r1") -> dict:
        return {
            "request_id": request_id,
            "metadata": {
                CHUNKING_META_KEY: {
                    "chunk_id": chunk_id,
                    "chunk_index": chunk_index,
                    "total_chunks": total_chunks,
                }
            },
            CHUNK_CONTENT_KEY: content,
        }

    @pytest.mark.asyncio
    async def test_non_chunked_message_returns_immediately(self, buffer):
        """Non-chunked messages should be returned as-is."""
        wire = {"request_id": "r1", "body": "data"}
        result = await buffer.add_chunk(wire, "r1")
        assert result == wire

    @pytest.mark.asyncio
    async def test_single_chunk_returns_immediately(self, buffer):
        """A single-chunk message should be returned immediately."""
        original = {"request_id": "r1", "body": "data"}
        original_json = json.dumps(original)
        chunk = self._make_chunk("c1", 0, 1, original_json)

        result = await buffer.add_chunk(chunk, "r1")
        assert result is not None
        assert result == original

    @pytest.mark.asyncio
    async def test_multi_chunk_reassembly(self, buffer):
        """Multiple chunks should be reassembled into the original message."""
        original = {"request_id": "r1", "body": {"content": "x" * 100}}
        original_json = json.dumps(original)

        # Split into 3 pieces
        piece_size = len(original_json) // 3
        pieces = [
            original_json[:piece_size],
            original_json[piece_size:2*piece_size],
            original_json[2*piece_size:],
        ]

        # Add chunks in order
        result = None
        for i, piece in enumerate(pieces):
            chunk = self._make_chunk("c1", i, 3, piece)
            result = await buffer.add_chunk(chunk, "r1")

        assert result is not None
        assert result == original

    @pytest.mark.asyncio
    async def test_out_of_order_chunks(self, buffer):
        """Chunks arriving out of order should still reassemble correctly."""
        original = {"request_id": "r1", "body": {"content": "y" * 100}}
        original_json = json.dumps(original)

        piece_size = len(original_json) // 3
        pieces = [
            original_json[:piece_size],
            original_json[piece_size:2*piece_size],
            original_json[2*piece_size:],
        ]

        # Add chunks out of order: 2, 0, 1
        result = await buffer.add_chunk(self._make_chunk("c1", 2, 3, pieces[2]), "r1")
        assert result is None

        result = await buffer.add_chunk(self._make_chunk("c1", 0, 3, pieces[0]), "r1")
        assert result is None

        result = await buffer.add_chunk(self._make_chunk("c1", 1, 3, pieces[1]), "r1")
        assert result is not None
        assert result == original

    @pytest.mark.asyncio
    async def test_incomplete_chunks_return_none(self, buffer):
        """Incomplete chunk sets should return None."""
        chunk0 = self._make_chunk("c1", 0, 3, "piece0")
        chunk1 = self._make_chunk("c1", 1, 3, "piece1")

        result0 = await buffer.add_chunk(chunk0, "r1")
        assert result0 is None

        result1 = await buffer.add_chunk(chunk1, "r1")
        assert result1 is None

        assert buffer.pending_count() == 1

    @pytest.mark.asyncio
    async def test_multiple_assemblies(self, buffer):
        """Should handle multiple concurrent chunk assemblies."""
        original1 = {"request_id": "r1", "body": "data1"}
        original2 = {"request_id": "r2", "body": "data2"}
        json1 = json.dumps(original1)
        json2 = json.dumps(original2)

        # Split each into 2 pieces
        mid1 = len(json1) // 2
        mid2 = len(json2) // 2

        # Interleave chunks from both messages
        result = await buffer.add_chunk(self._make_chunk("c1", 0, 2, json1[:mid1], "r1"), "r1")
        assert result is None

        result = await buffer.add_chunk(self._make_chunk("c2", 0, 2, json2[:mid2], "r2"), "r2")
        assert result is None

        result = await buffer.add_chunk(self._make_chunk("c1", 1, 2, json1[mid1:], "r1"), "r1")
        assert result is not None
        assert result == original1

        result = await buffer.add_chunk(self._make_chunk("c2", 1, 2, json2[mid2:], "r2"), "r2")
        assert result is not None
        assert result == original2

        assert buffer.pending_count() == 0

    @pytest.mark.asyncio
    async def test_clear_removes_all_assemblies(self, buffer):
        """clear() should remove all pending assemblies."""
        chunk = self._make_chunk("c1", 0, 3, "piece0")
        await buffer.add_chunk(chunk, "r1")
        assert buffer.pending_count() == 1

        await buffer.clear()
        assert buffer.pending_count() == 0

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, buffer):
        """Expired assemblies should be cleaned up."""
        # Create buffer with very short timeout
        short_timeout_buffer = WireChunkBuffer(timeout_seconds=0.01)

        chunk = self._make_chunk("c1", 0, 3, "piece0")
        await short_timeout_buffer.add_chunk(chunk, "r1")
        assert short_timeout_buffer.pending_count() == 1

        # Wait for timeout
        await asyncio.sleep(0.02)

        await short_timeout_buffer.cleanup_expired()
        assert short_timeout_buffer.pending_count() == 0

    @pytest.mark.asyncio
    async def test_buffer_size_limit_evicts_oldest(self):
        """Buffer should evict oldest assembly when full."""
        # Create buffer with small limit
        small_buffer = WireChunkBuffer(max_pending=2)

        # Add 2 incomplete assemblies
        chunk1 = self._make_chunk("c1", 0, 3, "piece0", "r1")
        await small_buffer.add_chunk(chunk1, "r1")
        assert small_buffer.pending_count() == 1

        await asyncio.sleep(0.01)  # Ensure different timestamps

        chunk2 = self._make_chunk("c2", 0, 3, "piece0", "r2")
        await small_buffer.add_chunk(chunk2, "r2")
        assert small_buffer.pending_count() == 2

        await asyncio.sleep(0.01)  # Ensure different timestamps

        # Add 3rd assembly, should evict oldest (c1)
        chunk3 = self._make_chunk("c3", 0, 3, "piece0", "r3")
        await small_buffer.add_chunk(chunk3, "r3")
        assert small_buffer.pending_count() == 2

        # Verify c1 was evicted by trying to complete it
        chunk1_more = self._make_chunk("c1", 1, 3, "piece1", "r1")
        result = await small_buffer.add_chunk(chunk1_more, "r1")
        # c1 was evicted, so this creates a new assembly
        assert result is None
        assert small_buffer.pending_count() == 2  # Still 2, not 3

    @pytest.mark.asyncio
    async def test_duplicate_chunk_index(self, buffer):
        """Duplicate chunk indices should be handled gracefully."""
        original = {"request_id": "r1", "body": "data"}
        original_json = json.dumps(original)
        mid = len(original_json) // 2

        # Add chunk 0 twice
        chunk0 = self._make_chunk("c1", 0, 2, original_json[:mid])
        await buffer.add_chunk(chunk0, "r1")
        await buffer.add_chunk(chunk0, "r1")  # duplicate

        # Add chunk 1
        chunk1 = self._make_chunk("c1", 1, 2, original_json[mid:])
        result = await buffer.add_chunk(chunk1, "r1")

        # Should still reassemble (duplicate overwrites)
        assert result is not None
        assert result == original


# ---------------------------------------------------------------------------
# Integration: split + reassemble
# ---------------------------------------------------------------------------


class TestSplitAndReassemble:
    """Integration tests for splitting and reassembling."""

    @pytest.mark.asyncio
    async def test_split_and_reassemble_roundtrip(self):
        """Split a payload, then reassemble via buffer - should match original."""
        original = {
            "request_id": "r1",
            "session_id": "s1",
            "channel_id": "web",
            "body": {"content": "x" * 5000},
        }

        # Split
        chunks = split_wire_payload_for_chunking(original, max_chunk_bytes=1024)
        assert len(chunks) > 1

        # Reassemble
        buffer = WireChunkBuffer()
        result = None
        for chunk in chunks:
            result = await buffer.add_chunk(chunk, "r1")

        assert result is not None
        assert result == original

    @pytest.mark.asyncio
    async def test_split_and_reassemble_with_unicode(self):
        """Roundtrip with multi-byte characters."""
        original = {
            "request_id": "r1",
            "body": {"content": "你好世界" * 500},
        }

        chunks = split_wire_payload_for_chunking(original, max_chunk_bytes=2048)
        assert len(chunks) > 1

        buffer = WireChunkBuffer()
        result = None
        for chunk in chunks:
            result = await buffer.add_chunk(chunk, "r1")

        assert result is not None
        assert result == original

    @pytest.mark.asyncio
    async def test_split_and_reassemble_large_payload(self):
        """Roundtrip with a very large payload."""
        original = {
            "request_id": "r1",
            "body": {"content": "a" * 50000},
        }

        chunks = split_wire_payload_for_chunking(original, max_chunk_bytes=4096)
        assert len(chunks) > 10

        buffer = WireChunkBuffer()
        result = None
        for chunk in chunks:
            result = await buffer.add_chunk(chunk, "r1")

        assert result is not None
        assert result == original
