# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from collections import defaultdict
from typing import Dict, Optional

from openjiuwen.core.graph.store.base import (
    Store,
    GraphState
)


class InMemoryStore(Store):
    def __init__(self) -> None:
        # store latest graph state： { session_id: { ns: state } }
        self.store_ck: defaultdict[str, Dict[str, GraphState]] = defaultdict(dict)

    async def get(self, session_id: str, ns: str) -> Optional[GraphState]:
        return copy.deepcopy(self.store_ck.get(session_id, {}).get(ns))

    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        # store the state object directly
        self.store_ck[session_id][ns] = copy.deepcopy(state)

    async def delete(self, session_id: str, ns: Optional[str] = None) -> None:
        if session_id not in self.store_ck:
            return  # Conversation ID doesn't exist, nothing to delete

        if ns is None:
            # Delete all namespaces for this session_id
            del self.store_ck[session_id]
        else:
            # Delete specific namespace by prefix under session_id
            InMemoryStore._delete_ns_by_prefix(self.store_ck[session_id], ns)

            # If session_id becomes empty after deletion, clean it up
            if not self.store_ck[session_id]:
                del self.store_ck[session_id]

    @staticmethod
    def _delete_ns_by_prefix(sub_map: Dict[str, GraphState], prefix: str) -> None:
        ns_to_delete_list = [ns_to_delete for ns_to_delete in sub_map.keys() if ns_to_delete.startswith(prefix)]
        if not ns_to_delete_list:
            return

        for ns in ns_to_delete_list:
            del sub_map[ns]
