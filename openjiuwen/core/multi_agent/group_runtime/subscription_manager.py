# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Subscription Manager Module

Manages topic-to-agent subscription mappings with wildcard matching.
"""
from __future__ import annotations

import fnmatch
from typing import Optional

from openjiuwen.core.common.logging import multi_agent_logger as logger


class SubscriptionManager:
    """Manages topic-to-agent subscriptions with wildcard matching.

    Maintains bidirectional indices for efficient lookup and removal.
    Supports exact and wildcard (``*``, ``?``) topic patterns.
    """

    def __init__(self):
        """Initialize subscription manager."""
        self._subscriptions: dict[str, set[str]] = {}  # topic_pattern -> agent IDs
        self._agent_topics: dict[str, set[str]] = {}  # agent_id -> topic patterns

    def subscribe(self, agent_id: str, topic_pattern: str) -> None:
        """Subscribe an agent to a topic pattern.

        Args:
            agent_id: Agent ID
            topic_pattern: Topic pattern (supports ``*`` and ``?`` wildcards)
        """
        if topic_pattern not in self._subscriptions:
            self._subscriptions[topic_pattern] = set()
        self._subscriptions[topic_pattern].add(agent_id)

        if agent_id not in self._agent_topics:
            self._agent_topics[agent_id] = set()
        self._agent_topics[agent_id].add(topic_pattern)

        logger.debug(
            f"[{self.__class__.__name__}] {agent_id} subscribed to {topic_pattern}"
        )

    def unsubscribe(self, agent_id: str, topic_pattern: str) -> None:
        """Unsubscribe an agent from a topic pattern.

        Args:
            agent_id: Agent ID
            topic_pattern: Topic pattern
        """
        if topic_pattern in self._subscriptions:
            self._subscriptions[topic_pattern].discard(agent_id)
            if not self._subscriptions[topic_pattern]:
                del self._subscriptions[topic_pattern]

        if agent_id in self._agent_topics:
            self._agent_topics[agent_id].discard(topic_pattern)
            if not self._agent_topics[agent_id]:
                del self._agent_topics[agent_id]

        logger.debug(
            f"[{self.__class__.__name__}] {agent_id} unsubscribed from {topic_pattern}"
        )

    def unsubscribe_all(self, agent_id: str) -> None:
        """Remove all subscriptions for an agent.

        Args:
            agent_id: Agent ID
        """
        if agent_id not in self._agent_topics:
            return

        topics = list(self._agent_topics[agent_id])
        for topic in topics:
            if topic in self._subscriptions:
                self._subscriptions[topic].discard(agent_id)
                if not self._subscriptions[topic]:
                    del self._subscriptions[topic]

        del self._agent_topics[agent_id]

        logger.debug(
            f"[{self.__class__.__name__}] Removed all subscriptions for {agent_id}"
        )

    def get_subscribers(self, topic_id: str) -> list[str]:
        """Get all subscribers matching a topic.

        Args:
            topic_id: Topic ID to match

        Returns:
            List of matching subscriber agent IDs
        """
        subscribers = set()
        for topic_pattern, agents in self._subscriptions.items():
            if self._match_pattern(topic_id, topic_pattern):
                subscribers.update(agents)

        logger.debug(
            f"[{self.__class__.__name__}] Found {len(subscribers)} subscribers for: {topic_id}"
        )
        return list(subscribers)

    @staticmethod
    def _match_pattern(topic_id: str, pattern: str) -> bool:
        """Match a topic ID against a subscription pattern.

        Supports exact match, ``*`` (any sequence), and ``?`` (any single character).

        Args:
            topic_id: Actual topic ID
            pattern: Subscription pattern

        Returns:
            True if matches
        """
        if pattern == topic_id:
            return True
        if '*' in pattern or '?' in pattern:
            return fnmatch.fnmatch(topic_id, pattern)
        return False

    def get_subscription_count(self) -> int:
        """Get total number of subscriptions.

        Returns:
            Total subscription count
        """
        return sum(len(agents) for agents in self._subscriptions.values())

    def list_subscriptions(self, agent_id: Optional[str] = None) -> dict:
        """List subscriptions for debugging.

        Args:
            agent_id: Optional agent ID to filter by

        Returns:
            Dictionary of subscriptions
        """
        if agent_id:
            return {
                "agent_id": agent_id,
                "topics": list(self._agent_topics.get(agent_id, set()))
            }
        return {
            "subscriptions": {
                pattern: list(agents)
                for pattern, agents in self._subscriptions.items()
            }
        }
