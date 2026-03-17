# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import inspect
from typing import Dict, Optional, List, Union

from openjiuwen.core.common.logging import graph_logger, LogEventType
from openjiuwen.core.graph.pregel.base import Message, PregelNode, GraphInterrupt
from openjiuwen.core.graph.pregel.config import PregelConfig, InnerPregelConfig, \
    create_inner_config
from openjiuwen.core.graph.pregel.constants import TASK_STATUS_INTERRUPT, TASK_STATUS_ERROR, PARENT_NS, NS
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import WorkflowEvents


class TaskExecutorPool:
    def __init__(self, config: PregelConfig):
        self.config = config
        self.succeed_messages: List[Message] = []
        from openjiuwen.core.graph.store.base import PendingNode
        self.failed: Dict[str, PendingNode] = {}
        self.running_tasks: Dict[asyncio.Task, PregelNode] = {}

    def submit(self, node: PregelNode, version: int) -> None:
        """Submit node's execution task"""
        async_task = asyncio.create_task(NodeTask(node, self.config, version).run())
        self.running_tasks[async_task] = node

    def _commit_failure(self, node: PregelNode, exc: Exception):
        """Record failed node and exception"""
        name = node.name
        if name not in self.failed:
            status = TASK_STATUS_INTERRUPT if isinstance(exc, GraphInterrupt) else TASK_STATUS_ERROR
            from openjiuwen.core.graph.store.base import PendingNode
            self.failed[name] = PendingNode(node_name=name, status=status, exception=[exc])

    async def wait_all(self) -> None:
        """Wait and process all tasks with FIRST_EXCEPTION semantics"""
        if not self.running_tasks:
            return

        tasks = list(self.running_tasks.keys())

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

        first_err_exc: Optional[Exception] = None
        interrupt_exc: Optional[GraphInterrupt] = None

        for t in done:
            node = self.running_tasks.pop(t)

            exc = t.exception()

            if exc:
                self._commit_failure(node, exc)
                if first_err_exc is None:
                    first_err_exc = exc
            else:
                res = t.result()
                if isinstance(res, GraphInterrupt):
                    self._commit_failure(node, res)
                    if interrupt_exc is None:
                        interrupt_exc = res
                else:
                    msgs: List[Message] = res
                    self.succeed_messages.extend(msgs)
        tasks_to_cancel = list(pending)
        for t in tasks_to_cancel:
            t.cancel()
        results = await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                graph_logger.warning(
                    "Task cancelled with exception",
                    event_type=LogEventType.GRAPH_VERTEX_CALL_ERROR,
                    metadata={"error": str(result)}
                )
        for t in tasks_to_cancel:
            if t in self.running_tasks:
                node = self.running_tasks.pop(t)
                self._commit_failure(node, asyncio.CancelledError())

        # Priority: normal exception > interrupt exception
        if first_err_exc:
            raise first_err_exc
        elif interrupt_exc:
            raise interrupt_exc

    def clear(self):
        self.succeed_messages.clear()
        self.failed.clear()
        self.running_tasks.clear()

    async def cancel_all(self):
        to_cancel = []
        for t in list(self.running_tasks.keys()):
            if not t.done() and not t.cancelled():
                t.cancel()
                to_cancel.append(t)
        results = await asyncio.gather(*to_cancel, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                graph_logger.warning(f"running task with exception",
                                     event_type=LogEventType.GRAPH_VERTEX_CALL_ERROR,
                                     metadata={"error": str(result)}
                                     )
        for t in to_cancel:
            if t in self.running_tasks:
                node = self.running_tasks.pop(t)
                self._commit_failure(node, asyncio.CancelledError())
        self.running_tasks.clear()


class NodeTask:

    def __init__(self, node: PregelNode, config: PregelConfig, version: int):
        self.node = node
        self.config = config
        self.messages: List[Message] = []
        self.version = version

    async def run(self) -> Union[List[Message], GraphInterrupt]:
        """
        Execute the node.
        Returns:
            List[Message]: If success.
            GraphInterrupt: If interrupted (caught internally).
        Raises:
            Exception: If any other error occurs (propagates to cancel others).
        """
        try:
            func = self.node.func
            target_func = func.__call__ if hasattr(func, "__call__") else func
            sig = inspect.signature(func)
            kwargs = {}
            if 'config' in sig.parameters:
                inner_config: InnerPregelConfig = create_inner_config(self.config)
                current_parent_ns = inner_config.get(PARENT_NS)
                current_node_name = self.node.name
                if current_parent_ns:
                    new_full_ns = f"{current_parent_ns}:{current_node_name}:{self.version}"
                    inner_config[NS] = new_full_ns
                    inner_config[PARENT_NS] = new_full_ns
                kwargs['config'] = inner_config
            if 'state' in sig.parameters:
                kwargs['state'] = None

            if asyncio.iscoroutinefunction(func) or asyncio.iscoroutinefunction(target_func):
                await target_func(**kwargs)
            else:
                target_func(**kwargs)

            # Route messages
            self.messages = []
            for r in self.node.routers:
                msgs = await r.dispatch(source_node=self.node.name)
                self.messages.extend(msgs)

            if self.messages:
                for msg in self.messages:
                    await trigger(
                        WorkflowEvents.EDGE_TRAVERSED,
                        source_node=msg.sender,
                        target_node=msg.target,
                        graph_id=self.config.get(NS))

            return self.messages

        except GraphInterrupt as e:
            # cast exception to return value
            return e
