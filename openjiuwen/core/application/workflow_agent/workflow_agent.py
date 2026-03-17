# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any, AsyncIterator

from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.single_agent.legacy import ControllerAgent, WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent.workflow_controller import WorkflowController
from openjiuwen.core.single_agent import Session


class WorkflowAgent(ControllerAgent):
    """Workflow-based Agent - Executes predefined workflows with multi-workflow controller
    
    Implemented using ControllerAgent
    """

    def __init__(self, agent_config: WorkflowAgentConfig):
        # Validate controller_type
        if agent_config.controller_type != ControllerType.WorkflowController:
            raise NotImplementedError(
                f"WorkflowAgent requires WorkflowController, "
                f"got {agent_config.controller_type}"
            )

        # Create controller without parameters - will be auto-configured by ControllerAgent
        controller = WorkflowController()
        
        # Pass to parent - parent will auto-configure it
        super().__init__(agent_config, controller=controller)

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        """Synchronous invocation - Delegate to controller
        
        Args:
            inputs: Input data, including query and conversation_id
            session: Session context (optional)
            
        Returns:
            Execution result
        """
        # Fully delegate to ControllerAgent implementation
        return await super().invoke(inputs, session)

    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        """Streaming invocation - Delegate to controller
        
        Args:
            inputs: Input data, including query and conversation_id
            session: Session context (optional)
            
        Yields:
            Streaming output
        """
        # Fully delegate to ControllerAgent implementation
        async for result in super().stream(inputs, session):
            yield result
