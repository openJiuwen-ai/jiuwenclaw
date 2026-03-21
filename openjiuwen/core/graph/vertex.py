# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from asyncio import CancelledError
from typing import Any, Optional, AsyncIterator, Literal

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT, END_NODE_STREAM, INPUTS_KEY, CONFIG_KEY
from openjiuwen.core.common.exception.errors import BaseError, ExecutionError, build_error
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import graph_logger as logger
from openjiuwen.core.common.logging import LogEventType
from openjiuwen.core.graph.atomic_node import AsyncAtomicNode
from openjiuwen.core.graph.executable import Executable, Output
from openjiuwen.core.graph.graph_state import GraphState
from openjiuwen.core.session import COMP_STREAM_CALL_TIMEOUT_KEY, get_by_schema
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session import NodeSession
from openjiuwen.core.session.stream import StreamSchemas, OutputSchema
from openjiuwen.core.session.stream import StreamEmitter
from openjiuwen.core.graph.stream_actor.base import StreamConsumer
from openjiuwen.core.session.tracer import TracerWorkflowUtils
from openjiuwen.core.graph.pregel import GraphInterrupt
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import WorkflowEvents

SUB_WORKFLOW_COMPONENT = "sub_workflow"


class Vertex(AsyncAtomicNode, StreamConsumer):

    def __init__(self, node_id: str, executable: Executable = None):
        self._node_id = node_id
        self._executable = executable
        self._context = None
        self._session: NodeSession = None
        self._stream_called_timeout = 10
        # if stream_call is available, call should wait for it
        self._stream_done = asyncio.Future()
        self._call_count: int = 0
        self._stream_call_count: int = 0
        self.is_end_node = False
        self._is_started = asyncio.Event()
        self._is_call_started = asyncio.Event()
        self._node_config = None
        self._component_ability = None
        self._has_stream_call: bool = False
        self._source_id: list = []
        self._log_message = {}
        self._is_first_init = True

    def init(self, session: BaseSession, **kwargs) -> bool:
        self._session = NodeSession(session, self._node_id, type(self._executable).__name__,
                                    self._executable.skip_trace())
        self._context = kwargs.get("context")
        self._stream_called_timeout = session.config().get_env(COMP_STREAM_CALL_TIMEOUT_KEY)
        self._node_config = self._session.node_config()
        self._component_ability = (
            self._node_config.abilities) if self._node_config and self._node_config.abilities else [
            ComponentAbility.INVOKE]
        self._has_stream_call = len(self._stream_abilities()) > 0
        self._has_call = len(self._component_ability) > len(self._stream_abilities())
        self._log_message = dict(graph_id=self._session.workflow_id(), node_id=self._node_id)
        if self._is_first_init:
            node_abilities = [ability.name for ability in self._component_ability]
            logger.info(
                f"Initialized node [{self._node_id}], abilities is {node_abilities}",
                event_type=LogEventType.GRAPH_VERTEX_INIT,
                **self._log_message)
            self._is_first_init = False
        has_stream_inputs = self._has_stream_call and (
                self._node_config and self._node_config.stream_io_configs and
                self._node_config.stream_io_configs.inputs_schema is not None)
        if has_stream_inputs and hasattr(self._executable, "set_mix"):
            self._executable.set_mix()
        self._is_started.clear()
        return True

    async def _run_executable(self, ability: ComponentAbility, is_subgraph: bool = False, config: Any = None,
                              event: asyncio.Event = None) -> bool:
        try:
            logger.info(
                f"Begin to call node [{self._node_id}] ability [{ability.name}]",
                event_type=LogEventType.GRAPH_VERTEX_ABILITY_START,
                **self._log_message
            )

            def set_event():
                if event is not None:
                    event.set()

            # Simplified strategy pattern using lambda functions wrapping async execution
            async def invoke_strategy():
                batch_inputs = await self._pre_invoke()
                logger.debug(f"Prepare inputs for [{self._node_id}] ability [{ability.name}]",
                             event_type=LogEventType.GRAPH_VERTEX_ABILITY_RUNNING,
                             inputs=batch_inputs,
                             metadata={"is_subgraph": is_subgraph},
                             **self._log_message)
                if is_subgraph:
                    batch_inputs = {INPUTS_KEY: batch_inputs, CONFIG_KEY: config}
                results = await self._executable.on_invoke(batch_inputs, session=self._session, context=self._context)
                results = await self._post_invoke(results)
                logger.debug(f"Post-process results for [{self._node_id}] ability [{ability.name}]",
                             event_type=LogEventType.GRAPH_VERTEX_ABILITY_RUNNING,
                             outputs=results,
                             metadata={"is_subgraph": is_subgraph},
                             **self._log_message)

            async def stream_strategy():
                batch_inputs = await self._pre_invoke()
                logger.debug(f"Prepare inputs for [{self._node_id}] ability [{ability.name}]",
                             event_type=LogEventType.GRAPH_VERTEX_ABILITY_RUNNING,
                             inputs=batch_inputs,
                             metadata={"is_subgraph": is_subgraph},
                             **self._log_message)
                if is_subgraph:
                    batch_inputs = {INPUTS_KEY: batch_inputs, CONFIG_KEY: config}
                result_iter = self._executable.on_stream(batch_inputs, session=self._session, context=self._context)
                await self._post_stream(result_iter, ComponentAbility.STREAM)

            async def collect_strategy():
                collect_iter = await self._pre_stream(ComponentAbility.COLLECT)
                set_event()
                batch_output = await self._executable.on_collect(collect_iter, self._session, context=self._context)
                results = await self._post_invoke(batch_output)
                logger.debug(f"Post-process inputs for [{self._node_id}] ability [{ability.name}]",
                             event_type=LogEventType.GRAPH_VERTEX_ABILITY_RUNNING,
                             outputs=results,
                             metadata={"is_subgraph": is_subgraph},
                             **self._log_message)

            async def transform_strategy():
                transform_iter = None
                transform_iter = await self._pre_stream(ComponentAbility.TRANSFORM)
                set_event()
                output_iter = self._executable.on_transform(transform_iter, self._session, context=self._context)
                await self._post_stream(output_iter, ComponentAbility.TRANSFORM)

            ability_strategies = {
                ComponentAbility.INVOKE: invoke_strategy,
                ComponentAbility.STREAM: stream_strategy,
                ComponentAbility.COLLECT: collect_strategy,
                ComponentAbility.TRANSFORM: transform_strategy
            }

            # Execute strategy if found
            strategy = ability_strategies.get(ability)
            await strategy()
            logger.info(
                f"Succeed to call node [{self._node_id}] ability [{ability.name}]",
                event_type=LogEventType.GRAPH_VERTEX_ABILITY_END,
                **self._log_message
            )
            return True
        except GraphInterrupt:
            logger.info(
                f"Interrupt to call node [{self._node_id}] ability [{ability.name}]",
                event_type=LogEventType.GRAPH_VERTEX_ABILITY_END,
                **self._log_message
            )
            raise
        except BaseError as e:
            logger.error(
                f"Failed to call node [{self._node_id}] ability [{ability.name}]",
                event_type=LogEventType.GRAPH_VERTEX_ABILITY_ERROR,
                exception=e,
                error_code=e.code,
                error_msg=e.message,
                **self._log_message
            )
            raise
        except Exception as e:
            logger.error(
                f"Failed to call node [{self._node_id}]'s '{ability.name}'",
                event_type=LogEventType.GRAPH_VERTEX_ABILITY_ERROR,
                exception=e,
                **self._log_message
            )
            raise build_error(
                StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR, cause=e, ability=ability.name,
                comp=self._node_id,
                reason=e, workflow=self._session.workflow_id())
        finally:
            if event and not event.is_set():
                event.set()

    async def __call__(self, state: GraphState, config) -> Output:
        logger.info(f"Begin to call batch-in node [{self._node_id}]", event_type=LogEventType.GRAPH_VERTEX_CALL_START,
                    **self._log_message)
        try:
            if self._executable.post_commit():
                await self.atomic_invoke(config=config, session=self._session)
            else:
                await self.call(config)
            logger.info(f"Succeed to call batch-in node [{self._node_id}]",
                        event_type=LogEventType.GRAPH_VERTEX_CALL_END,
                        **self._log_message)
            node_output = self._session.state().get(self._node_id)
            # Emit node executed event with inputs and outputs
            await trigger(
                WorkflowEvents.NODE_EXECUTED,
                node_id=self._node_id,
                ability=[a.name for a in self._component_ability],
                graph_id=self._session.workflow_id(),
                inputs=state,
                outputs=node_output)

            return {"source_node_id": [self._node_id]}
        except Exception as e:
            if self._session.tracer() is not None:
                await self.__trace_error__(e)
            if isinstance(e, BaseError):
                logger.error(f"Failed to call batch-in node [{self._node_id}]",
                             event_type=LogEventType.GRAPH_VERTEX_CALL_END,
                             error_code=e.code,
                             error_msg=e.message,
                             exception=e,
                             **self._log_message)
            elif isinstance(e, GraphInterrupt):
                logger.info(f"Interrupt to call batch-in node [{self._node_id}]",
                            event_type=LogEventType.GRAPH_VERTEX_CALL_END,
                            **self._log_message)
            else:
                logger.error(f"Failed to call batch-in node [{self._node_id}]",
                             event_type=LogEventType.GRAPH_VERTEX_CALL_END,
                             exception=e,
                             **self._log_message)
            if not isinstance(e, GraphInterrupt):
                # Emit node error event
                await trigger(
                    WorkflowEvents.NODE_ERROR,
                    node_id=self._node_id,
                    graph_id=self._session.workflow_id(),
                    error=e)
            raise e
        finally:
            self._call_count += 1
            self._is_started.clear()
            self._is_call_started.clear()

    async def _atomic_invoke(self, **kwargs) -> Any:
        return await self.call(kwargs.get("config", None))

    async def _pre_invoke(self) -> Optional[dict]:
        await self.__trace_component_begin__()
        inputs_schema = self._node_config.io_configs.inputs_schema if self._node_config else None
        inputs_transformer = inputs_schema if not isinstance(inputs_schema, dict) else None
        if inputs_transformer is None:
            inputs = self._session.state().get_inputs(inputs_schema) if inputs_schema is not None else None
        else:
            inputs = self._session.state().get_inputs_by_transformer(inputs_transformer)
        await self.__trace_component_inputs__(inputs)
        return inputs

    async def _post_invoke(self, results: Optional[dict]) -> Any:
        outputs_schema = self._node_config.io_configs.outputs_schema if self._node_config else None
        outputs_transformer = outputs_schema if not isinstance(outputs_schema, dict) else None
        if outputs_transformer is None:
            if outputs_schema:
                results = get_by_schema(outputs_schema, results)
                if (not self.is_end_node) and results and isinstance(results, dict):
                    results = {key: value for key, value in results.items() if value is not None}
        else:
            results = outputs_transformer(results)
        id_end_mix_mode = self.is_end_node and self._has_call and self._has_stream_call
        if results and isinstance(results, dict) and id_end_mix_mode:
            # need refactor merge state
            outputs = results.get("output")
            if outputs and not isinstance(outputs, list):
                results["output"] = [outputs]
            old_outputs = self._session.state().get_outputs(self._node_id)
            if isinstance(old_outputs, dict) and isinstance(old_outputs.get("output"), list) and isinstance(
                    results.get("output"), list):
                results["output"].extend(old_outputs.get("output"))

        if results is not None:
            self._session.state().set_outputs(results)
        await self.__trace_component_outputs__(results)
        self._clear_interactive()
        return results

    async def _pre_stream(self, ability: ComponentAbility) -> dict:
        try:
            await self.__trace_component_begin__()
            actor_manager = self._session.actor_manager()
            inputs_schema = self._node_config.stream_io_configs.inputs_schema if self._node_config else None
            if not isinstance(inputs_schema, dict):
                inputs_schema = None
            enable_trace = self._session.tracer() and not self._executable.skip_trace()

            async def stream_callable(chunk):
                logger.debug(f"Consume chunk of {self._node_id}[{ability.name}]",
                             event_type=LogEventType.GRAPH_VERTEX_ABILITY_RUNNING,
                             chunk=chunk,
                             **self._log_message)
                if enable_trace:
                    await TracerWorkflowUtils.trace_component_stream_input(self._session, chunk, send=False)

            return await actor_manager.consume(self._node_id, ability, inputs_schema, stream_callable)
        except Exception as e:
            raise e

    async def _post_stream(self, results_iter: AsyncIterator, ability: ComponentAbility) -> None:
        is_end_node = self.is_end_node
        is_sub_graph = self._session.parent_id() != ''
        actor_manager = self._session.actor_manager()
        output_schema = self._node_config.stream_io_configs.outputs_schema \
            if self._node_config.stream_io_configs else None
        output_transformer = None
        if not isinstance(output_schema, dict):
            output_transformer = output_schema
        end_stream_index = 0
        async for chunk in results_iter:
            if output_transformer is None:
                message = actor_manager.stream_transform.get_by_default_transformer(chunk, output_schema) \
                    if output_schema else chunk
            else:
                message = actor_manager.stream_transform.get_by_defined_transformer(chunk, output_transformer)
            logger.debug(f"Produce chunk[{end_stream_index}] from {self._node_id}[{ability.name}]",
                         event_type=LogEventType.GRAPH_VERTEX_ABILITY_RUNNING,
                         chunk=message,
                         metadata={"is_end_node": is_end_node, "is_sub_graph": is_sub_graph},
                         **self._log_message)
            await self._process_chunk(message, is_end_node, end_stream_index, is_sub_graph, ability)
            end_stream_index += 1
        if is_end_node and is_sub_graph:
            await self._session.actor_manager().sub_workflow_stream().send(StreamEmitter.END_FRAME)
        else:
            await self._session.actor_manager().end_message(self._node_id, ability)
        logger.debug(f"Produce 'END_FRAME' chunk of [{self._node_id}] ability [{ability.name}]",
                     event_type=LogEventType.GRAPH_VERTEX_ABILITY_RUNNING,
                     chunk=StreamEmitter.END_FRAME,
                     metadata={"is_end_node": is_end_node, "is_sub_graph": is_sub_graph},
                     **self._log_message)
        self._clear_interactive()

        # Support get_stream_output for any component (e.g., LLM, SubWorkflowComponent)
        if hasattr(self._executable, "get_stream_output"):
            result = self._executable.get_stream_output()
            if result is not None:
                self._session.state().set_outputs(result)

    async def _process_chunk(self, message,
                             is_end_node: bool,
                             end_stream_index: int,
                             is_sub_graph: bool,
                             ability: ComponentAbility):
        if is_end_node and not is_sub_graph:
            if isinstance(message, StreamSchemas):
                message_stream_data = message
            else:
                message_stream_data = {
                    "type": END_NODE_STREAM,
                    "index": end_stream_index,
                    "payload": message
                }
            await self.__trace_component_stream_output__(message_stream_data)
            if self._session.stream_writer_manager().get_output_writer():
                await self._session.stream_writer_manager().get_output_writer().write(message_stream_data)
        elif is_end_node and is_sub_graph:
            message_stream_data = message.payload if isinstance(message, OutputSchema) else message
            await self.__trace_component_stream_output__(message_stream_data)
            await self._session.actor_manager().sub_workflow_stream().send(message_stream_data)
        else:
            first_frame = end_stream_index == 0
            await self.__trace_component_stream_output__(message)
            await self._session.actor_manager().produce(self._node_id, message, ability, first_frame=first_frame)

    def _clear_interactive(self) -> None:
        if self._session.state().get(INTERACTIVE_INPUT):
            self._session.state().update({INTERACTIVE_INPUT: None})

    async def call(self, config: Any = None):
        # 1. check whether init node or not
        if self._session is None or self._executable is None:
            raise build_error(StatusCode.GRAPH_VERTEX_EXECUTION_ERROR, reason="node is not initialized",
                              node_id=self._node_id)
        # 2. begin to execute node 'batch-in' abilities
        is_subgraph = self._executable.graph_invoker()
        current_ability = None
        try:
            call_ability = [ability for ability in self._component_ability if
                            ability in [ComponentAbility.INVOKE, ComponentAbility.STREAM]]
            for ability in call_ability:
                current_ability = ability
                await self._run_executable(ability, is_subgraph, config)
            if len(call_ability) == 0:
                await self.__trace_component_begin__()

        except ExecutionError as e:
            logger.error(
                "Node ability call failed",
                event_type=LogEventType.GRAPH_VERTEX_CALL_ERROR,
                node_id=self._node_id,
                error_message=str(e),
                metadata={"ability": current_ability.name if current_ability else None}
            )
            raise e

        if self._stream_called():
            # 3. wait for node's 'stream-in' abilities execution finished
            stream_timeout = self._stream_called_timeout if (
                    self._stream_called_timeout and self._stream_called_timeout > 0) else None
            try:
                result = await asyncio.wait_for(
                    self._stream_done,
                    timeout=stream_timeout
                )
                if isinstance(result, Exception):
                    raise result
            except asyncio.TimeoutError:
                raise build_error(StatusCode.GRAPH_VERTEX_STREAM_CALL_TIMEOUT, timeout=stream_timeout,
                                  node_id=self._node_id)
        elif self._has_stream_call and not self.is_end_node:
            # raise error when has stream call but no stream data in
            raise build_error(StatusCode.GRAPH_VERTEX_STREAM_CALL_ERROR, reason="no stream data in",
                              node_id=self._node_id)

        # 4. when the component output is in streaming mode, send an end tracer frame with empty outputs.
        await self.__trace_component_done__()

    def is_done(self) -> bool:
        return (self._call_count == self._stream_call_count
                or self._call_count == self._stream_call_count + 1)

    def _stream_called(self) -> bool:
        return self._stream_call_count == self._call_count + 1

    async def stream_call(self, event: asyncio.Event, error_callback):
        logger.info(f"Begin to call stream-in node [{self._node_id}]",
                    event_type=LogEventType.GRAPH_VERTEX_STREAM_CALL_START,
                    **self._log_message)
        self._stream_call_count += 1
        self._stream_done = asyncio.Future()

        if self._session is None or self._session.actor_manager() is None:
            error = build_error(StatusCode.GRAPH_VERTEX_STREAM_CALL_ERROR,
                                reason="queue manager is not initialized",
                                node_id=self._node_id)
            self._stream_done.set_result(error)
            logger.warning(f"Failed to call stream-in node [{self._node_id}, actor_manager is missing",
                           event_type=LogEventType.GRAPH_VERTEX_STREAM_CALL_ERROR,
                           exception=error,
                           **self._log_message)
            error_callback(error)
            return
        error = None
        tasks = []
        try:
            call_ability = self._stream_abilities()
            for ability in call_ability:
                e = asyncio.Event()
                task = asyncio.create_task(self._run_executable(ability, event=e))
                tasks.append(task)
                await e.wait()
            event.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
            logger.info(f"Succeed to call stream-in node [{self._node_id}]",
                        event_type=LogEventType.GRAPH_VERTEX_STREAM_CALL_END,
                        **self._log_message)
        except asyncio.CancelledError:
            cancelled_tasks = []
            finished_tasks = []
            error_tasks = {}

            for idx, task in enumerate(tasks):
                ability_name = call_ability[idx].name if idx < len(call_ability) else f"task_{idx}"
                if task.done():
                    if task.exception():
                        error_tasks[ability_name] = str(task.exception())
                    else:
                        finished_tasks.append(ability_name)
                else:
                    cancelled_tasks.append(ability_name)

            pending_tasks = []
            for task in tasks:
                if not task.done() and not task.cancelled():
                    task.cancel()
                    pending_tasks.append(task)

            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            logger.warning(
                f"Cancel to call stream-in node [{self._node_id}]",
                event_type=LogEventType.GRAPH_VERTEX_STREAM_CALL_ERROR,
                metadata={
                    "cancelled": cancelled_tasks,
                    "finished": finished_tasks,
                    "error": error_tasks
                },
                **self._log_message
            )
        except Exception as e:
            logger.error(
                f"Failed to call stream-in node [{self._node_id}]",
                event_type=LogEventType.GRAPH_VERTEX_STREAM_CALL_ERROR,
                exception=e,
                **self._log_message
            )
            error_callback(e)
            error = e
        finally:
            self._stream_done.set_result(error if error else True)
            await self.__trace_component_stream_input_send__()

    def _stream_abilities(self) -> list[Literal[ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]]:
        call_ability = [ability for ability in self._component_ability if
                        ability in [ComponentAbility.COLLECT, ComponentAbility.TRANSFORM]]
        return call_ability

    def should_handle_message(self) -> bool:
        call_ability = self._stream_abilities()
        return len(call_ability) > 0

    async def __trace_component_inputs__(self, inputs: Optional[dict]) -> None:
        if (not self._session.tracer()) or self._executable.skip_trace():
            return
        self._is_call_started.set()
        need_send = (not self._has_stream_call) or self._stream_done.done()
        await TracerWorkflowUtils.trace_component_inputs(self._session, inputs, send=need_send)
        if self._executable.component_type() == SUB_WORKFLOW_COMPONENT:
            self._session.tracer().register_workflow_span_manager(self._session.executable_id())

    async def __trace_component_outputs__(self, outputs: Optional[dict] = None) -> None:
        if (not self._session.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_component_outputs(self._session, outputs)

    async def __trace_component_begin__(self) -> None:
        if (not self._session.tracer()) or self._executable.skip_trace():
            return
        if not self._is_started.is_set():
            self._is_started.set()
            await TracerWorkflowUtils.trace_component_begin(self._session)

    async def __trace_component_done__(self) -> None:
        if (not self._session.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_component_done(self._session)

    async def __trace_component_stream_output__(self, chunk) -> None:
        if (not self._session.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_component_stream_output(self._session, chunk)

    async def __trace_error__(self, error: Exception) -> None:
        if (not self._session.tracer()) or self._executable.skip_trace():
            return
        await TracerWorkflowUtils.trace_error(self._session, error)

    async def __trace_component_stream_input_send__(self) -> None:
        if (not self._session.tracer()) or self._executable.skip_trace():
            return
        if (not self._has_call) or self._is_call_started.is_set():
            await TracerWorkflowUtils.trace_component_stream_input(self._session, {}, send=True)

    async def reset(self):
        self._call_count = 0
        self._stream_call_count = 0
        self._stream_done.cancel()
        try:
            await self._stream_done
        except CancelledError:
            pass
        self._stream_done = asyncio.Future()
