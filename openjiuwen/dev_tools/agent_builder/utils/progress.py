# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import time
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from openjiuwen.core.common.logging import LogManager
from openjiuwen.dev_tools.agent_builder.utils.enums import ProgressStage, ProgressStatus

logger = LogManager.get_logger("agent_builder")


@dataclass
class ProgressStep:
    """Progress step information

    Represents a step in the build process, including stage, status, message, etc.

    Attributes:
        stage: Build stage
        status: Step status
        message: Step description message
        details: Details dict
        timestamp: Step timestamp
        duration: Step duration (seconds)
        error: Error message (if any)
    """

    stage: ProgressStage
    status: ProgressStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dict

        Returns:
            Dict format step information
        """
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "duration": self.duration,
            "error": self.error
        }


@dataclass
class BuildProgress:
    """Build progress information

    Represents the progress state of the entire build process.

    Attributes:
        session_id: Session ID
        agent_type: Agent type
        current_stage: Current stage
        current_status: Current status
        current_message: Current message
        steps: Step list
        overall_progress: Overall progress (0-100)
        start_time: Start time
        last_update_time: Last update time
        error: Error message (if any)
    """

    session_id: str
    agent_type: str
    current_stage: ProgressStage
    current_status: ProgressStatus
    current_message: str
    steps: List[ProgressStep] = field(default_factory=list)
    overall_progress: float = 0.0
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_update_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dict

        Returns:
            Dict format progress information
        """
        return {
            "session_id": self.session_id,
            "agent_type": self.agent_type,
            "current_stage": self.current_stage.value,
            "current_status": self.current_status.value,
            "current_message": self.current_message,
            "steps": [step.to_dict() for step in self.steps],
            "overall_progress": self.overall_progress,
            "start_time": self.start_time.isoformat(),
            "last_update_time": self.last_update_time.isoformat(),
            "error": self.error
        }


class ProgressReporter:
    """Progress Reporter

    Responsible for collecting and pushing build progress information, supports observer pattern.
    Notifies progress updates via callback functions.

    Example:
        ```python
        reporter = ProgressReporter("session_123", "llm_agent")

        def on_progress(progress: BuildProgress):
            print(f"Progress: {progress.overall_progress}%")

        reporter.add_callback(on_progress)
        reporter.start_stage(ProgressStage.CLARIFYING, "Clarifying requirements...")
        ```
    """

    def __init__(self, session_id: str, agent_type: str) -> None:
        """
        Initialize progress reporter

        Args:
            session_id: Session ID
            agent_type: Agent type
        """
        self.session_id: str = session_id
        self.agent_type: str = agent_type
        self.progress: BuildProgress = BuildProgress(
            session_id=session_id,
            agent_type=agent_type,
            current_stage=ProgressStage.INITIALIZING,
            current_status=ProgressStatus.PENDING,
            current_message="Initializing..."
        )
        self._callbacks: List[Callable[[BuildProgress], None]] = []
        self._step_start_times: Dict[ProgressStage, float] = {}

    def add_callback(self, callback: Callable[[BuildProgress], None]) -> None:
        """
        Add progress callback function

        Args:
            callback: Callback function, receives BuildProgress parameter
        """
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[BuildProgress], None]) -> None:
        """
        Remove progress callback function

        Args:
            callback: Callback function to remove
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify(self) -> None:
        """Notify all callback functions"""
        for callback in self._callbacks:
            try:
                callback(self.progress)
            except Exception as e:
                logger.error(
                    "Progress callback execution failed",
                    error=str(e),
                    callback=str(callback),
                    session_id=self.session_id
                )

    def start_stage(
            self,
            stage: ProgressStage,
            message: str,
            details: Optional[Dict[str, Any]] = None,
            progress: Optional[float] = None
    ) -> None:
        """
        Start a build stage

        Args:
            stage: Build stage
            message: Stage description message
            details: Details
            progress: Overall progress (0-100), if None then auto-calculated
        """
        # End previous stage
        if self.progress.current_stage != ProgressStage.INITIALIZING:
            self._end_current_stage(ProgressStatus.SUCCESS)

        # Record start time
        self._step_start_times[stage] = time.time()

        # Update progress
        self.progress.current_stage = stage
        self.progress.current_status = ProgressStatus.RUNNING
        self.progress.current_message = message
        self.progress.last_update_time = datetime.now(timezone.utc)

        if progress is not None:
            self.progress.overall_progress = progress
        else:
            # Auto-calculate progress based on stage
            self.progress.overall_progress = self._calculate_progress(stage)

        # Add step
        step = ProgressStep(
            stage=stage,
            status=ProgressStatus.RUNNING,
            message=message,
            details=details or {}
        )
        self.progress.steps.append(step)

        logger.info(
            "Started build stage",
            session_id=self.session_id,
            stage=stage.value,
            message=message
        )

        self._notify()

    def update_stage(
            self,
            message: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
            progress: Optional[float] = None
    ) -> None:
        """
        Update current stage information

        Args:
            message: Update message
            details: Details
            progress: Overall progress
        """
        if message:
            self.progress.current_message = message

        if details and self.progress.steps:
            self.progress.steps[-1].details.update(details)

        if progress is not None:
            self.progress.overall_progress = progress

        self.progress.last_update_time = datetime.now(timezone.utc)
        self._notify()

    def complete_stage(
            self,
            message: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Complete current stage

        Args:
            message: Completion message
            details: Details
        """
        self._end_current_stage(ProgressStatus.SUCCESS, message, details)

    def fail_stage(
            self,
            error: str,
            message: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Mark current stage as failed

        Args:
            error: Error message
            message: Failure message
            details: Details
        """
        self.progress.error = error
        self._end_current_stage(ProgressStatus.FAILED, message, details, error)

    def warn_stage(
            self,
            warning: str,
            message: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Mark current stage with warning

        Args:
            warning: Warning message
            message: Warning message
            details: Details
        """
        if message:
            self.progress.current_message = message

        if self.progress.steps:
            self.progress.steps[-1].status = ProgressStatus.WARNING
            self.progress.steps[-1].details["warning"] = warning
            if details:
                self.progress.steps[-1].details.update(details)

        self.progress.current_status = ProgressStatus.WARNING
        self.progress.last_update_time = datetime.now(timezone.utc)

        logger.warning(
            "Build stage warning",
            session_id=self.session_id,
            stage=self.progress.current_stage.value,
            warning=warning
        )

        self._notify()

    def _end_current_stage(
            self,
            status: ProgressStatus,
            message: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
            error: Optional[str] = None
    ) -> None:
        """End current stage"""
        if not self.progress.steps:
            return

        current_step = self.progress.steps[-1]
        current_step.status = status

        if message:
            current_step.message = message

        if details:
            current_step.details.update(details)

        if error:
            current_step.error = error
            self.progress.error = error

        # Calculate duration
        stage = current_step.stage
        if stage in self._step_start_times:
            duration = time.time() - self._step_start_times[stage]
            current_step.duration = duration
            del self._step_start_times[stage]

        self.progress.current_status = status
        if message:
            self.progress.current_message = message

        self.progress.last_update_time = datetime.now(timezone.utc)

    def _calculate_progress(self, stage: ProgressStage) -> float:
        """
        Calculate overall progress based on stage

        Args:
            stage: Current stage

        Returns:
            Progress percentage (0-100)
        """
        # LLM Agent progress mapping
        llm_agent_progress: Dict[ProgressStage, float] = {
            ProgressStage.INITIALIZING: 0,
            ProgressStage.CLARIFYING: 20,
            ProgressStage.RESOURCE_RETRIEVING: 40,
            ProgressStage.GENERATING_CONFIG: 60,
            ProgressStage.TRANSFORMING_DSL: 80,
            ProgressStage.COMPLETED: 100,
        }

        # Workflow Agent progress mapping
        workflow_progress: Dict[ProgressStage, float] = {
            ProgressStage.INITIALIZING: 0,
            ProgressStage.DETECTING_INTENTION: 10,
            ProgressStage.GENERATING_WORKFLOW_DESIGN: 25,
            ProgressStage.GENERATING_DL: 45,
            ProgressStage.VALIDATING_DL: 60,
            ProgressStage.REFINING_DL: 70,
            ProgressStage.TRANSFORMING_MERMAID: 85,
            ProgressStage.TRANSFORMING_WORKFLOW_DSL: 95,
            ProgressStage.COMPLETED: 100,
        }

        if self.agent_type == "llm_agent":
            return llm_agent_progress.get(stage, 0.0)
        elif self.agent_type == "workflow":
            return workflow_progress.get(stage, 0.0)

        return 0.0

    def get_progress(self) -> BuildProgress:
        """
        Get current progress

        Returns:
            Build progress information
        """
        return self.progress

    def complete(self, message: str = "Build completed") -> None:
        """
        Mark build as completed

        Args:
            message: Completion message
        """
        self.progress.current_stage = ProgressStage.COMPLETED
        self.progress.current_status = ProgressStatus.SUCCESS
        self.progress.current_message = message
        self.progress.overall_progress = 100.0
        self.progress.last_update_time = datetime.now(timezone.utc)

        # End last step
        if self.progress.steps:
            self._end_current_stage(ProgressStatus.SUCCESS, message)

        logger.info(
            "Build completed",
            session_id=self.session_id,
            agent_type=self.agent_type
        )

        self._notify()


class ProgressManager:
    """Progress Manager

    Manages progress information for all sessions, provides creation and query of progress reporters.

    Example:
        ```python
        manager = ProgressManager()
        reporter = manager.create_reporter("session_123", "llm_agent")
        progress = manager.get_progress("session_123")
        ```
    """

    def __init__(self) -> None:
        """Initialize progress manager"""
        self._reporters: Dict[str, ProgressReporter] = {}

    def get_reporter(self, session_id: str) -> Optional[ProgressReporter]:
        """
        Get progress reporter

        Args:
            session_id: Session ID

        Returns:
            Progress reporter instance, or None if not exists
        """
        return self._reporters.get(session_id)

    def create_reporter(
            self,
            session_id: str,
            agent_type: str
    ) -> ProgressReporter:
        """
        Create progress reporter

        If a reporter with the same session_id exists, returns the existing instance.

        Args:
            session_id: Session ID
            agent_type: Agent type

        Returns:
            Progress reporter instance
        """
        if session_id in self._reporters:
            return self._reporters[session_id]

        reporter = ProgressReporter(session_id, agent_type)
        self._reporters[session_id] = reporter
        return reporter

    def remove_reporter(self, session_id: str) -> None:
        """
        Remove progress reporter

        Args:
            session_id: Session ID
        """
        if session_id in self._reporters:
            del self._reporters[session_id]

    def get_progress(self, session_id: str) -> Optional[BuildProgress]:
        """
        Get build progress

        Args:
            session_id: Session ID

        Returns:
            Build progress information, or None if not exists
        """
        reporter = self._reporters.get(session_id)
        return reporter.get_progress() if reporter else None


# Global progress manager singleton, for reuse by executor / REST / WebSocket modules
progress_manager = ProgressManager()
