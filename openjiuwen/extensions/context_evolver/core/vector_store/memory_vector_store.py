# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""In-memory vector store for development and testing."""

from typing import List, Dict, Any, Optional
import numpy as np
from openjiuwen.core.common.logging import context_engine_logger as logger

from ..schema import VectorNode


class MemoryVectorStore:
    """Simple in-memory vector store using numpy for similarity search."""

    def __init__(self):
        """Initialize empty vector store."""
        self._vectors: Dict[str, VectorNode] = {}
        logger.info("Initialized MemoryVectorStore")

    async def async_upsert(self, node: VectorNode) -> None:
        """Insert or update a vector node.

        Args:
            node: VectorNode to store 
        """
        if node.embedding is None:
            raise ValueError(f"Node {node.id} has no embedding")

        self._vectors[node.id] = node
        logger.debug("Upserted vector: %s", node.id)

    async def async_search(
        self,
        embedding: List[float],
        top_k: int = 10,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[VectorNode]:
        """Search for similar vectors.

        Args:
            embedding: Query embedding
            top_k: Number of results to return
            metadata_filter: Optional metadata filter (e.g., {"workspace_id": "user123"})

        Returns:
            List of most similar VectorNodes
        """
        if not self._vectors:
            logger.debug("Vector store is empty")
            return []

        # Filter vectors by metadata if provided
        candidates = self._vectors.values()
        if metadata_filter:
            candidates = [
                v for v in candidates
                if all(v.metadata.get(k) == val for k, val in metadata_filter.items())
            ]

        if not candidates:
            logger.debug("No vectors match filter")
            return []

        # Calculate cosine similarity
        query_vec = np.array(embedding)
        query_norm = np.linalg.norm(query_vec)

        similarities = []
        for node in candidates:
            if node.embedding is None:
                continue

            vec = np.array(node.embedding)
            vec_norm = np.linalg.norm(vec)

            if vec_norm == 0 or query_norm == 0:
                similarity = 0.0
            else:
                similarity = np.dot(query_vec, vec) / (query_norm * vec_norm)

            similarities.append((similarity, node))

        # Sort by similarity (highest first)
        similarities.sort(key=lambda x: x[0], reverse=True)

        # Return top_k results
        results = [node for _, node in similarities[:top_k]]
        logger.debug("Found %s similar vectors", len(results))

        return results

    async def async_delete(self, node_id: str) -> bool:
        """Delete a vector node.

        Args:
            node_id: ID of node to delete

        Returns:
            True if deleted, False if not found
        """
        if node_id in self._vectors:
            del self._vectors[node_id]
            logger.debug("Deleted vector: %s", node_id)
            return True
        return False

    def clear(self) -> None:
        """Clear all vectors."""
        self._vectors.clear()
        logger.info("Cleared all vectors")

    def count(self) -> int:
        """Get number of stored vectors.

        Returns:
            Number of vectors
        """
        return len(self._vectors)

    def get_all(self, metadata_filter: Optional[Dict[str, Any]] = None) -> List[VectorNode]:
        """Get all stored vectors, optionally filtered by metadata.

        Args:
            metadata_filter: Optional metadata filter (e.g., {"workspace_id": "user123"})

        Returns:
            List of all VectorNodes matching the filter
        """
        if not self._vectors:
            return []

        if metadata_filter:
            return [
                v for v in self._vectors.values()
                if all(v.metadata.get(k) == val for k, val in metadata_filter.items())
            ]

        return list(self._vectors.values())

    def load_node(self, node_id: str, node: VectorNode) -> None:
        """Load a single vector node directly (for deserialization).

        Args:
            node_id: ID of the node
            node: VectorNode to load
        """
        self._vectors[node_id] = node

    async def load_from_dict(self, data: Dict[str, Dict[str, Any]]) -> None:
        """Load vectors from a dictionary of serialized nodes.

        Args:
            data: Dictionary mapping node IDs to their serialized data
        """
        for node_id, node_data in data.items():
            node = VectorNode.from_dict(node_data)
            if node.embedding is not None:
                self._vectors[node_id] = node
                logger.debug("Loaded vector: %s", node_id)

    def __repr__(self) -> str:
        """String representation."""
        return f"MemoryVectorStore(count={len(self._vectors)})"
