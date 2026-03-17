# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Base Class

Provides the foundation for building guardrail implementations.
Integrates with the callback framework for event-driven detection.
"""

import logging
from abc import (
    ABC,
    abstractmethod,
)
from functools import partial
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
)

from openjiuwen.core.security.guardrail.models import (
    GuardrailResult,
    RiskAssessment,
)
from openjiuwen.core.security.guardrail.backends import GuardrailBackend
from openjiuwen.core.runner.callback.enums import HookType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import GuardrailError


class BaseGuardrail(ABC):
    """Abstract base class for guardrail implementations.

    A guardrail monitors specific events in the agent execution flow and
    performs security detection when those events are triggered. It uses the
    callback framework for event registration and can be configured with a
    custom detection backend.

    Subclasses should define DEFAULT_EVENTS class attribute and can override
    listen_events property for dynamic event configuration.

    Example:
        >>> guardrail = ToolGuardrail()
        >>> guardrail.set_backend(MyDetector())
        >>> await guardrail.register(callback_framework)

    Attributes:
        DEFAULT_EVENTS: Class-level default event names.
        _backend: The detection backend instance.
        _events: List of configured event names to monitor.
        _registered_events: List of successfully registered event names.
        _framework: Reference to the callback framework instance.
    """

    DEFAULT_EVENTS: List[str] = []

    def __init__(
            self,
            backend: Optional[GuardrailBackend] = None,
            events: Optional[List[str]] = None,
            enable_logging: bool = True
    ):
        """Initialize the guardrail.

        Args:
            backend: Optional detection backend. Can also be set later via
                set_backend().
            events: Optional list of event names to listen to. If not provided,
                uses DEFAULT_EVENTS from subclass.
            enable_logging: Enable logging output.
        """
        self._backend: Optional[GuardrailBackend] = backend

        # Configure events
        if events is not None:
            self._events: List[str] = events.copy()
        elif self.DEFAULT_EVENTS:
            self._events: List[str] = self.DEFAULT_EVENTS.copy()
        else:
            self._events: List[str] = []

        self._registered_events: List[str] = []
        self._registered_callbacks: Dict[str, Callable] = {}  # Store wrapper callbacks for unregister
        self._framework: Optional[Any] = None

        # Logging
        self.enable_logging = enable_logging
        self.logger = logging.getLogger(__name__)
        if enable_logging:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )

    @property
    def listen_events(self) -> List[str]:
        """List of event names this guardrail should listen to.

        Returns:
            List of event name strings (copy to prevent external modification).
        """
        return self._events.copy()

    def with_events(self, events: List[str]) -> "BaseGuardrail":
        """Set the event names to listen to (chainable).

        This allows runtime configuration of which events the guardrail
        should monitor. Must be called before register().

        Args:
            events: List of event name strings.

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

    async def detect(
        self,
        event_name: str,
        *args,
        **kwargs
    ) -> GuardrailResult:
        """Perform security detection for the triggered event.

        This method is called when a subscribed event is triggered. The default
        implementation delegates to the configured backend if available.
        Subclasses can override this to implement custom detection logic.

        Args:
            event_name: The name of the triggered event.
            *args: Positional arguments passed from the callback framework
                when the event is triggered.
            **kwargs: Keyword arguments (event data) passed from the callback
                framework when the event is triggered.

        Returns:
            GuardrailResult indicating whether the content is safe.

        Raises:
            ValueError: If no backend is configured and detect is not overridden.
        """
        if not self._backend:
            if self.enable_logging:
                self.logger.error(
                    f"No backend configured for {self.__class__.__name__}"
                )
            raise ValueError(
                f"No backend configured for {self.__class__.__name__}. "
                "Either provide a backend via set_backend() or override detect()."
            )

        if self.enable_logging:
            self.logger.info(
                f"Guardrail detection started for event '{event_name}'"
            )

        # Combine args and kwargs into a single data dict for backend analysis
        analysis_data = {
            "event": event_name,
            "args": args,
            **kwargs
        }
        
        if self.enable_logging:
            self.logger.debug(
                f"Analyzing data with backend: {self._backend.__class__.__name__}"
            )
        
        assessment = await self._backend.analyze(analysis_data)

        if self.enable_logging:
            if assessment.has_risk:
                self.logger.warning(
                    f"Guardrail detected risk: {assessment.risk_type} "
                    f"(level: {assessment.risk_level}) for event '{event_name}'"
                )
            else:
                self.logger.info(
                    f"Guardrail passed for event '{event_name}'"
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
            self.logger.info(
                f"Registering guardrail {self.__class__.__name__} "
                f"for events: {self.listen_events}"
            )

        # Register ERROR hook to re-throw GuardrailError after it's caught by callback framework
        async def rethrow_error_hook(e, *hook_args, **hook_kwargs):
            """Re-throw the caught exception to propagate it to agent execution layer."""
            raise e

        for event in self.listen_events:
            # Register ERROR hook for this event (one-time registration)
            framework.add_hook(event, HookType.ERROR, rethrow_error_hook)
            
            if self.enable_logging:
                self.logger.info(f"Registered ERROR hook for event '{event}'")

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
                priority=100,
                namespace="guardrail",
                tags={"guardrail", self.__class__.__name__}
            )
            self._registered_events.append(event)
            self._registered_callbacks[event] = callback_with_event
            
            if self.enable_logging:
                self.logger.info(
                    f"Registered callback for event '{event}' "
                    f"-> {callback_with_event.__name__}"
                )

    async def unregister(self) -> None:
        """Unregister this guardrail from the callback framework.

        Removes all registered callbacks. Safe to call even if not registered.
        """
        if self.enable_logging:
            self.logger.info(
                f"Unregistering guardrail {self.__class__.__name__}"
            )
        
        if self._framework:
            for event in self._registered_events:
                callback = self._registered_callbacks.get(event)
                if callback:
                    try:
                        await self._framework.unregister(event, callback)
                        if self.enable_logging:
                            self.logger.info(
                                f"Unregistered callback for event '{event}'"
                            )
                    except Exception:
                        # Ignore unregister errors (callback might not exist)
                        pass
        self._registered_events.clear()
        self._registered_callbacks.clear()

    async def _detect_callback(self, event_name: str, *args, **kwargs) -> None:
        """Internal callback wrapper for the callback framework.

        This method is called by the callback framework when a subscribed event
        is triggered. It performs security detection and raises GuardrailError
        if a risk is detected.

        Args:
            event_name: Event name (bound via closure during registration).
            *args: Positional arguments from callback framework.
            **kwargs: Event data from callback framework.

        Raises:
            GuardrailError: If detection result indicates a security risk.
        """
        if self.enable_logging:
            self.logger.info(
                f"Guardrail callback triggered for event '{event_name}'"
            )
        
        result = await self.detect(event_name, *args, **kwargs)

        if not result.is_safe:
            risk_info = {
                "risk_type": result.risk_type or "unknown",
                "risk_level": result.risk_level.name if result.risk_level else "UNKNOWN",
                "event": event_name,
            }
            if result.details:
                risk_info.update(result.details)

            if self.enable_logging:
                self.logger.warning(
                    f"Guardrail blocked event '{event_name}': "
                    f"{result.risk_type or 'unknown'} risk detected"
                )

            raise GuardrailError(
                StatusCode.GUARDRAIL_BLOCKED,
                msg=f"Guardrail blocked: {result.risk_type or 'unknown'} risk detected",
                details=risk_info
            )
