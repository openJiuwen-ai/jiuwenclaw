# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Agent runtime layer.

Responsible for agent creation, parallel execution, single-task execution,
and RAIL-based trajectory collection.

Import directly from submodules to avoid circular imports::

    from openjiuwen.dev_tools.agentrl.agent_runtime.trajectory import TrajectoryCollector, TrajectoryCollectionRail
    from openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor import RuntimeExecutor
    from openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor import ParallelRuntimeExecutor
    from openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory import AgentFactory, build_agent_factory
"""

from openjiuwen.dev_tools.agentrl.agent_runtime.trajectory import TrajectoryCollector, TrajectoryCollectionRail
from openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor import RuntimeExecutor
from openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor import ParallelRuntimeExecutor
from openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory import AgentFactory, build_agent_factory

__all__ = [
    "TrajectoryCollector",
    "TrajectoryCollectionRail",
    "RuntimeExecutor",
    "ParallelRuntimeExecutor",
    "AgentFactory",
    "build_agent_factory",
]
