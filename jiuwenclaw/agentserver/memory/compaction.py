# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory Compaction Manager - Automatic message compression and summarization.

Provides automatic triggering of message compression when token threshold is exceeded.
"""

import asyncio
import json
import logging
import os
from enum import Enum
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .internal import estimate_tokens

logger = logging.getLogger(__name__)

MEMORY_COMPACT_THRESHOLD = 8000
MEMORY_COMPACT_KEEP_RECENT = 10
COMPACTED_SUMMARY_FILE = "compacted_summary.json"


class MessageMark(Enum):
    """Message compression status marks."""
    NONE = "none"
    COMPRESSED = "compressed"


@dataclass
class Message:
    """Message with compression tracking."""
    id: str
    role: str
    content: str
    timestamp: str = ""
    mark: MessageMark = MessageMark.NONE
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "mark": self.mark.value
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        return cls(
            id=data["id"],
            role=data["role"],
            content=data["content"],
            timestamp=data.get("timestamp", ""),
            mark=MessageMark(data.get("mark", "none"))
        )


class TokenCounter:
    """Token counter for messages using unified estimate_tokens."""
    
    @classmethod
    def count_text(cls, text: str) -> int:
        return estimate_tokens(text)
    
    @classmethod
    def count_message(cls, message: Dict[str, Any]) -> int:
        content = message.get("content", "")
        if isinstance(content, str):
            return cls.count_text(content)
        elif isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total += cls.count_text(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        total += cls.count_text(json.dumps(block.get("input", {})))
                    elif block.get("type") == "tool_result":
                        total += cls.count_text(str(block.get("output", "")))
            return total
        return 0
    
    @classmethod
    def count_messages(cls, messages: List[Dict[str, Any]]) -> int:
        return sum(cls.count_message(msg) for msg in messages)


class MessageStore:
    """Manages message storage with compression tracking."""
    
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.store_path = os.path.join(workspace_dir, "memory", "messages.json")
        self.summary_path = os.path.join(workspace_dir, "memory", COMPACTED_SUMMARY_FILE)
        self._messages: List[Message] = []
        self._compressed_summary: str = ""
        self._load()
    
    def _load(self) -> None:
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._messages = [Message.from_dict(m) for m in data.get("messages", [])]
            except Exception as e:
                logger.warning(f"Failed to load messages: {e}")
        
        if os.path.exists(self.summary_path):
            try:
                with open(self.summary_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._compressed_summary = data.get("summary", "")
            except Exception as e:
                logger.warning(f"Failed to load compressed summary: {e}")
    
    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump({
                "messages": [m.to_dict() for m in self._messages]
            }, f, ensure_ascii=False, indent=2)
    
    def _save_summary(self) -> None:
        os.makedirs(os.path.dirname(self.summary_path), exist_ok=True)
        
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump({
                "summary": self._compressed_summary,
                "updated_at": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def add_message(self, role: str, content: str, msg_id: Optional[str] = None) -> Message:
        import uuid
        
        message = Message(
            id=msg_id or str(uuid.uuid4()),
            role=role,
            content=content,
            timestamp=datetime.now().isoformat(),
            mark=MessageMark.NONE
        )
        self._messages.append(message)
        self._save()
        return message
    
    def get_messages(
        self,
        exclude_mark: Optional[MessageMark] = None,
        include_mark: Optional[MessageMark] = None,
        prepend_summary: bool = True
    ) -> List[Dict[str, Any]]:
        result = []
        
        if prepend_summary and self._compressed_summary:
            result.append({
                "role": "system",
                "content": self._compressed_summary
            })
        
        for msg in self._messages:
            if exclude_mark and msg.mark == exclude_mark:
                continue
            if include_mark and msg.mark != include_mark:
                continue
            
            result.append({
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp
            })
        
        return result
    
    def get_uncompressed_messages(self) -> List[Message]:
        return [m for m in self._messages if m.mark == MessageMark.NONE]
    
    def update_compressed_summary(self, summary: str) -> None:
        self._compressed_summary = summary
        self._save_summary()
        logger.info("Updated compressed summary")
    
    def get_compressed_summary(self) -> str:
        return self._compressed_summary
    
    def mark_messages(self, msg_ids: List[str], mark: MessageMark) -> int:
        count = 0
        for msg in self._messages:
            if msg.id in msg_ids:
                msg.mark = mark
                count += 1
        self._save()
        return count
    
    def clear(self) -> None:
        self._messages = []
        self._compressed_summary = ""
        self._save()
        self._save_summary()
    
    @property
    def message_count(self) -> int:
        return len(self._messages)
    
    @property
    def uncompressed_count(self) -> int:
        return len(self.get_uncompressed_messages())


class CompactionManager:
    """Manages automatic message compression."""
    
    def __init__(
        self,
        workspace_dir: str,
        threshold: int = MEMORY_COMPACT_THRESHOLD,
        keep_recent: int = MEMORY_COMPACT_KEEP_RECENT
    ):
        self.workspace_dir = workspace_dir
        self.threshold = threshold
        self.keep_recent = keep_recent
        self.message_store = MessageStore(workspace_dir)
        self._compaction_callbacks: List = []
    
    def add_compaction_callback(self, callback) -> None:
        self._compaction_callbacks.append(callback)
    
    async def _notify_compaction(self, summary: str, compacted_count: int) -> None:
        for callback in self._compaction_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(summary, compacted_count)
                else:
                    callback(summary, compacted_count)
            except Exception as e:
                logger.error(f"Compaction callback failed: {e}")
    
    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        if len(messages) <= self.keep_recent:
            return False
        
        messages_to_compact = messages[:-self.keep_recent] if self.keep_recent > 0 else messages
        estimated_tokens = TokenCounter.count_messages(messages_to_compact)
        
        return estimated_tokens > self.threshold
    
    async def check_and_compact(self, memory_manager) -> Optional[str]:
        messages = self.message_store.get_messages(
            exclude_mark=MessageMark.COMPRESSED,
            prepend_summary=False
        )
        
        if not self.should_compact(messages):
            return None
        
        return await self.do_compact(memory_manager)
    
    async def do_compact(self, memory_manager) -> str:
        messages = self.message_store.get_uncompressed_messages()
        
        if len(messages) <= self.keep_recent:
            logger.debug("Not enough messages to compact")
            return ""
        
        messages_to_compact = messages[:-self.keep_recent] if self.keep_recent > 0 else messages
        messages_to_keep = messages[-self.keep_recent:] if self.keep_recent > 0 else []
        
        compact_dicts = [{
            "role": m.role,
            "content": m.content,
            "timestamp": m.timestamp
        } for m in messages_to_compact]
        
        estimated_tokens = TokenCounter.count_messages(compact_dicts)
        
        logger.info(
            "Memory compaction triggered: estimated %d tokens "
            "(threshold: %d), compactable_msgs: %d, keep_recent_msgs: %d",
            estimated_tokens,
            self.threshold,
            len(messages_to_compact),
            len(messages_to_keep)
        )
        
        previous_summary = self.message_store.get_compressed_summary()
        
        from .summarizer import compact_memory
        compacted = await compact_memory(
            messages=compact_dicts,
            previous_summary=previous_summary
        )
        
        self.message_store.update_compressed_summary(compacted)
        
        compacted_ids = [m.id for m in messages_to_compact]
        marked_count = self.message_store.mark_messages(compacted_ids, MessageMark.COMPRESSED)
        
        logger.info(f"Marked {marked_count} messages as compacted")
        
        memory_manager.add_async_summary_task(
            messages=compact_dicts,
            date=datetime.now().strftime("%Y-%m-%d")
        )
        
        await self._notify_compaction(compacted, len(messages_to_compact))
        
        return compacted
    
    def add_message(self, role: str, content: str) -> Message:
        return self.message_store.add_message(role, content)
    
    def get_messages_for_context(self) -> List[Dict[str, Any]]:
        return self.message_store.get_messages(
            exclude_mark=MessageMark.COMPRESSED,
            prepend_summary=True
        )
    
    def get_compressed_summary(self) -> str:
        return self.message_store.get_compressed_summary()
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_messages": self.message_store.message_count,
            "uncompressed_messages": self.message_store.uncompressed_count,
            "threshold": self.threshold,
            "keep_recent": self.keep_recent,
            "has_compressed_summary": bool(self.message_store.get_compressed_summary())
        }
