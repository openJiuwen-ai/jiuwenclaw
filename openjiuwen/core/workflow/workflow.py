# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import uuid
from abc import ABCMeta
from typing import (
    AsyncIterator,
    Self,
)

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import (
    BaseError,
    build_error,
)
from openjiuwen.core.common.logging import (
    LogEventType,
    workflow_logger as logger,
)
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import (
    CONFIG_KEY,
    INPUTS_KEY,
    Router,
)
from openjiuwen.core.graph.executable import (
    Executable,
    Input,
    Output,
)
from openjiuwen.core.graph.graph import PregelGraph
from openjiuwen.core.graph.stream_actor.manager import ActorManager
from openjiuwen.core.session import (
    InteractiveInput,
    NodeSession,
    SubWorkflowSession,
    Transformer,
    WORKFLOW_EXECUTE_TIMEOUT,
    WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT,
    WORKFLOW_STREAM_FRAME_TIMEOUT,
    WorkflowSession,
)
from openjiuwen.core.session.stream import (
    BaseStreamMode,
    OutputSchema,
    StreamEmitter,
    StreamMode,
    StreamWriterManager,
)
from openjiuwen.core.session.tracer import (
    Tracer,
    TracerWorkflowUtils,
)
from openjiuwen.core.session.workflow import Session
from openjiuwen.core.workflow._workflow import BaseWorkflow
from openjiuwen.core.workflow.base import (
    WorkflowCard,
    WorkflowChunk,
    WorkflowExecutionState,
    WorkflowOutput,
)
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.workflow.components.component import ComponentComposable
from openjiuwen.core.workflow.workflow_config import WorkflowConfig
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import WorkflowEvents


class _WorkflowMeta(ABCMeta):
    def __call__(cls, *args, **kwargs):
        instance = super().__call__(*args, **kwargs)
        from openjiuwen.core.runner import Runner
        _fw = Runner.callback_framework
        fn = instance.invoke
        fn = _fw.emit_before(WorkflowEvents.WORKFLOW_INVOKE_INPUT)(fn)
        fn = _fw.transform_io(
            input_event=WorkflowEvents.WORKFLOW_INVOKE_INPUT,
            output_event=WorkflowEvents.WORKFLOW_INVOKE_OUTPUT,
        )(fn)
        fn = _fw.emit_after(WorkflowEvents.WORKFLOW_INVOKE_OUTPUT)(fn)
        instance.invoke = fn

        fn = instance.stream
        fn = _fw.emit_before(WorkflowEvents.WORKFLOW_STREAM_INPUT)(fn)
        fn = _fw.transform_io(
            input_event=WorkflowEvents.WORKFLOW_STREAM_INPUT,
            output_event=WorkflowEvents.WORKFLOW_STREAM_OUTPUT,
        )(fn)
        fn = _fw.emit_after(WorkflowEvents.WORKFLOW_STREAM_OUTPUT, item_key="result")(fn)
        instance.stream = fn
        return instance


class Workflow(metaclass=_WorkflowMeta):
    """
    A workflow represents a directed graph of components that process data.

    The workflow orchestrates the execution of connected components, managing
    data flow, error handling, and streaming between components.
    """

    def __init__(self, card: WorkflowCard = None, **kwargs):
        """
        Initialize a new workflow.

        Args:
            card: Metadata describing the workflow (name, description, etc.)
            kwargs: workflow configs
        """
        self._card = card if card else WorkflowCard(id=uuid.uuid4().hex)
        self._internal = BaseWorkflow(WorkflowConfig(card=self._card, **kwargs), PregelGraph())
        self._end_comp_id: str = ""
        self._is_streaming = False

    @property
    def card(self):
        """Get the workflow metadata card."""
        return self._card

    def set_start_comp(
            self,
            start_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
    ) -> Self:
        """
        Set the starting component of the workflow.

        The start component is the entry point that receives initial inputs.

        Args:
            start_comp_id: Unique identifier for the start component
            component: The component instance to use as start
            inputs_schema: Schema defining expected input structure
            outputs_schema: Schema defining output structure

        Returns:
            Self for method chaining
        """
        self._internal.add_workflow_comp(start_comp_id,
                                         component,
                                         wait_for_all=False,
                                         inputs_schema=inputs_schema,
                                         outputs_schema=outputs_schema)
        self._internal.start_comp(start_comp_id)
        return self

    def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: ComponentComposable | Executable,
            *,
            wait_for_all: bool = None,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self:
        """
        Add a component to the workflow graph.

        Args:
            comp_id: Unique identifier for the component
            workflow_comp: The component instance to add
            wait_for_all: If True, wait for all predecessor outputs before executing
            inputs_schema: Schema defining expected input structure
            outputs_schema: Schema defining output structure
            stream_inputs_schema: Schema for streaming inputs
            stream_outputs_schema: Schema for streaming outputs
            comp_ability: List of component capabilities (streaming, batching, etc.)

        Returns:
            Self for method chaining
        """
        self._internal.add_workflow_comp(comp_id,
                                         workflow_comp,
                                         wait_for_all=wait_for_all,
                                         inputs_schema=inputs_schema,
                                         outputs_schema=outputs_schema,
                                         stream_inputs_schema=stream_inputs_schema,
                                         stream_outputs_schema=stream_outputs_schema,
                                         comp_ability=comp_ability)
        return self

    def set_end_comp(
            self,
            end_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            response_mode: str = None
    ) -> Self:
        """
        Set the ending component of the workflow.

        The end component produces the final output of the workflow.

        Args:
            end_comp_id: Unique identifier for the end component
            component: The component instance to use as end
            inputs_schema: Schema defining expected input structure
            outputs_schema: Schema defining output structure
            stream_inputs_schema: Schema for streaming inputs
            stream_outputs_schema: Schema for streaming outputs
            response_mode: How the component should respond (e.g., "stream", "batch")

        Returns:
            Self for method chaining
        """
        comp_ability = []
        if response_mode is not None and "streaming" == response_mode:
            self._is_streaming = True
            if inputs_schema is not None:
                comp_ability.append(ComponentAbility.STREAM)
            if stream_inputs_schema is not None:
                comp_ability.append(ComponentAbility.TRANSFORM)
            if not comp_ability:
                comp_ability = [ComponentAbility.STREAM]
        else:
            comp_ability = [ComponentAbility.INVOKE]
            if stream_inputs_schema is not None:
                comp_ability.append(ComponentAbility.COLLECT)
        wait_for_all = True if ((ComponentAbility.COLLECT in comp_ability)
                                or (ComponentAbility.TRANSFORM in comp_ability)) else False
        self._internal.add_workflow_comp(
            end_comp_id,
            component,
            wait_for_all=wait_for_all,
            comp_ability=comp_ability,
            inputs_schema=inputs_schema,
            outputs_schema=outputs_schema,
            stream_inputs_schema=stream_inputs_schema,
            stream_outputs_schema=stream_outputs_schema,
        )
        self._internal.end_comp(end_comp_id)
        self._end_comp_id = end_comp_id
        return self

    def add_connection(self, src_comp_id: str | list[str], target_comp_id: str) -> Self:
        """
        Add a data connection between components.

        Creates a directed edge for regular (non-streaming) data flow.

        Args:
            src_comp_id: Source component ID or set of IDs
            target_comp_id: Target component ID

        Returns:
            Self for method chaining
        """
        self._internal.add_connection(src_comp_id, target_comp_id)
        return self

    def add_stream_connection(self, src_comp_id: str, target_comp_id: str) -> Self:
        """
        Add a streaming connection between components.

        Creates a directed edge for streaming data flow.

        Args:
            src_comp_id: Source component ID
            target_comp_id: Target component ID

        Returns:
            Self for method chaining
        """
        self._internal.add_stream_connection(src_comp_id, target_comp_id)
        return self

    def add_conditional_connection(self, src_comp_id: str, router: Router) -> Self:
        """
        Add a conditional connection with routing logic.

        Creates a connection where the target is determined dynamically
        based on the router's logic.

        Args:
            src_comp_id: Source component ID
            router: Router instance that decides the target based on data

        Returns:
            Self for method chaining
        """
        self._internal.add_conditional_connection(src_comp_id, router)
        return self

    async def invoke(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            **kwargs
    ) -> WorkflowOutput:
        """
        Execute the workflow synchronously.

        Runs the entire workflow and returns the final output.

        Args:
            inputs: Input data for the workflow
            session: Workflow session for state management
            context: context engine
            **kwargs: Additional execution parameters
                - is_sub: Whether this is a sub-workflow execution
                - skip_inputs_validate: Whether to skip input validation

        Returns:
            WorkflowOutput containing results and metadata
        """
        if kwargs.get("is_sub"):
            return await self._sub_invoke(inputs, session, context, **kwargs)
        self._validate_session(session)
        self._validate_inputs(inputs, **kwargs)
        self._install_asyncio_exception_handler()
        logger.info(
            "Begin to run workflow invoke",
            event_type=LogEventType.WORKFLOW_EXECUTE_START,
            workflow_id=self._card.id,
            workflow_name=self._card.name,
            inputs=inputs,
        )
        workflow_session = self._create_workflow_session(session, stream_modes=[BaseStreamMode.OUTPUT], is_sub=False)

        async def _invoke_task():
            chunks = []
            async for chunk in self._stream(inputs, workflow_session, context=context):
                chunks.append(chunk)

            is_interaction = False
            for chunk in chunks:
                if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                    is_interaction = True
                    break
            if is_interaction:
                output = WorkflowOutput(result=[chunk for chunk in chunks],
                                        state=WorkflowExecutionState.INPUT_REQUIRED)
            else:
                if self._is_streaming:
                    result = chunks
                else:
                    result = workflow_session.state().get_outputs(self._end_comp_id)
                output = WorkflowOutput(result=result, state=WorkflowExecutionState.COMPLETED)

            return output

        invoke_timeout = workflow_session.config().get_env(WORKFLOW_EXECUTE_TIMEOUT)
        try:
            result = await self._execute_with_timeout(_invoke_task, invoke_timeout)
            logger.info(
                "Succeed to run workflow invoke",
                event_type=LogEventType.WORKFLOW_EXECUTE_END,
                workflow_id=self._card.id,
                workflow_name=self._card.name,
                session_id=workflow_session.session_id(),
                outputs=result
            )
            return result
        except Exception as e:
            logger.error(
                "Failed to run workflow invoke",
                event_type=LogEventType.WORKFLOW_EXECUTE_ERROR,
                workflow_id=self._card.id,
                workflow_name=self._card.name,
                exception=e,
                session_id=workflow_session.session_id(),
            )
            raise e

    async def stream(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            stream_modes: list[StreamMode] = None,
            **kwargs
    ) -> AsyncIterator[WorkflowChunk]:
        """
        Execute the workflow with streaming output.

        Returns an async iterator that yields workflow chunks as they become available.

        Args:
            inputs: Input data for the workflow
            session: Workflow session for state management
            stream_modes: Type(s) of WorkflowChunk
            context: context engine
            **kwargs: Additional execution parameters
                - is_sub: Whether this is a sub-workflow execution
                - skip_inputs_validate: Whether to skip input validation

        Yields:
            WorkflowChunk: Stream chunks containing partial results, logs, or events
        """
        if kwargs.get("is_sub"):
            async for chunk in self._sub_stream(inputs, session, context, **kwargs):
                yield chunk
            return

        self._validate_session(session)
        self._validate_inputs(inputs, **kwargs)
        self._install_asyncio_exception_handler()
        logger.info(
            "Begin to run workflow stream",
            event_type=LogEventType.WORKFLOW_EXECUTE_START,
            workflow_id=self._card.id,
            workflow_name=self._card.name,
            inputs=inputs,
        )
        workflow_session = self._create_workflow_session(session, stream_modes=stream_modes, is_sub=False)
        idx = 0
        async for chunk in self._stream(inputs, workflow_session, context):
            logger.debug(
                f"Output workflow chunk[{idx}]",
                event_type=LogEventType.WORKFLOW_OUTPUT_CHUNK,
                workflow_id=self._card.id,
                workflow_name=self._card.name,
                chunk=chunk,
                chunk_idx=idx,
            )
            yield chunk
            idx += 1
        logger.info(
            "Succeed to run workflow stream",
            event_type=LogEventType.WORKFLOW_EXECUTE_END,
            workflow_id=self._card.id,
            workflow_name=self._card.name,
            metadata={"total_chunks": idx}
        )

    def draw(
            self,
            title: str = "",
            output_format: str = "mermaid",  # "mermaid", "png", "svg"
            expand_subgraph: int | bool = False,
            enable_animation: bool = False,  # only works for "mermaid" format
            **kwargs
    ) -> str | bytes:
        """
        Generate a Mermaid diagram of the workflow.

        Visualizes the workflow structure as a flowchart.

        Args:
            title: Diagram title
            output_format: Output format ("mermaid", "png", or "svg")
            expand_subgraph: Level of subgraph expansion (False/True or integer depth)
            enable_animation: Enable animation in Mermaid diagram (Mermaid format only)
            **kwargs: Additional rendering options

        Returns:
            str: Mermaid syntax when output_format="mermaid"
            bytes: Image binary data when output_format="png" or "svg"
        """
        if output_format == "png":
            return self._internal.to_mermaid_png(title, expand_subgraph)
        if output_format == "svg":
            return self._internal.to_mermaid_svg(title, expand_subgraph)
        return self._internal.to_mermaid(title, expand_subgraph, enable_animation)

    async def _stream(self, inputs: Input,
                      session: WorkflowSession,
                      context: ModelContext = None,
                      ) -> AsyncIterator[WorkflowChunk]:

        # workflow start tracer info
        await TracerWorkflowUtils.trace_workflow_start(session, inputs)
        await trigger(
            WorkflowEvents.WORKFLOW_STARTED,
            workflow_id=self._card.id,
            workflow_name=self._card.name,
            inputs=inputs)
        # calculate timeout and frame_timeout
        timeout = session.config().get_env(WORKFLOW_EXECUTE_TIMEOUT)
        frame_timeout = session.config().get_env(WORKFLOW_STREAM_FRAME_TIMEOUT)
        if timeout is not None and 0 < timeout <= frame_timeout:
            frame_timeout = timeout
        session.config().set_envs({WORKFLOW_STREAM_FRAME_TIMEOUT: frame_timeout})
        first_frame_timeout = session.config().get_env(WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT)
        if timeout is not None and 0 < timeout <= first_frame_timeout:
            first_frame_timeout = timeout
        session.config().set_envs({WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT: first_frame_timeout})

        async def stream_process():
            compiled_graph = self._internal.compile(session, context)
            try:
                await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: None}, session)
            finally:
                # workflow end tracer info
                outputs = session.state().get_outputs(self._end_comp_id)
                await asyncio.shield(TracerWorkflowUtils.trace_workflow_done(session, outputs))
                await asyncio.shield(session.stream_writer_manager().stream_emitter().close())

        task = asyncio.create_task(self._execute_with_timeout(stream_process, timeout))

        try:
            interaction_chuck_list = []
            chunks = []
            async for chunk in session.stream_writer_manager().stream_output(first_frame_timeout=first_frame_timeout,
                                                                             timeout=frame_timeout,
                                                                             need_close=True):
                yield chunk
                if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                    interaction_chuck_list.append(chunk)
                chunks.append(chunk)
            try:
                await task
                results = session.state().get_outputs(self._end_comp_id)
                await trigger(
                    WorkflowEvents.WORKFLOW_FINISHED,
                    workflow_id=self._card.id,
                    workflow_name=self._card.name,
                    outputs=results)
                if results:
                    yield OutputSchema(type="workflow_final", index=0, payload=results)
            except asyncio.CancelledError:
                logger.warning(
                    "Workflow stream output be cancelled",
                    event_type=LogEventType.WORKFLOW_EXECUTE_ERROR,
                    workflow_id=self._card.id,
                    workflow_name=self._card.name
                )
                raise
        except asyncio.CancelledError:
            await trigger(
                WorkflowEvents.WORKFLOW_CANCELLED,
                workflow_id=self._card.id,
                workflow_name=self._card.name)
            logger.warning(
                "Canecel stream output task",
                event_type=LogEventType.WORKFLOW_EXECUTE_ERROR,
                workflow_id=self._card.id,
                workflow_name=self._card.name
            )
            if not task.done() and not task.cancelled():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            raise
        except BaseError as e:
            await trigger(
                WorkflowEvents.WORKFLOW_ERROR,
                workflow_id=self._card.id,
                workflow_name=self._card.name,
                error=e)
            raise
        finally:
            if not task.done() and task.cancelled():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.warning(
                        "workflow task was cancelled",
                        event_type=LogEventType.WORKFLOW_EXECUTE_ERROR,
                        workflow_id=self._card.id,
                        workflow_name=self._card.name
                    )
                except Exception as e:
                    logger.warning(
                        "unexpected exception",
                        event_type=LogEventType.WORKFLOW_EXECUTE_ERROR,
                        workflow_id=self._card.id,
                        workflow_name=self._card.name
                    )
            await asyncio.shield(session.close())
            await asyncio.shield(self._internal.reset())

    async def _sub_invoke(self, inputs: Input, session: Session,
                          context: ModelContext = None, **kwargs) -> Output:
        sub_workflow_session = self._create_workflow_session(session, is_sub=True)

        try:
            compiled_graph = self._internal.compile(sub_workflow_session, context)
            await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: kwargs.get(CONFIG_KEY)},
                                        sub_workflow_session)
            if self._is_streaming:
                messages = []
                sub_end_ability = self._internal.config().spec.comp_configs.get(self._end_comp_id).abilities
                required_abilities = [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]
                stream_ability_count = sum(ability in sub_end_ability for ability in required_abilities)
                while stream_ability_count > 0:
                    frame = await sub_workflow_session.actor_manager().sub_workflow_stream().receive(
                        session.get_env(WORKFLOW_EXECUTE_TIMEOUT))
                    if frame is None:
                        continue
                    if frame == StreamEmitter.END_FRAME:
                        stream_ability_count -= 1
                        continue
                    messages.append(frame)
                if messages:
                    return dict(stream=messages)

            node_session = NodeSession(sub_workflow_session, self._end_comp_id)
            output_key = self._end_comp_id

            results = node_session.state().get_outputs(output_key)
            return results
        finally:
            await asyncio.shield(sub_workflow_session.close())
            await asyncio.shield(self._internal.reset())

    async def _sub_stream(self, inputs: Input, session: Session, context: ModelContext = None, **kwargs) -> \
            AsyncIterator[Output]:
        sub_workflow_session = self._create_workflow_session(session, is_sub=True)
        try:
            compiled_graph = self._internal.compile(sub_workflow_session, context=context)
            await compiled_graph.invoke({INPUTS_KEY: inputs, CONFIG_KEY: kwargs.get(CONFIG_KEY)}, sub_workflow_session)
            if self._is_streaming:
                frame_count = 0
                stream_timeout = session.get_env(WORKFLOW_EXECUTE_TIMEOUT)
                sub_end_ability = self._internal.config().spec.comp_configs.get(self._end_comp_id).abilities
            required_abilities = [ComponentAbility.STREAM, ComponentAbility.TRANSFORM]
            stream_ability_count = sum(ability in sub_end_ability for ability in required_abilities)
            while stream_ability_count > 0:
                frame = await sub_workflow_session.actor_manager().sub_workflow_stream().receive(stream_timeout)
                if frame is None:
                    continue
                if frame == StreamEmitter.END_FRAME:
                    stream_ability_count -= 1
                    continue
                frame_count += 1
                yield frame
        finally:
            await asyncio.shield(sub_workflow_session.close())
            await asyncio.shield(self._internal.reset())

    async def _execute_with_timeout(self, func, timeout):
        task = asyncio.create_task(func())
        try:
            return await asyncio.wait_for(task, timeout=timeout if (timeout and timeout > 0) else None)
        except asyncio.CancelledError:
            logger.error(
                "Workflow execution cancelled",
                event_type=LogEventType.WORKFLOW_EXECUTE_ERROR,
                workflow_id=self._card.id,
                workflow_name=self._card.name
            )
            raise
        except asyncio.TimeoutError as e:
            logger.error(
                f"Workflow execution timeout {timeout} s",
                event_type=LogEventType.WORKFLOW_EXECUTE_ERROR,
                workflow_id=self._card.id,
                workflow_name=self._card.name,
                exception=e
            )
            raise build_error(StatusCode.WORKFLOW_EXECUTION_TIMEOUT, cause=e, timeout=timeout, card=self._card)
        except BaseError as e:
            raise e
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass

    def _create_workflow_session(self, session, stream_modes=None, is_sub: bool = False):
        if not is_sub:
            session.set_workflow_card(self._card)
            parent = session.get_parent()
            workflow_session = WorkflowSession(workflow_id=self._card.id,
                                               parent=parent if parent is not None else None,
                                               session_id=session.get_session_id())
            workflow_session.config().set_envs(session.get_envs())
            self._internal.auto_complete_abilities()
            workflow_session.config().add_workflow_config(workflow_id=self._card.id,
                                                          workflow_config=self._internal.config())
            mq_manager = ActorManager(self._internal.config().spec, self._internal.stream_actor(), sub_graph=False,
                                      session=workflow_session)
            workflow_session.set_actor_manager(mq_manager)
            workflow_session.set_stream_writer_manager(
                StreamWriterManager(stream_emitter=StreamEmitter(), modes=stream_modes))
            if workflow_session.tracer() is None and (stream_modes is None or BaseStreamMode.TRACE in stream_modes):
                tracer = Tracer()
                tracer.init(workflow_session.stream_writer_manager())
                workflow_session.set_tracer(tracer)
            return workflow_session
        else:
            inner_session = getattr(session, "_inner")
            self._internal.auto_complete_abilities()
            actor_manager = ActorManager(self._internal.config().spec, self._internal.stream_actor(), sub_graph=True,
                                         session=inner_session)
            sub_workflow_session = SubWorkflowSession(
                inner_session,
                workflow_id=self._card.id,
                actor_manager=actor_manager
            )
            sub_workflow_session.config().add_workflow_config(workflow_id=self._card.id,
                                                              workflow_config=self._internal.config())
            return sub_workflow_session

    def _validate_inputs(self, inputs, **kwargs):
        if self._card.input_params is not None and not isinstance(inputs, InteractiveInput):
            try:
                inputs = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                        skip_validate=kwargs.get("skip_inputs_validate"))
            except Exception as e:
                raise build_error(StatusCode.WORKFLOW_EXECUTE_INPUT_INVALID, cause=e, inputs=inputs,
                                  reason=f"input validation failed against schema: {str(e) if e else 'Unknown error'}",
                                  workflow=self._card.str())

    def _validate_session(self, session):
        if not session:
            raise build_error(StatusCode.WORKFLOW_EXECUTE_SESSION_INVALID,
                              reason="session is required for workflow execution",
                              workflow=self._card.str())


    @staticmethod
    def _install_asyncio_exception_handler():
        """Install a global exception handler for asyncio tasks to handle unhandled exception."""

        def loop_exception_handler(_, context):
            """Handle unhandled exceptions in asyncio tasks."""
            exception = context.get("exception")
            if exception:
                import traceback
                traceback_info = ''.join(traceback.format_exception(type(exception), exception,
                                                                    exception.__traceback__))
                logger.error(
                    "Unhandled exception in asyncio",
                    event_type=LogEventType.SYSTEM_ERROR,
                    error_message=str(exception),
                    metadata={"traceback": traceback_info}
                )

        loop = asyncio.get_event_loop()
        loop.set_exception_handler(loop_exception_handler)
