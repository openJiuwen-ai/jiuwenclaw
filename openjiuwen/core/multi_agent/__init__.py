# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Multi-Agent Module

Provides the Card + Config pattern for agent teams.

Public API::

    from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig

Legacy API (deprecated)::

    from openjiuwen.core.multi_agent.legacy import AgentTeamConfig, ControllerTeam
"""

from openjiuwen.core.session.agent_team import Session, create_agent_team_session


# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == "BaseTeam":
        from openjiuwen.core.multi_agent.team import BaseTeam
        return BaseTeam
    elif name == "TeamConfig":
        from openjiuwen.core.multi_agent.config import TeamConfig
        return TeamConfig
    elif name == "TeamCard":
        from openjiuwen.core.multi_agent.schema.team_card import TeamCard
        return TeamCard
    elif name == "EventDrivenTeamCard":
        from openjiuwen.core.multi_agent.schema.team_card import EventDrivenTeamCard
        return EventDrivenTeamCard
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "TeamCard",
    "EventDrivenTeamCard",
    "TeamConfig",
    "Session",
    "BaseTeam",
    "create_agent_team_session"
]
