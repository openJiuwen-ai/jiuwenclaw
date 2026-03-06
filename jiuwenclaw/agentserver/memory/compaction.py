# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Context Compression Manager - Automatic message compression and summarization.

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

CONTEXT_COMPACT_THRESHOLD = 8000
CONTEXT_COMPACT_KEEP_RECENT = 10
COMPRESSED_SUMMARY_FILE = "compressed_summary.json"


class MessageStatus(Enum):
    """Message compression status marks."""
    PENDING = "pending"
    ARCHIVED = "archived"


@dataclass
class MessageRecord:
    """Message with compression tracking."""
    msg_id: str
    role: str
    content: str
    created_at: str = ""
    status: MessageStatus = MessageStatus.PENDING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "status": self.status.value
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageRecord":
        return cls(
            msg_id=data["msg_id"],
            role=data["role"],
            content=data["content"],
            created_at=data.get("created_at", ""),
            status=MessageStatus(data.get("status", "pending"))
        )


class TokenEstimator:
    """Token estimator for messages using unified estimate_tokens."""

    @classmethod
    def estimate_text(cls, text: str) -> int:
        return estimate_tokens(text)

    @classmethod
    def estimate_message(cls, message: Dict[str, Any]) -> int:
        content = message.get("content", "")
        if isinstance(content, str):
            return cls.estimate_text(content)
        elif isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total += cls.estimate_text(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        total += cls.estimate_text(json.dumps(block.get("input", {})))
                    elif block.get("type") == "tool_result":
                        total += cls.estimate_text(str(block.get("output", "")))
            return total
        return 0

    @classmethod
    def estimate_messages(cls, messages: List[Dict[str, Any]]) -> int:
        return sum(cls.estimate_message(msg) for msg in messages)


class MessageRepository:
    """Manages message storage with compression tracking."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.store_path = os.path.join(workspace_dir, "memory", "messages.json")
        self.summary_path = os.path.join(workspace_dir, "memory", COMPRESSED_SUMMARY_FILE)
        self._records: List[MessageRecord] = []
        self._archived_summary: str = ""
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._records = [MessageRecord.from_dict(m) for m in data.get("records", [])]
            except Exception as e:
                logger.warning(f"Failed to load messages: {e}")

        if os.path.exists(self.summary_path):
            try:
                with open(self.summary_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._archived_summary = data.get("summary", "")
            except Exception as e:
                logger.warning(f"Failed to load compressed summary: {e}")

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)

        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump({
                "records": [r.to_dict() for r in self._records]
            }, f, ensure_ascii=False, indent=2)

    def _save_summary(self) -> None:
        os.makedirs(os.path.dirname(self.summary_path), exist_ok=True)

        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump({
                "summary": self._archived_summary,
                "updated_at": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)

    def add_record(self, role: str, content: str, msg_id: Optional[str] = None) -> MessageRecord:
        import uuid

        record = MessageRecord(
            msg_id=msg_id or str(uuid.uuid4()),
            role=role,
            content=content,
            created_at=datetime.now().isoformat(),
            status=MessageStatus.PENDING
        )
        self._records.append(record)
        self._save()
        return record

    def get_records(
            self,
            exclude_status: Optional[MessageStatus] = None,
            include_status: Optional[MessageStatus] = None,
            prepend_summary: bool = True
    ) -> List[Dict[str, Any]]:
        result = []

        if prepend_summary and self._archived_summary:
            result.append({
                "role": "system",
                "content": self._archived_summary
            })

        for rec in self._records:
            if exclude_status and rec.status == exclude_status:
                continue
            if include_status and rec.status != include_status:
                continue

            result.append({
                "id": rec.msg_id,
                "role": rec.role,
                "content": rec.content,
                "timestamp": rec.created_at
            })

        return result

    def get_pending_records(self) -> List[MessageRecord]:
        return [r for r in self._records if r.status == MessageStatus.PENDING]

    def update_archived_summary(self, summary: str) -> None:
        self._archived_summary = summary
        self._save_summary()
        logger.info("Updated archived summary")

    def get_archived_summary(self) -> str:
        return self._archived_summary

    def mark_records(self, msg_ids: List[str], status: MessageStatus) -> int:
        count = 0
        for rec in self._records:
            if rec.msg_id in msg_ids:
                rec.status = status
                count += 1
        self._save()
        return count

    def clear(self) -> None:
        self._records = []
        self._archived_summary = ""
        self._save()
        self._save_summary()

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def pending_count(self) -> int:
        return len(self.get_pending_records())


class ContextCompactionManager:
    """Manages automatic message compression."""

    def __init__(
            self,
            workspace_dir: str,
            threshold: int = CONTEXT_COMPACT_THRESHOLD,
            keep_recent: int = CONTEXT_COMPACT_KEEP_RECENT
    ):
        self.workspace_dir = workspace_dir
        self.threshold = threshold
        self.keep_recent = keep_recent
        self.message_repo = MessageRepository(workspace_dir)
        self._compaction_callbacks: List = []

    def add_compaction_callback(self, callback) -> None:
        self._compaction_callbacks.append(callback)

    async def _notify_compaction(self, summary: str, archived_count: int) -> None:
        for callback in self._compaction_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(summary, archived_count)
                else:
                    callback(summary, archived_count)
            except Exception as e:
                logger.error(f"Compaction callback failed: {e}")

    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        if len(messages) <= self.keep_recent:
            return False

        messages_to_archive = messages[:-self.keep_recent] if self.keep_recent > 0 else messages
        estimated_tokens = TokenEstimator.estimate_messages(messages_to_archive)

        return estimated_tokens > self.threshold

    async def check_and_compact(self, memory_manager) -> Optional[str]:
        messages = self.message_repo.get_records(
            exclude_status=MessageStatus.ARCHIVED,
            prepend_summary=False
        )

        if not self.should_compact(messages):
            return None

        return await self.do_compact(memory_manager)

    async def do_compact(self, memory_manager) -> str:
        records = self.message_repo.get_pending_records()

        if len(records) <= self.keep_recent:
            logger.debug("Not enough messages to compact")
            return ""

        records_to_archive = records[:-self.keep_recent] if self.keep_recent > 0 else records
        records_to_keep = records[-self.keep_recent:] if self.keep_recent > 0 else []

        archive_dicts = [{
            "role": r.role,
            "content": r.content,
            "timestamp": r.created_at
        } for r in records_to_archive]

        estimated_tokens = TokenEstimator.estimate_messages(archive_dicts)

        logger.info(
            "Context compaction triggered: estimated %d tokens "
            "(threshold: %d), archivable_records: %d, keep_recent_records: %d",
            estimated_tokens,
            self.threshold,
            len(records_to_archive),
            len(records_to_keep)
        )

        prior_summary = self.message_repo.get_archived_summary()

        from .summarizer import compact_memory
        archived = await compact_memory(
            messages=archive_dicts,
            prior_summary=prior_summary
        )

        self.message_repo.update_archived_summary(archived)

        archived_ids = [r.msg_id for r in records_to_archive]
        marked_count = self.message_repo.mark_records(archived_ids, MessageStatus.ARCHIVED)

        logger.info(f"Marked {marked_count} messages as archived")

        memory_manager.add_async_summary_task(
            messages=archive_dicts,
            date=datetime.now().strftime("%Y-%m-%d")
        )

        await self._notify_compaction(archived, len(records_to_archive))

        return archived

    def add_message(self, role: str, content: str) -> MessageRecord:
        return self.message_repo.add_record(role, content)

    def get_messages_for_context(self) -> List[Dict[str, Any]]:
        return self.message_repo.get_records(
            exclude_status=MessageStatus.ARCHIVED,
            prepend_summary=True
        )

    def get_archived_summary(self) -> str:
        return self.message_repo.get_archived_summary()

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_records": self.message_repo.record_count,
            "pending_records": self.message_repo.pending_count,
            "threshold": self.threshold,
            "keep_recent": self.keep_recent,
            "has_archived_summary": bool(self.message_repo.get_archived_summary())
        }
