# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Any, Callable, Awaitable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import graph_logger as logger
from openjiuwen.core.common.logging import LogEventType
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.workflow.workflow_config import WorkflowSpec
from openjiuwen.core.session import Transformer, get_by_schema, STREAM_INPUT_GEN_TIMEOUT_KEY
from openjiuwen.core.session.stream import AsyncStreamQueue
from openjiuwen.core.graph.stream_actor.base import StreamActor, StreamGraph


class StreamTransform:
    @classmethod
    def get_by_defined_transformer(cls, origin_message: dict, transformer: Transformer) -> dict:
        return transformer(origin_message)

    @classmethod
    def get_by_default_transformer(cls, origin_message: dict, stream_inputs_schema: dict) -> dict:
        return get_by_schema(stream_inputs_schema, origin_message)


class ActorManager:
    def __init__(self, workflow_spec: WorkflowSpec, graph: StreamGraph, sub_graph: bool, session):
        self._stream_edges = workflow_spec.stream_edges
        self._streams: Dict[str, StreamActor] = {}
        self._streams_transform = StreamTransform()

        consumer_dict = _build_reverse_graph(self._stream_edges)
        for consumer_id, producer_ids in consumer_dict.items():
            consumer_stream_ability = [ability for ability in workflow_spec.comp_configs[consumer_id].abilities if
                                       ability in [ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]]
            sources = set()
            for producer_id in producer_ids:
                for ability in workflow_spec.comp_configs[producer_id].abilities:
                    if ability in [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]:
                        sources.add(f"{producer_id}-{ability.name}")

            self._streams[consumer_id] = StreamActor(consumer_id, graph.get_node(consumer_id),
                                                     consumer_stream_ability, list(sources),
                                                     stream_generator_timeout=session.config().get_env(
                                                         STREAM_INPUT_GEN_TIMEOUT_KEY))

        self._sub_graph = sub_graph
        self._sub_workflow_stream = AsyncStreamQueue(maxsize=10 * 1024) if sub_graph else None

    def sub_workflow_stream(self) -> AsyncStreamQueue:
        if not self._sub_graph:
            raise build_error(StatusCode.GRAPH_STREAM_ACTOR_EXECUTION_ERROR,
                              reason=f"only sub graph has sub_workflow_stream")
        return self._sub_workflow_stream

    def _get_actor(self, consumer_id: str) -> StreamActor:
        return self._streams[consumer_id]

    @property
    def stream_transform(self):
        return self._streams_transform

    async def produce(self, producer_id: str, message_content: Any,
                      ability: ComponentAbility, first_frame: bool = False):
        consumer_ids = self._stream_edges.get(producer_id)
        if consumer_ids:
            for consumer_id in consumer_ids:
                actor = self._get_actor(consumer_id)
                await actor.send({producer_id: message_content}, ability, first_frame=first_frame,
                                 producer_id=producer_id)
        else:
            logger.warning(
                f"Discard chunk send from [{producer_id}] to none consumer",
                event_type=LogEventType.GRAPH_SEND_STREAM_CHUNK,
                chunk=message_content
            )

    async def end_message(self, producer_id: str, ability: ComponentAbility):
        end_message_content = f"END_{producer_id}"
        await self.produce(producer_id, end_message_content, ability)

    async def consume(self, consumer_id: str, ability: ComponentAbility, schema: dict,
                      stream_callback: Callable[[dict], Awaitable[None]] = None) -> dict:
        actor = self._get_actor(consumer_id)
        return await actor.generator(ability, schema, stream_callback)

    async def shutdown(self):
        for actor in self._streams.values():
            await actor.shutdown()


def _build_reverse_graph(graph):
    reverse_graph = {}

    for source, targets in graph.items():
        for target in targets:
            if target not in reverse_graph:
                reverse_graph[target] = []
            reverse_graph[target].append(source)

    return reverse_graph
