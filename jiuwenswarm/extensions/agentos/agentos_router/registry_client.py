# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass

from jiuwenswarm.extensions.agentos.agentos_router.models import AgentInfo, ImageInfo


@dataclass(frozen=True)
class RegistryConfig:
    endpoint: str = ""
    request_timeout_s: float = 10.0


class RegistryClient:
    """Registry facade reserved for AgentOS phase two integration."""

    def __init__(self, config: RegistryConfig) -> None:
        self._config = config
        self._registered_agents: dict[str, AgentInfo] = {}

    async def register_agent(self, agent_info: AgentInfo) -> None:
        self._registered_agents[agent_info.agent_id] = agent_info.copy()

    async def unregister_agent(self, agent_id: str) -> None:
        self._registered_agents.pop(str(agent_id or "").strip(), None)

    async def report_heartbeat(self, agent_id: str) -> None:
        del agent_id

    async def get_image_info(self, image_name: str) -> ImageInfo:
        return ImageInfo(
            image_name=str(image_name or "").strip(),
            metadata={"source": "phase_one_default"},
        )

    async def close(self) -> None:
        self._registered_agents.clear()
