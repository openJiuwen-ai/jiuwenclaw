# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WebSocket message chunking for large payloads.

Splits oversized wire payloads into smaller chunks on the sender side
and reassembles them on the receiver side.

Wire protocol for chunks::

    {
        "request_id": "<original request_id>",
        "session_id": "<original session_id>",
        ...  # all routing keys preserved
        "metadata": {
            "_chunking": {
                "chunk_id": "<unique id>",
                "chunk_index": 0,
                "total_chunks": 5
            }
        },
        "_content": "<piece of the original JSON string>"
    }

The receiver buffers chunks by ``chunk_id``, concatenates ``_content``
pieces in order, and parses the result as the original JSON message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jiuwenswarm.common.ws_limits import AGENT_WS_SEND_BUDGET_BYTES

logger = logging.getLogger(__name__)

# Chunking metadata key in wire protocol
CHUNKING_META_KEY = "_chunking"

# Content field key in chunk wire payload
CHUNK_CONTENT_KEY = "_content"

# Default timeout for incomplete chunk assemblies (5 minutes)
CHUNK_ASSEMBLY_TIMEOUT_SECONDS = 300.0

# Maximum number of incomplete assemblies allowed in buffer
# Prevents memory exhaustion from continuous packet loss
MAX_PENDING_ASSEMBLIES = 100

# Routing keys that must be preserved in every chunk
_ROUTING_KEYS = (
    "request_id",
    "session_id",
    "task_id",
    "context_id",
    "correlation_id",
    "channel",
    "channel_id",
    "response_id",
    "sequence",
    "type",
    "event",
    "protocol_version",
    "response_kind",
    "is_stream",
    "is_final",
    "status",
    "agent_ref",
    "ok",
)

# Estimated overhead for chunk wrapper (metadata + routing keys + JSON structure)
# Conservative estimate to ensure chunks fit within the budget
_CHUNK_WRAPPER_OVERHEAD_BYTES = 512


@dataclass
class ChunkMetadata:
    """Metadata for a chunked message."""

    chunk_id: str
    chunk_index: int
    total_chunks: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkMetadata:
        return cls(
            chunk_id=str(data["chunk_id"]),
            chunk_index=int(data["chunk_index"]),
            total_chunks=int(data["total_chunks"]),
        )


@dataclass
class ChunkBuildContext:
    """Shared context for building chunk wire payloads during a single split operation.

    Groups the parameters that remain constant across all chunks of one message
    (``routing`` keys, ``original_metadata``, and ``chunk_id``), so the per-chunk
    call site only needs to supply the varying fields (``chunk_index``, ``content``).
    """

    routing: dict[str, Any]
    original_metadata: dict[str, Any]
    chunk_id: str

    def build_chunk(
        self,
        chunk_index: int,
        content: str,
        total_chunks: int | None = None,
    ) -> dict[str, Any]:
        """Build a chunk wire payload."""
        chunk = dict(self.routing)  # Copy routing keys
        # Preserve original metadata and add chunking metadata
        chunk["metadata"] = dict(self.original_metadata)
        chunk["metadata"][CHUNKING_META_KEY] = ChunkMetadata(
            chunk_id=self.chunk_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks or 999,  # Placeholder, will be updated
        ).to_dict()
        chunk[CHUNK_CONTENT_KEY] = content
        return chunk


@dataclass
class ChunkAssembly:
    """Buffer for assembling chunked messages."""

    chunk_id: str
    total_chunks: int
    request_id: str
    pieces: dict[int, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def add_piece(self, chunk_index: int, content: str) -> None:
        """Add a content piece to the assembly buffer."""
        self.pieces[chunk_index] = content

    def is_complete(self) -> bool:
        """Check if all pieces have been received."""
        return len(self.pieces) == self.total_chunks

    def reassemble(self) -> str | None:
        """Reassemble the complete JSON string from pieces.

        Returns None if not all pieces are present.
        """
        if not self.is_complete():
            return None

        # Concatenate pieces in order
        parts: list[str] = []
        for i in range(self.total_chunks):
            piece = self.pieces.get(i)
            if piece is None:
                return None
            parts.append(piece)

        return "".join(parts)


def is_chunked_message(wire: dict[str, Any]) -> bool:
    """Check if a wire payload is a chunk of a larger message."""
    metadata = wire.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return CHUNKING_META_KEY in metadata


def get_chunk_metadata(wire: dict[str, Any]) -> ChunkMetadata | None:
    """Extract chunk metadata from a wire payload."""
    metadata = wire.get("metadata")
    if not isinstance(metadata, dict):
        return None
    chunking_meta = metadata.get(CHUNKING_META_KEY)
    if not isinstance(chunking_meta, dict):
        return None
    try:
        return ChunkMetadata.from_dict(chunking_meta)
    except (KeyError, ValueError):
        return None


def split_wire_payload_for_chunking(
    wire: dict[str, Any],
    max_chunk_bytes: int = AGENT_WS_SEND_BUDGET_BYTES,
) -> list[dict[str, Any]]:
    """Split a wire payload into chunks if it exceeds the size limit.

    Strategy:
    1. Serialize the entire payload to JSON
    2. If it fits, return as-is
    3. Otherwise, split the JSON string into pieces that fit within the budget
    4. Wrap each piece in a chunk message with routing keys and metadata

    Args:
        wire: The wire payload to potentially split
        max_chunk_bytes: Maximum size per chunk in bytes

    Returns:
        List of chunk wire payloads. If the original payload fits within the limit,
        returns a single-element list with the original payload.
    """
    serialized = json.dumps(wire, ensure_ascii=False)
    actual_bytes = len(serialized.encode("utf-8"))

    # If payload fits, no chunking needed
    if actual_bytes <= max_chunk_bytes:
        return [wire]

    logger.info(
        "Payload exceeds chunking threshold, splitting: actual_bytes=%d max_bytes=%d request_id=%s",
        actual_bytes,
        max_chunk_bytes,
        wire.get("request_id"),
    )

    # Generate unique chunk_id
    chunk_id = f"{wire.get('request_id', 'unknown')}_{uuid.uuid4().hex[:8]}"

    # Calculate available space for content after accounting for wrapper overhead
    # Start with a conservative estimate and adjust if needed
    content_char_limit = max_chunk_bytes // 4  # Conservative: assume 4 bytes per char
    chunks: list[dict[str, Any]] = []

    # Build routing keys dict (preserved in every chunk)
    routing = {}
    for key in _ROUTING_KEYS:
        if key in wire and wire[key] is not None:
            routing[key] = wire[key]

    # Preserve original metadata (e.g., E2A_WIRE_SERVER_PUSH_KEY)
    original_metadata = wire.get("metadata") or {}

    # Build shared context for all chunks
    context = ChunkBuildContext(
        routing=routing,
        original_metadata=original_metadata,
        chunk_id=chunk_id,
    )

    # Split the JSON string into pieces
    offset = 0
    chunk_index = 0

    while offset < len(serialized):
        # Try to fit content within the budget
        piece = serialized[offset: offset + content_char_limit]
        piece_bytes = len(piece.encode("utf-8"))

        # Build a test chunk to check size
        test_chunk = context.build_chunk(chunk_index, piece)
        test_serialized = json.dumps(test_chunk, ensure_ascii=False)
        test_bytes = len(test_serialized.encode("utf-8"))

        # If test chunk exceeds budget, reduce content size
        while test_bytes > max_chunk_bytes and len(piece) > 1:
            # Reduce by 10% and retry
            content_char_limit = int(content_char_limit * 0.9)
            piece = serialized[offset: offset + content_char_limit]
            piece_bytes = len(piece.encode("utf-8"))
            test_chunk = context.build_chunk(chunk_index, piece)
            test_serialized = json.dumps(test_chunk, ensure_ascii=False)
            test_bytes = len(test_serialized.encode("utf-8"))

        # If still too large even with 1 char, we can't chunk further
        if test_bytes > max_chunk_bytes:
            logger.error(
                "Cannot chunk payload further: single char exceeds budget. "
                "request_id=%s chunk_index=%d",
                wire.get("request_id"),
                chunk_index,
            )
            # Return original payload (will fail size check downstream)
            return [wire]

        chunks.append(test_chunk)
        offset += len(piece)
        chunk_index += 1

    # Update total_chunks in all chunks
    total_chunks = len(chunks)
    for chunk in chunks:
        chunk["metadata"][CHUNKING_META_KEY]["total_chunks"] = total_chunks

    logger.info(
        "Split payload into %d chunks: chunk_id=%s request_id=%s",
        total_chunks,
        chunk_id,
        wire.get("request_id"),
    )
    return chunks


class WireChunkBuffer:
    """Buffer for assembling chunked wire messages.

    Manages multiple in-progress chunk assemblies and handles timeouts.
    """

    def __init__(
        self,
        timeout_seconds: float = CHUNK_ASSEMBLY_TIMEOUT_SECONDS,
        max_pending: int = MAX_PENDING_ASSEMBLIES,
    ) -> None:
        self._assemblies: dict[str, ChunkAssembly] = {}
        self._timeout_seconds = timeout_seconds
        self._max_pending = max_pending
        self._lock = asyncio.Lock()

    async def add_chunk(
        self,
        chunk: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any] | None:
        """Add a chunk to the buffer.

        Args:
            chunk: The chunk wire payload (already parsed from JSON)
            request_id: The request_id for routing

        Returns:
            The reassembled complete message (parsed from JSON) if all chunks
            are present, None otherwise.
        """
        chunk_meta = get_chunk_metadata(chunk)
        if chunk_meta is None:
            # Not a chunked message, return as-is
            return chunk

        content = chunk.get(CHUNK_CONTENT_KEY, "")
        if not isinstance(content, str):
            logger.warning(
                "Chunk content is not a string: chunk_id=%s chunk_index=%d",
                chunk_meta.chunk_id,
                chunk_meta.chunk_index,
            )
            return None

        async with self._lock:
            # Get or create assembly
            assembly = self._assemblies.get(chunk_meta.chunk_id)
            if assembly is None:
                # Check if buffer is full before creating new assembly
                if len(self._assemblies) >= self._max_pending:
                    # Remove oldest assembly to make room
                    await self._evict_oldest_assembly()

                assembly = ChunkAssembly(
                    chunk_id=chunk_meta.chunk_id,
                    total_chunks=chunk_meta.total_chunks,
                    request_id=request_id,
                )
                self._assemblies[chunk_meta.chunk_id] = assembly

            # Add piece
            assembly.add_piece(chunk_meta.chunk_index, content)

            # Check if complete
            if assembly.is_complete():
                # Remove from buffer
                del self._assemblies[chunk_meta.chunk_id]
                # Reassemble
                json_str = assembly.reassemble()
                if json_str is not None:
                    try:
                        reassembled = json.loads(json_str)
                        logger.info(
                            "Reassembled chunked message: chunk_id=%s chunks=%d request_id=%s",
                            chunk_meta.chunk_id,
                            chunk_meta.total_chunks,
                            request_id,
                        )
                        return reassembled
                    except json.JSONDecodeError as e:
                        logger.error(
                            "Failed to parse reassembled chunked message: chunk_id=%s error=%s",
                            chunk_meta.chunk_id,
                            e,
                        )
                        return None

            return None

    async def _evict_oldest_assembly(self) -> None:
        """Remove the oldest assembly from buffer to prevent memory exhaustion.

        This method should be called when buffer is full and a new assembly needs to be created.
        Must be called with self._lock held.
        """
        if not self._assemblies:
            return

        # Find the oldest assembly by created_at timestamp
        oldest_chunk_id = min(
            self._assemblies.keys(),
            key=lambda k: self._assemblies[k].created_at
        )
        assembly = self._assemblies.pop(oldest_chunk_id)
        logger.warning(
            "Buffer full, evicted oldest incomplete assembly: chunk_id=%s received=%d/%d request_id=%s",
            oldest_chunk_id,
            len(assembly.pieces),
            assembly.total_chunks,
            assembly.request_id,
        )

    async def cleanup_expired(self) -> None:
        """Remove expired assemblies that haven't completed."""
        async with self._lock:
            now = time.time()
            expired = [
                chunk_id
                for chunk_id, assembly in self._assemblies.items()
                if now - assembly.created_at > self._timeout_seconds
            ]
            for chunk_id in expired:
                assembly = self._assemblies.pop(chunk_id)
                logger.warning(
                    "Cleaned up expired chunk assembly: chunk_id=%s received=%d/%d request_id=%s",
                    chunk_id,
                    len(assembly.pieces),
                    assembly.total_chunks,
                    assembly.request_id,
                )

    async def clear(self) -> None:
        """Clear all assemblies (e.g., on disconnect)."""
        async with self._lock:
            self._assemblies.clear()

    def pending_count(self) -> int:
        """Return the number of pending assemblies."""
        return len(self._assemblies)
