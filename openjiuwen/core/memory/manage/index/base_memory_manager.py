# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod, ABC
from typing import Any, Tuple, List

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.common.crypto import encrypt, decrypt, NONCE_LENGTH, TAG_LENGTH

from openjiuwen.core.memory.manage.mem_model.memory_unit import BaseMemoryUnit
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class BaseMemoryManager(ABC):
    """
    Simplified abstract base class for memory manager implementations.
    Managing a specific type of memory data.
    """

    NONCE_HEX_LENGTH = NONCE_LENGTH * 2  # hex_length = bytes_length * 2
    TAG_HEX_LENGTH = TAG_LENGTH * 2  # hex_length = bytes_length * 2

    @abstractmethod
    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                           llm: Tuple[str, Model] | None = None, **kwargs):
        """add memories in batch."""
        pass

    @abstractmethod
    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs):
        """update memory by its id."""
        pass

    @abstractmethod
    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        """delete memory by its id."""
        pass

    @abstractmethod
    async def delete_by_user_id(self, user_id: str, scope_id: str, **kwargs):
        """delete memory by user id and app id."""
        pass

    @abstractmethod
    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        """get memory by its id."""
        pass

    @abstractmethod
    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        """query memory, return top k results"""
        pass

    @staticmethod
    def encrypt_memory_if_needed(key: bytes, plaintext: str) -> str:
        if not key or not plaintext:
            return plaintext

        try:
            encrypt_memory, nonce, tag = encrypt(key=key, plaintext=plaintext)
            return f"{nonce}{tag}{encrypt_memory}"
        except ValueError as e:
            memory_logger.warning(
                "Encrypt exception occurred",
                exception=str(e),
                event_type=LogEventType.MEMORY_PROCESS,
            )
            return ""
        except Exception as e:
            memory_logger.warning(
                "Encrypt error occurred",
                exception=str(e),
                event_type=LogEventType.MEMORY_PROCESS,
            )
            return ""

    @staticmethod
    def decrypt_memory_if_needed(key: bytes, ciphertext: str) -> str:
        if not key or not ciphertext:
            return ciphertext

        nonce_and_tag_len = BaseMemoryManager.NONCE_HEX_LENGTH + BaseMemoryManager.TAG_HEX_LENGTH
        if len(ciphertext) < nonce_and_tag_len:
            memory_logger.warning(
                "Decryption error occurred: invalid ciphertext",
                event_type=LogEventType.MEMORY_PROCESS,
                metadata={"ciphertext_len": len(ciphertext)}
            )
            return ""

        nonce = ciphertext[0:BaseMemoryManager.NONCE_HEX_LENGTH]
        tag = ciphertext[BaseMemoryManager.NONCE_HEX_LENGTH:nonce_and_tag_len]
        encrypt_memory = ciphertext[nonce_and_tag_len:]
        try:
            return decrypt(key=key, ciphertext=encrypt_memory, nonce=nonce, tag=tag)
        except ValueError as e:
            memory_logger.warning(
                "Decrypt exception occurred",
                event_type=LogEventType.MEMORY_PROCESS,
                exception=str(e)
            )
            return ""
        except Exception as e:
            memory_logger.warning(
                "Decrypt error occurred",
                event_type=LogEventType.MEMORY_PROCESS,
                exception=str(e)
            )
            return ""
