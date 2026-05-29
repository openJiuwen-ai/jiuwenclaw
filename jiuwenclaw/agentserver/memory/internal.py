# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Internal utilities for memory system."""

import hashlib
import math
import os
import re
import unicodedata
from typing import List, Dict, Any, Optional, Set

import jieba

from .types import MemoryChunk


def estimate_tokens(text: str) -> int:
    """Estimate token count for text.
    
    Uses simple character-based estimation: ~4 characters per token.
    """
    if not text:
        return 0
    return len(text) // 4


def ensure_dir(path: str) -> None:
    """Ensure directory exists."""
    if path:
        os.makedirs(path, exist_ok=True)


def list_memory_files(
    workspace_dir: str,
    extra_paths: Optional[List[str]] = None
) -> List[str]:
    """List all memory files in workspace."""
    files = []
    extra_paths = extra_paths or []
    
    for filename in ["MEMORY.md", "memory.md"]:
        filepath = os.path.join(workspace_dir, filename)
        if os.path.isfile(filepath):
            files.append(filepath)
    
    memory_dir = os.path.join(workspace_dir, "memory")
    if os.path.isdir(memory_dir):
        for root, _, filenames in os.walk(memory_dir):
            for f in filenames:
                if f.endswith(".md"):
                    files.append(os.path.join(root, f))
    
    for extra in extra_paths:
        full_path = os.path.join(workspace_dir, extra)
        if os.path.isfile(full_path):
            files.append(full_path)
        elif os.path.isdir(full_path):
            for root, _, filenames in os.walk(full_path):
                for f in filenames:
                    if f.endswith(".md"):
                        files.append(os.path.join(root, f))
    
    return sorted(set(files))


async def build_file_entry(abs_path: str, workspace_dir: str) -> Dict[str, Any]:
    """Build file entry for indexing."""
    stat = os.stat(abs_path)
    
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    
    content_hash = hash_text(content)
    
    return {
        "path": os.path.relpath(abs_path, workspace_dir),
        "absPath": abs_path,
        "hash": content_hash,
        "mtimeMs": int(stat.st_mtime * 1000),
        "size": stat.st_size
    }


def chunk_markdown(
    content: str,
    settings: Optional[Dict[str, Any]] = None
) -> List[MemoryChunk]:
    """Chunk markdown content by tokens."""
    settings = settings or {}
    target_tokens = settings.get("tokens", 256)
    overlap = settings.get("overlap", 32)
    
    lines = content.split("\n")
    chunks = []
    current_chunk = []
    current_tokens = 0
    start_line = 1
    
    for i, line in enumerate(lines, 1):
        line_tokens = estimate_tokens(line)
        
        if current_tokens + line_tokens > target_tokens and current_chunk:
            chunk_text = "\n".join(current_chunk)
            chunks.append(MemoryChunk(
                text=chunk_text,
                startLine=start_line,
                endLine=i - 1
            ))
            
            overlap_lines = []
            overlap_tokens = 0
            for j in range(len(current_chunk) - 1, -1, -1):
                lt = estimate_tokens(current_chunk[j])
                if overlap_tokens + lt > overlap:
                    break
                overlap_lines.insert(0, current_chunk[j])
                overlap_tokens += lt
            
            current_chunk = overlap_lines
            current_tokens = overlap_tokens
            start_line = i - len(overlap_lines)
        
        current_chunk.append(line)
        current_tokens += line_tokens
    
    if current_chunk:
        chunk_text = "\n".join(current_chunk)
        chunks.append(MemoryChunk(
            text=chunk_text,
            startLine=start_line,
            endLine=len(lines)
        ))
    
    return chunks


def hash_text(text: str) -> str:
    """Generate hash for text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


_STOPWORDS: Optional[Set[str]] = None


def _load_stopwords() -> Set[str]:
    """Load stop words from data file (singleton)."""
    global _STOPWORDS
    if _STOPWORDS is not None:
        return _STOPWORDS

    # stopwords file lives in jiuwenclaw/resources/
    resources_dir = os.path.join(os.path.dirname(__file__), "..", "..", "resources")
    filepath = os.path.join(resources_dir, "stopwords_zh.txt")

    words: Set[str] = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word:
                    words.add(word)
    except FileNotFoundError:
        pass

    _STOPWORDS = words
    return words


def _is_valid_fts_token(token: str) -> bool:
    """Check if a token is meaningful enough for FTS indexing/searching."""
    token = token.strip()
    if not token:
        return False
    # Filter stop words (applies to all lengths)
    if token in _load_stopwords():
        return False
    return True


def parse_embedding(data: Any) -> Optional[List[float]]:
    """Parse embedding from API response."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "embedding" in data:
            return parse_embedding(data["embedding"])
        if "data" in data and isinstance(data["data"], list):
            if data["data"] and "embedding" in data["data"][0]:
                return data["data"][0]["embedding"]
    return None


def tokenize_for_fts(text: str, save: bool) -> str:
    """Tokenize text for FTS5 indexing/searching.

    Uses jieba for Chinese text segmentation, then filters out
    punctuation, symbols, and stop words.
    """
    if save:
        # 存储时，需要切分的更加精细化，提高搜索的召回率
        tokens = jieba.cut_for_search(text.strip())
    else:
        tokens = jieba.cut(text.strip())
    return " ".join(t for t in tokens if _is_valid_fts_token(t))


def build_fts_query(query: str) -> str:
    """Build FTS5 query from user input."""
    tokenized = tokenize_for_fts(query, False)
    if not tokenized:
        return ""
    tokens = tokenized.split()
    return " OR ".join(f'"{t}"' for t in tokens[:10])


def bm25_rank_to_score(rank: float) -> float:
    """Convert BM25 rank to similarity score (0-1)."""
    if rank >= 0:
        return 1.0 / (1.0 + rank)
    return 1.0 / (1.0 - rank)


def is_memory_path(rel_path: str) -> bool:
    """Check if path is a memory file."""
    basename = os.path.basename(rel_path).lower()
    if basename in ("memory.md", "memory"):
        return True

    parts = rel_path.replace("\\", "/").split("/")
    if "memory" in [p.lower() for p in parts]:
        return True

    return False


def is_daily_memory_file(rel_path: str) -> bool:
    """Check if path is a daily memory file (YYYY-MM-DD.md) under memory/daily_memory/."""
    basename = os.path.basename(rel_path)
    if not re.match(r'^\d{4}-\d{2}-\d{2}\.md$', basename):
        return False
    parts = rel_path.replace("\\", "/").split("/")
    return "daily_memory" in [p.lower() for p in parts]


def list_daily_memory_files(
    workspace_dir: str,
) -> List[str]:
    """List only daily memory files (memory/daily_memory/YYYY-MM-DD.md) in workspace."""
    files = []
    daily_memory_dir = os.path.join(workspace_dir, "memory", "daily_memory")
    if os.path.isdir(daily_memory_dir):
        for f in os.listdir(daily_memory_dir):
            if re.match(r'^\d{4}-\d{2}-\d{2}\.md$', f):
                files.append(os.path.join(daily_memory_dir, f))
    return sorted(set(files))


def normalize_extra_memory_paths(
    paths: Optional[List[str]],
    workspace_dir: str
) -> List[str]:
    """Normalize extra memory paths."""
    if not paths:
        return []
    
    normalized = []
    for p in paths:
        if os.path.isabs(p):
            normalized.append(p)
        else:
            normalized.append(os.path.join(workspace_dir, p))
    
    return normalized


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if len(vec1) != len(vec2):
        return 0.0
    
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    
    if norm1 < 1e-10 or norm2 < 1e-10:
        return 0.0
    
    return dot / (norm1 * norm2)
