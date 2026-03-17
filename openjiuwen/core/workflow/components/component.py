# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC
from typing import AsyncIterator, TypeVar

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Executable
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.internal.workflow import NodeSession
from openjiuwen.core.session.node import Session

Input = TypeVar("Input", contravariant=True)
Output = TypeVar("Output", contravariant=True)


class ComponentExecutable(Executable):
    """
    Interface for executable components in a workflow.

    Defines the four fundamental execution patterns for components:
    1. Invoke - Synchronous batch processing
    2. Stream - Streaming output from batch input
    3. Collect - Batch output from streaming input
    4. Transform - Streaming to streaming transformation

    This interface allows components to support different I/O patterns
    based on their capabilities and use cases.
    """

    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        if not isinstance(session, NodeSession):
            raise build_error(StatusCode.WORKFLOW_INNER_ORCHESTRATION_ERROR,
                              reason="session type must be NodeSession when on_invoke")
        return await self.invoke(inputs, Session(session, False), kwargs.get("context"))

    async def on_stream(self, inputs: Input, session: BaseSession, **kwargs) -> AsyncIterator[Output]:
        if not isinstance(session, NodeSession):
            raise build_error(StatusCode.WORKFLOW_INNER_ORCHESTRATION_ERROR,
                              reason="session type must be NodeSession when on_stream")

        async for value in self.stream(inputs, Session(session, False), kwargs.get("context")):
            yield value

    async def on_collect(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        if not isinstance(session, NodeSession):
            raise build_error(StatusCode.WORKFLOW_INNER_ORCHESTRATION_ERROR,
                              reason="session type must be NodeSession when on_collect")

        return await self.collect(inputs, Session(session, True), kwargs.get("context"))

    async def on_transform(self, inputs: Input, session: BaseSession, **kwargs) -> AsyncIterator[Output]:
        if not isinstance(session, NodeSession):
            raise build_error(StatusCode.WORKFLOW_INNER_ORCHESTRATION_ERROR,
                              reason="session is not NodeSession when on_transform")

        async for value in self.transform(inputs, Session(session, True), kwargs.get("context")):
            yield value

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        Execute component synchronously with batch input and output.

        Args:
            inputs: Batch input data
            session: Current execution session
            context: Context engine

        Returns:
            Output: Batch output data

        Note:
            This is the most common execution pattern for simple operations.
        """
        class_name = type(self).__name__
        method_name = "stream"

        raise NotImplementedError(
            f"Component '{class_name}' is missing required method: {method_name}()\n"
            f"  → Expected signature: async def {method_name}(self, inputs: Input, session: Session, "
            f"context: ModelContext) -> AsyncIterator[Output]"
        )

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """
        Execute component with batch input but streaming output.

        Args:
            inputs: Batch input data
            session: Current execution session
            context:  Context engine

        Returns:
            AsyncIterator[Output]: Async iterator that yields output chunks

        Note:
            Useful for long-running operations or real-time data generation.
        """
        class_name = type(self).__name__
        method_name = "stream"

        raise NotImplementedError(
            f"Component '{class_name}' is missing required method: {method_name}()\n"
            f"  → Expected signature: async def {method_name}(self, inputs: Input, session: Session, "
            f"context: ModelContext) -> AsyncIterator[Output]"
        )

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        Execute component with streaming input but batch output.

        Args:
            inputs: Streaming input data (async iterator)
            session: Current execution session
            context: Context engine

        Returns:
            Output: Batch output collected from all input chunks

        Note:
            Useful for aggregating streaming data into a single result.
        """
        class_name = type(self).__name__
        method_name = "collect"

        raise NotImplementedError(
            f"Component '{class_name}' is missing required method: {method_name}()\n"
            f"  → Expected signature: async def {method_name}(self, inputs: Input, session: Session, "
            f"context: ModelContext) -> Output"
        )

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """ Execute component with streaming input and streaming output.

         Args:
             inputs: Streaming input data (async iterator)
             session: Current execution session
             context: Context engine

         Returns:
             AsyncIterator[Output]: Async iterator that yields transformed output chunks

         Note:
             This is the most general pattern for real-time data processing pipelines.
         """
        class_name = type(self).__name__
        method_name = "transform"

        raise NotImplementedError(
            f"Component '{class_name}' is missing required method: {method_name}()\n"
            f"  → Expected signature: async def {method_name}(self, inputs: Input, session: Session, "
            f"context: ModelContext) -> AsyncIterator[Output]"
        )


class ComponentComposable(ABC):
    """
    Abstract base class for workflow graph construction.

    This class is responsible for defining how a component integrates
    into a workflow graph. It separates the construction logic from
    the execution logic defined in ComponentExecutable.

    Components implementing this interface can describe their
    graph structure independently of their runtime behavior.
    """

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        """
        Add this component to a workflow graph.

        Args:
            graph: The workflow graph to add this component to
            node_id: Unique identifier for this component node
            wait_for_all: If True, wait for all predecessor outputs before execution

        Note:
            This method defines the component's position and connections
            within the larger workflow graph.
        """
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)

    def to_executable(self) -> Executable:
        """
        Convert this workflow component to an executable instance.

        Returns:
            ComponentExecutable: An executable instance of this component

        Note:
            This method creates the runtime representation of the component
            that can actually execute the business logic.
        """
        if isinstance(self, Executable):
            return self
        class_name = type(self).__name__
        method_name = "to_executable"

        raise NotImplementedError(
            f"Component '{class_name}' is missing required method: {method_name}()\n"
            f"  → Expected signature: def {method_name}(self) -> Executable"
        )


class WorkflowComponent(ComponentExecutable, ComponentComposable):
    """
    Standard implementation combining both execution and graph construction.

    This class provides a complete component implementation that:
    1. Can be executed (implements ComponentExecutable)
    2. Can be added to workflow graphs (implements ComponentComposable)

    This is the most common base class for user-defined components
    that need both construction-time and runtime capabilities.

    Example:
        class MyComponent(WorkflowComponent):
            async def invoke(self, inputs, runtime, context):
                # Implementation here
                pass

            def add_component(self, graph, node_id, wait_for_all=False):
                # Add to graph logic here
                pass
    """
    ...
