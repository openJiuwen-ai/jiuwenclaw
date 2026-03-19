# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ApplicationError
from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.resource.retriever import ResourceRetriever
from openjiuwen.dev_tools.agent_builder.utils.enums import BuildState, ProgressStage, ProgressStatus
from openjiuwen.dev_tools.agent_builder.utils.utils import merge_dict_lists
from openjiuwen.dev_tools.agent_builder.utils.progress import ProgressReporter

logger = LogManager.get_logger("agent_builder")


class BaseAgentBuilder(ABC):
    """Base agent builder defining unified build flow and interface.

    Defines the unified agent build flow and interface, subclasses need to implement
    specific build logic. Uses template method pattern to define build flow framework.

    Build flow:
    1. Initialize state (INITIAL)
    2. Process request (PROCESSING)
    3. Complete build (COMPLETED)

    Example:
        ```python
        class CustomAgentBuilder(BaseAgentBuilder):
            def _handle_initial(self, query, history):
                # Implement initial state handling
                pass

            # Implement other abstract methods...
        ```
    """

    def __init__(
            self,
            llm: Model,
            history_manager: HistoryManager,
            progress_reporter: Optional[ProgressReporter] = None
    ) -> None:
        """
        Initialize the builder.

        Args:
            llm: LLM service instance
            history_manager: History manager instance
            progress_reporter: Progress reporter (optional)
        """
        self.llm: Model = llm
        self.history_manager: HistoryManager = history_manager
        self._retriever: ResourceRetriever = ResourceRetriever(llm)
        self._resource: Dict[str, Any] = {}
        self._state: BuildState = BuildState.INITIAL
        self._progress_reporter: Optional[ProgressReporter] = progress_reporter

    @property
    def state(self) -> BuildState:
        """
        Get current build state.

        Returns:
            Build state enum
        """
        return self._state

    @state.setter
    def state(self, value: BuildState) -> None:
        """
        Set build state.

        Args:
            value: Build state enum
        """
        self._state = value

    @property
    def resource(self) -> Dict[str, Any]:
        """
        Get current resource.

        Returns:
            Resource dictionary
        """
        return self._resource

    def execute(self, query: str) -> Union[str, Dict[str, Any]]:
        """
        Execute build flow (template method).

        This is the unified execution entry point, defining the build flow framework.
        Specific processing logic is implemented by subclasses.

        Args:
            query: User query

        Returns:
            Build result, may be intermediate result or final DSL
                - str: Intermediate result (clarification result, flowchart, etc.)
                - Dict[str, Any]: Final DSL

        Raises:
            ApplicationError: On state error
        """
        logger.debug(
            "Starting build flow",
            state=self._state.value,
            query_length=len(query)
        )

        try:
            if self._progress_reporter:
                self._progress_reporter.start_stage(
                    ProgressStage.INITIALIZING,
                    "Starting build flow...",
                    {"state": self._state.value}
                )

            dialog_history = self.history_manager.get_history()

            if self._progress_reporter:
                self._progress_reporter.start_stage(
                    ProgressStage.RESOURCE_RETRIEVING,
                    "Retrieving relevant resources...",
                    {"dialog_length": len(dialog_history)}
                )

            self._update_resource(dialog_history)

            if self._progress_reporter:
                resource_count = sum(
                    len(v) if isinstance(v, list) else 1
                    for v in self._resource.values()
                )
                self._progress_reporter.complete_stage(
                    "Resource retrieval completed",
                    {"resource_count": resource_count}
                )

            if self._state == BuildState.INITIAL:
                result = self._handle_initial(query, dialog_history)
            elif self._state == BuildState.PROCESSING:
                result = self._handle_processing(query, dialog_history)
            elif self._state == BuildState.COMPLETED:
                result = self._handle_completed(query, dialog_history)
            else:
                error_msg = f"Unknown build state: {self._state}"
                logger.error("Unknown build state", state=str(self._state))
                if self._progress_reporter:
                    self._progress_reporter.fail_stage(
                        error_msg,
                        "Build state error"
                    )
                raise ApplicationError(
                    StatusCode.LLM_AGENT_STATE_ERROR,
                    msg=error_msg,
                )

            logger.debug(
                "Build flow completed",
                state=self._state.value,
                result_type=type(result).__name__
            )

            if self._progress_reporter and self._state == BuildState.COMPLETED:
                self._progress_reporter.complete("Build completed")

            return result

        except Exception as e:
            logger.error(
                "Build flow failed",
                state=self._state.value,
                error=str(e),
                error_type=type(e).__name__
            )

            if self._progress_reporter:
                self._progress_reporter.fail_stage(
                    str(e),
                    "Build failed",
                    {"error_type": type(e).__name__}
                )

            raise

    def _update_resource(self, dialog_history: List[Dict[str, str]]) -> None:
        """
        Update resource information.

        Retrieves relevant resources from dialog history and merges into existing resources.
        Resource update failure will not interrupt build flow, only logs warning.

        Args:
            dialog_history: Dialog history
        """
        try:
            new_resource = self._retriever.retrieve(
                dialog_history,
                for_workflow=self._is_workflow_builder()
            )

            for key, value in new_resource.items():
                if key not in self._resource:
                    self._resource[key] = value
                else:
                    if (
                            isinstance(self._resource[key], list)
                            and isinstance(value, list)
                    ):
                        self._resource[key] = self._merge_resource_lists(
                            self._resource[key], value
                        )
                    else:
                        self._resource[key] = value

            logger.debug(
                "Resource update completed",
                resource_keys=list(self._resource.keys())
            )

        except Exception as e:
            logger.warning(
                "Resource update failed, continuing with existing resources",
                error=str(e)
            )

    def _merge_resource_lists(
            self,
            exists: List[Dict[str, Any]],
            news: List[Dict[str, Any]],
            unique_key: str = "resource_id"
    ) -> List[Dict[str, Any]]:
        """
        Merge resource lists, removing duplicates.

        Args:
            exists: Existing resource list
            news: New resource list
            unique_key: Key for uniqueness check

        Returns:
            Merged resource list
        """
        return merge_dict_lists(exists, news, unique_key)

    def reset(self) -> None:
        """
        Reset builder state.

        Restores builder to initial state, clearing all intermediate data.
        """
        self._state = BuildState.INITIAL
        self._resource = {}
        self._reset_internal_state()
        logger.debug("Builder state has been reset")

    def get_build_status(self) -> Dict[str, Any]:
        """
        Get build status information.

        Returns:
            Dictionary containing state, resources and other information
        """
        return {
            "state": self._state.value,
            "resource_count": {
                key: len(value) if isinstance(value, list) else 1
                for key, value in self._resource.items()
            }
        }

    @abstractmethod
    def _handle_initial(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> Union[str, Dict[str, Any]]:
        """
        Handle initial state.

        When builder is in initial state, handles user's first query.

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Processing result (may be intermediate result or final DSL)
        """
        pass

    @abstractmethod
    def _handle_processing(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> Union[str, Dict[str, Any]]:
        """
        Handle processing state.

        When builder is in processing state, continues processing user query.

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Processing result (may be intermediate result or final DSL)
        """
        pass

    @abstractmethod
    def _handle_completed(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> Union[str, Dict[str, Any]]:
        """
        Handle completed state.

        When builder is in completed state, handles user's final confirmation or optimization request.

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Processing result (usually final DSL)
        """
        pass

    @abstractmethod
    def _reset_internal_state(self) -> None:
        """
        Reset internal state.

        Subclass-specific state reset logic.
        """
        pass

    @abstractmethod
    def _is_workflow_builder(self) -> bool:
        """
        Check if this is a workflow builder.

        Returns:
            True if workflow builder, False if LLM Agent builder
        """
        pass
