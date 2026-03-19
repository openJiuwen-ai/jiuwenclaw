# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Base Class

Provides the foundation for building guardrail implementations.
Integrates with the callback framework for event-driven detection.

Design principles:
- Two-layer architecture: BaseGuardrail (event handling) + GuardrailBackend (detection)
- extract_context() abstract method for data preprocessing
- BaseGuardrail responsible for registering with callback framework
"""

from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
)

from openjiuwen.core.security.guardrail.context import GuardrailContext
from openjiuwen.core.security.guardrail.backends import GuardrailBackend
from openjiuwen.core.security.guardrail.enums import RiskLevel
from openjiuwen.core.security.guardrail.models import (
    GuardrailResult,
    RiskAssessment,
)
from openjiuwen.core.runner.callback.enums import HookType
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import GuardrailError
from openjiuwen.core.common.logging import logger


class BaseGuardrail(ABC):
    """Abstract base class for guardrail implementations.

    A guardrail monitors specific events in the agent execution flow and
    performs security detection when those events are triggered. It uses the
    callback framework for event registration and delegates detection logic
    to a configured GuardrailBackend.

    Subclasses should define DEFAULT_EVENTS class attribute and override
    extract_context() to preprocess event data into GuardrailContext.

    Example:
        >>> guardrail = PromptInjectionGuardrail()
        >>> guardrail.set_backend(MyDetectorBackend())
        >>> await guardrail.register(callback_framework)

    Attributes:
        DEFAULT_EVENTS: Class-level default event names.
        DEFAULT_PRIORITY: Default priority for callback registration.
        NAMESPACE: Namespace for organizing callbacks.
        _backend: The detection backend instance.
        _events: List of configured event names to monitor.
        _registered_events: List of successfully registered event names.
        _framework: Reference to the callback framework instance.
    """

    DEFAULT_EVENTS: List[Any] = []
    DEFAULT_PRIORITY: int = 100
    NAMESPACE: str = "guardrail"

    def __init__(
        self,
        *,
        events: Optional[List[Any]] = None,
        backend: Optional[GuardrailBackend] = None,
        priority: Optional[int] = None,
        enable_logging: bool = True
    ):
        """Initialize the guardrail.

        Args:
            events: Optional list of event names to listen to. If not provided,
                uses DEFAULT_EVENTS from subclass.
            backend: Optional detection backend. Can also be set later via
                set_backend().
            priority: Optional priority for callback registration. Uses
                DEFAULT_PRIORITY if not provided.
            enable_logging: Enable logging output.
        """
        self._backend: Optional[GuardrailBackend] = backend

        # Configure events
        if events is not None:
            self._events: List[Any] = events.copy()
        elif self.DEFAULT_EVENTS:
            self._events: List[Any] = self.DEFAULT_EVENTS.copy()
        else:
            self._events: List[Any] = []

        self._priority = priority if priority is not None else self.DEFAULT_PRIORITY

        self._registered_events: List[Any] = []
        self._registered_callbacks: Dict[Any, Callable] = {}
        self._framework: Optional[Any] = None

        self.enable_logging = enable_logging

    @property
    def listen_events(self) -> List[Any]:
        """List of event names this guardrail should listen to.

        Returns:
            List of event names (copy to prevent external modification).
        """
        return self._events.copy()

    def with_events(self, events: List[Any]) -> "BaseGuardrail":
        """Set the event names to listen to (chainable).

        This allows runtime configuration of which events the guardrail
        should monitor. Must be called before register().

        Args:
            events: List of event names.

        Returns:
            Self for method chaining.
        """
        self._events = events.copy()
        return self

    def set_backend(self, backend: GuardrailBackend) -> "BaseGuardrail":
        """Set the detection backend.

        Args:
            backend: The detection backend to use.

        Returns:
            Self for method chaining.
        """
        self._backend = backend
        return self

    def get_backend(self) -> Optional[GuardrailBackend]:
        """Get the current detection backend.

        Returns:
            The configured detection backend, or None if not set.
        """
        return self._backend

    def get_registered_events(self) -> List[Any]:
        """Get the list of successfully registered event names.

        Returns:
            List of registered event names (copy to prevent external modification).
        """
        return self._registered_events.copy()

    def is_event_registered(self, event: Any) -> bool:
        """Check if an event has been registered.

        Args:
            event: The event name to check.

        Returns:
            True if the event is registered, False otherwise.
        """
        return event in self._registered_events

    @abstractmethod
    def extract_context(
        self,
        event: Any,
        *args,
        **kwargs
    ) -> GuardrailContext:
        """Extract guardrail context from event data.

        This method MUST be implemented by subclasses. It is responsible for
        extracting and preprocessing data from the raw event arguments into
        a unified GuardrailContext format that the backend can understand.

        Args:
            event: The event that was triggered.
            *args: Positional arguments passed from the callback framework.
            **kwargs: Keyword arguments passed from the callback framework.

        Returns:
            GuardrailContext with unified data format for backend analysis.
        """
        pass

    async def detect(
        self,
        event: Any,
        *args,
        **kwargs
    ) -> GuardrailResult:
        """Perform security detection for the triggered event.

        This method is called when a subscribed event is triggered. It
        preprocesses the data via extract_context(), then delegates to the
        configured backend for actual detection.

        Args:
            event: The event that was triggered.
            *args: Positional arguments from the callback framework.
            **kwargs: Keyword arguments from the callback framework.

        Returns:
            GuardrailResult indicating whether the content is safe.

        Raises:
            ValueError: If no backend is configured.
        """
        if not self._backend:
            if self.enable_logging:
                logger.error(
                    "No backend configured for %s", self.__class__.__name__
                )
            raise ValueError(
                f"No backend configured for {self.__class__.__name__}. "
                "Use set_backend() to set one."
            )

        if self.enable_logging:
            logger.info(
                "Guardrail detection started for event '%s'", event
            )

        ctx = self.extract_context(event, *args, **kwargs)

        if self.enable_logging:
            logger.debug(
                "Analyzing data with backend: %s", self._backend.__class__.__name__
            )

        assessment = await self._backend.analyze(ctx)

        if self.enable_logging:
            if assessment.has_risk:
                logger.warning(
                    "Guardrail detected risk: %s (level: %s) for event '%s'",
                    assessment.risk_type,
                    assessment.risk_level,
                    event
                )
            else:
                logger.info(
                    "Guardrail passed for event '%s'", event
                )

        return GuardrailResult(
            is_safe=not assessment.has_risk,
            risk_level=assessment.risk_level,
            risk_type=assessment.risk_type,
            details=assessment.details
        )

    async def register(self, framework: Any) -> None:
        """Register this guardrail with the callback framework.

        This registers the guardrail's detect method as a callback for all
        events specified in listen_events.

        Args:
            framework: AsyncCallbackFramework instance.
        """
        self._framework = framework

        if self.enable_logging:
            logger.info(
                "Registering guardrails %s for events: %s",
                self.__class__.__name__,
                self.listen_events
            )

        for event in self.listen_events:
            # Create a wrapper that binds event_name via closure
            # This ensures each callback knows which event triggered it
            def make_callback(evt):
                async def callback_wrapper(*args, **kwargs):
                    return await self._detect_callback(evt, *args, **kwargs)
                callback_wrapper.__name__ = f"_detect_callback_{evt}"
                return callback_wrapper

            callback_with_event = make_callback(event)

            await framework.register(
                event=event,
                callback=callback_with_event,
                priority=self._priority,
                namespace=self.NAMESPACE,
                tags={"guardrail", self.__class__.__name__}
            )
            self._registered_events.append(event)
            self._registered_callbacks[event] = callback_with_event

            if self.enable_logging:
                logger.info(
                    "Registered callback for event '%s' -> %s",
                    event,
                    callback_with_event.__name__
                )

    async def unregister(self) -> None:
        """Unregister this guardrail from the callback framework.

        Removes all registered callbacks. Safe to call even if not registered.
        """
        if self.enable_logging:
            logger.info(
                "Unregistering guardrail %s", self.__class__.__name__
            )

        if self._framework:
            for event in self._registered_events:
                callback = self._registered_callbacks.get(event)
                if callback:
                    try:
                        await self._framework.unregister(event, callback)
                        if self.enable_logging:
                            logger.info(
                                "Unregistered callback for event '%s'", event
                            )
                    except Exception as e:
                        if self.enable_logging:
                            logger.warning(
                                "Failed to unregister callback for event '%s': %s",
                                event,
                                str(e)
                            )
        self._registered_events.clear()
        self._registered_callbacks.clear()

    async def _detect_callback(self, event: Any, *args, **kwargs) -> None:
        """Internal callback wrapper for the callback framework.

        This method is called by the callback framework when a subscribed event
        is triggered. It performs security detection and raises GuardrailError
        or AbortError if a risk is detected.

        Args:
            event: Event that triggered this callback.
            *args: Positional arguments from callback framework.
            **kwargs: Event data from callback framework.

        Raises:
            GuardrailError: If detection result indicates a security risk (non-critical).
            AbortError: If detection result indicates a critical security risk.
        """
        if self.enable_logging:
            logger.info(
                "Guardrail callback triggered for event '%s'", event
            )

        result = await self.detect(event, *args, **kwargs)

        if not result.is_safe:
            risk_info = {
                "risk_type": result.risk_type or "unknown",
                "risk_level": result.risk_level.name if result.risk_level else "UNKNOWN",
                "event": str(event),
            }
            if result.details:
                risk_info.update(result.details)

            if self.enable_logging:
                logger.warning(
                    "Guardrail blocked event '%s': %s risk detected",
                    event,
                    result.risk_type or "unknown"
                )

            if result.risk_level == RiskLevel.CRITICAL:
                raise AbortError(
                    reason=f"Critical security risk detected: {result.risk_type or 'unknown'}",
                    details=risk_info
                )

            raise GuardrailError(
                StatusCode.GUARDRAIL_BLOCKED,
                msg=f"Guardrail blocked: {result.risk_type or 'unknown'} risk detected",
                details=risk_info
            )
