# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any, Optional, List

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.dev_tools.agent_builder.executor import AgentBuilderExecutor, HistoryManager
from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.utils.progress import progress_manager

logger = LogManager.get_logger("agent_builder")


class AgentBuilder:
    """Unified Agent Builder Entry Point

    Provides simple interface for building LLM Agent and Workflow Agent.
    Manages session state and builder instances internally.

    Example:
        ```python
        builder = AgentBuilder(model_info={...})

        result = builder.build_llm_agent(
            query="Create a customer service assistant",
            session_id="session_123"
        )

        result = builder.build_workflow(
            query="Create a data processing workflow",
            session_id="session_456"
        )

        progress = AgentBuilder.get_progress("session_123")
        ```
    """

    def __init__(
            self,
            model_info: Optional[Dict[str, Any]] = None,
            history_manager_map: Optional[Dict[str, HistoryManager]] = None,
            agent_builder_map: Optional[Dict[str, BaseAgentBuilder]] = None
    ) -> None:
        """
        Initialize builder

        Args:
            model_info: LLM model configuration
            history_manager_map: Session history manager map (optional, for cross-instance reuse)
            agent_builder_map: Builder map (optional, for cross-instance reuse)
        """
        self.model_info: Dict[str, Any] = model_info or {}
        self.history_manager_map: Dict[str, HistoryManager] = (
                history_manager_map or {}
        )
        self.agent_builder_map: Dict[str, BaseAgentBuilder] = (
                agent_builder_map or {}
        )

    def build_agent(
            self,
            query: str,
            session_id: str,
            agent_type: str = "llm_agent",
    ) -> Dict[str, Any]:
        """
        Build agent (unified interface)

        Args:
            query: User query
            session_id: Session ID
            agent_type: Agent type ('llm_agent' or 'workflow')

        Returns:
            Build result dict containing status and corresponding data
        """
        executor = AgentBuilderExecutor(
            query=query,
            session_id=session_id,
            agent_type=agent_type,
            history_manager_map=self.history_manager_map,
            agent_builder_map=self.agent_builder_map,
            model_info=self.model_info,
            enable_progress=True
        )

        result = executor.execute()

        status_info = executor.get_build_status()
        state = status_info.get("state", "unknown")

        response: Dict[str, Any] = {
            "status": self._map_state_to_status(state, agent_type),
            "session_id": session_id,
            "agent_type": agent_type
        }

        if isinstance(result, str):
            try:
                dsl = JsonUtils.safe_json_loads(result)
                response["dsl"] = dsl
                response["status"] = "completed"
            except Exception:
                if agent_type == "llm_agent":
                    response["response"] = result
                    response["status"] = "clarifying"
                elif agent_type == "workflow":
                    if "graph" in result or "flowchart" in result:
                        response["mermaid_code"] = result
                        response["status"] = "processing"
                    else:
                        response["response"] = result
                        response["status"] = "requesting"
        elif isinstance(result, dict):
            response.update(result)
        else:
            response["response"] = str(result)

        return response

    def build_llm_agent(
            self,
            query: str,
            session_id: str,
    ) -> Dict[str, Any]:
        """
        Build LLM Agent

        Args:
            query: User query
            session_id: Session ID

        Returns:
            Build result
        """
        return self.build_agent(query, session_id, "llm_agent")

    def build_workflow(
            self,
            query: str,
            session_id: str,
    ) -> Dict[str, Any]:
        """
        Build Workflow Agent

        Args:
            query: User query
            session_id: Session ID

        Returns:
            Build result
        """
        return self.build_agent(query, session_id, "workflow")

    def get_session_history(
            self,
            session_id: str,
            k: Optional[int] = None
    ) -> List[Dict[str, str]]:
        """
        Get session history

        Args:
            session_id: Session ID
            k: Return latest k messages, None for all

        Returns:
            Message list
        """
        history_manager = self.history_manager_map.get(session_id)
        if not history_manager:
            return []
        if k:
            return history_manager.get_latest_k_messages(k)
        return history_manager.get_history()

    def clear_session(self, session_id: str) -> None:
        """
        Clear session history
        
        Args:
            session_id: Session ID
        """
        if session_id in self.history_manager_map:
            self.history_manager_map[session_id].clear()

        if session_id in self.agent_builder_map:
            self.agent_builder_map[session_id].reset()

    def get_build_status(
            self,
            session_id: str
    ) -> Dict[str, Any]:
        """
        Get build status

        Args:
            session_id: Session ID

        Returns:
            Build status info
        """
        if session_id not in self.agent_builder_map:
            return {
                "session_id": session_id,
                "state": "not_found"
            }

        builder = self.agent_builder_map[session_id]
        return builder.get_build_status()

    
    @staticmethod
    def get_progress(
            session_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get build progress

        Args:
            session_id: Session ID

        Returns:
            Build progress info, None if not exists
        """
        progress = progress_manager.get_progress(session_id)
        if progress:
            return progress.to_dict()
        return None

    @staticmethod
    def _map_state_to_status(state: str, agent_type: str) -> str:
        """
        Map internal state to external status

        Args:
            state: Internal state
            agent_type: Agent type

        Returns:
            External status
        """
        state_mapping: Dict[str, str] = {
            "initial": "clarifying" if agent_type == "llm_agent" else "requesting",
            "processing": "processing",
            "completed": "completed"
        }
        return state_mapping.get(state, "unknown")
