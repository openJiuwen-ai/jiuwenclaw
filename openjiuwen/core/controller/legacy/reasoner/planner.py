# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

from typing import List
from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.legacy.event.event import Event
from openjiuwen.core.controller.legacy.task.task import Task, TaskStatus
from openjiuwen.core.common.constants.enums import TaskType


class Planner:
    """Planner - Plans and decomposes complex tasks"""

    def __init__(self, config, context_engine, session):
        """
        Initialize Planner
        
        Args:
            config: Planner config
            context_engine: Context engine
            session: Session environment
        """
        self.config = config
        self.context_engine = context_engine
        self.session = session

    @staticmethod
    def _create_default_task(event: Event) -> Task:
        # Temporary: return default task
        return Task(
            task_type=TaskType.UNDEFINED,
            description=f"Planner task for message: {event.content.get_query() if event.content else 'No content'}",
            status=TaskStatus.PENDING,
            metadata={
                "original_message_id": event.event_id,
                "task_source": "planner"
            }
        )

    async def process_message(self, event: Event) -> List[Task]:
        """
        Process message, plan tasks and generate task list
        
        Args:
            message: Input message
            
        Returns:
            List[Task]: Generated task list
        """
        # Implement task planning logic:
        # 1. Analyze message, identify complex tasks
        # 2. Create execution plan, decompose into subtasks
        # 3. Generate task list, set task dependencies
        # 4. Return task list

        logger.debug(f"Processing message {event.event_id} with Planner")
        return [self._create_default_task(event)]
