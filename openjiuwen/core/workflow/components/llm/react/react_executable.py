# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import AsyncIterator, Dict, Any
from openjiuwen.core.workflow.components.component import ComponentExecutable
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from .react_config import ReActAgentCompConfig


class ReActAgentCompExecutable(ComponentExecutable):
    def __init__(self, config: ReActAgentCompConfig):
        super().__init__()
        self.config = config
        # Lazy import to avoid circular dependency:
        # react_agent.py → base.py → ability_manager.py → workflow/__init__.py → react_executable.py
        from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
        # Create a ReActAgent instance with a workflow-specific card
        self._react_agent = ReActAgent(
            card=AgentCard(
                id="react_agent_workflow_executable",
                name="ReAct Agent Workflow Executable",
                description="ReAct agent for workflow execution"
            )
        )
        self._react_agent.configure(config)

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Execute ReAct loop synchronously with batch input/output."""
        try:
            # Execute the ReAct agent with the provided inputs
            result = await self._react_agent.invoke(inputs, session)
            return result
        except Exception as e:
            # Handle errors appropriately
            return {
                "output": f"Error in ReAct execution: {str(e)}",
                "result_type": "error"
            }

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """Execute ReAct loop with streaming output."""
        try:
            # Stream the ReAct agent execution
            async for chunk in self._react_agent.stream(inputs, session):
                yield chunk
        except Exception as e:
            # Handle errors appropriately
            yield {
                "type": "error",
                "payload": {"output": f"Error in ReAct streaming: {str(e)}", "result_type": "error"}
            }

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Execute ReAct loop with streaming input aggregated to batch output."""
        # For now, just delegate to invoke since ReAct expects batch input
        # In the future, we could aggregate streaming inputs
        try:
            # If inputs is an async iterator, collect it first
            if hasattr(inputs, '__aiter__'):
                collected_inputs = []
                async for input_chunk in inputs:
                    collected_inputs.append(input_chunk)
                
                # Combine collected inputs into a single input
                if len(collected_inputs) == 1:
                    final_inputs = collected_inputs[0]
                else:
                    # Combine multiple inputs - this depends on the input format
                    # For now, just use the last input
                    final_inputs = collected_inputs[-1]
            else:
                final_inputs = inputs
                
            result = await self._react_agent.invoke(final_inputs, session)
            return result
        except Exception as e:
            return {
                "output": f"Error in ReAct collect: {str(e)}",
                "result_type": "error"
            }

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """Execute ReAct loop with streaming input/output."""
        try:
            # Process each input chunk through the ReAct agent
            async for input_chunk in inputs:
                # Execute ReAct on this chunk
                result = await self._react_agent.invoke(input_chunk, session)
                yield result
        except Exception as e:
            yield {
                "type": "error",
                "payload": {"output": f"Error in ReAct transform: {str(e)}", "result_type": "error"}
            }