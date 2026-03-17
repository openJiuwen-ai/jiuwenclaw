# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from collections import defaultdict
from typing import Any, List, Set, Dict

from openjiuwen.core.graph.pregel.base import Message, TriggerMessage, BarrierMessage, Channel
from openjiuwen.core.graph.pregel.constants import END


class ChannelManager:
    def __init__(self, channels: list[Channel]):
        # write message index: Message.targets -> Channel
        self.map_key_to_channel: Dict[str, Channel] = {}

        # read node index: Node.node_name -> List[Channel]
        self.map_node_to_channels: Dict[str, List[Channel]] = defaultdict(list)

        # updated node
        self._ready_node_names: Set[str] = set()
        # message to next step
        self.buffer: list[Message] = []

        for ch in channels:
            self.map_key_to_channel[ch.key] = ch
            self.map_node_to_channels[ch.node_name].append(ch)
            # recover
            if ch.is_ready():
                self._ready_node_names.add(ch.node_name)

    def buffer_message(self, msg: Message) -> None:
        self.buffer.append(msg)

    def is_empty(self) -> bool:
        return len(self.buffer) == 0

    def flush(self) -> None:
        updated_nodes = set()

        for msg in self.buffer:
            ch = self.map_key_to_channel.get(msg.target)
            if not ch:
                raise ValueError(f"Channel not found for target key: '{msg.target}'")

            changed = ch.accept(msg)

            if changed:
                updated_nodes.add(ch.node_name)

        self.buffer.clear()

        for node_name in updated_nodes:
            channels = self.map_node_to_channels[node_name]
            # any trigger or barrier channel is ready, node is ready
            if any(c.is_ready() for c in channels):
                self._ready_node_names.add(node_name)

    def get_ready_nodes(self) -> list[str]:
        return list(self._ready_node_names)

    def consume(self, node_name: str):
        if node_name not in self.map_node_to_channels:
            return

        channels = self.map_node_to_channels[node_name]

        for ch in channels:
            if ch.is_ready():
                ch.consume()

        self._ready_node_names.discard(node_name)
        return

    def snapshot(self) -> Dict[str, Any]:
        snap = {}
        for name, chs in self.map_node_to_channels.items():
            if name == END:
                continue
            node_channels_snap = [c.snapshot() for c in chs]
            has_state = any(state is not None and state != [] and state != {} for state in node_channels_snap)
            if has_state:
                snap[name] = node_channels_snap

        return snap

    def restore(self, snapshot: Dict[str, Any]):
        for node_name, channel_states in snapshot.items():
            if node_name in self.map_node_to_channels:
                channels = self.map_node_to_channels[node_name]

                if len(channels) == len(channel_states):
                    for channel, state in zip(channels, channel_states):

                        if state is not None and state != [] and state != {}:
                            if hasattr(channel, 'restore'):
                                channel.restore(state)

                                if channel.is_ready():
                                    self._ready_node_names.add(node_name)


class TriggerChannel(Channel):
    def __init__(self, name: str):
        super().__init__(name)
        self.messages: List[TriggerMessage] = []

    def is_ready(self) -> bool:
        return len(self.messages) > 0

    def accept(self, msg: Message) -> bool:
        if isinstance(msg, TriggerMessage):
            self.messages.append(msg)
            return True
        return False

    def consume(self):
        # node result payloads are mostly empty
        self.messages.clear()

    def snapshot(self):
        return list(self.messages)

    def restore(self, state):
        self.messages = list(state)


class BarrierChannel(Channel):
    def __init__(self, node_name: str, expected: Set[str]):
        # node_name = "collect" (Node Name)
        super().__init__(node_name)
        self.expected = expected
        self.received: Set[str] = set()
        self._router_key = self._make_router_key(node_name, expected)

    @property
    def key(self) -> str:
        return self._router_key

    @property
    def node_name(self) -> str:
        return self.name

    @staticmethod
    def _make_router_key(name: str, expected: Set[str]) -> str:
        senders = "|".join(sorted(list(expected)))
        return f"barrier:{senders}->{name}"

    def is_ready(self) -> bool:
        return self.received == self.expected

    def accept(self, msg: Message) -> bool:
        if isinstance(msg, BarrierMessage):
            if msg.sender not in self.received:
                self.received.add(msg.sender)
                return True
        return False

    def consume(self) -> None:
        self.received.clear()
        return None

    def snapshot(self):
        return list(self.received)

    def restore(self, snapshot):
        self.received = set(snapshot)
