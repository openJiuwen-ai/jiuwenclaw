# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from enum import Enum
from typing import Union, List, Optional, Dict, Any

from pydantic import BaseModel

from openjiuwen.core.common.logging import LogEventType, create_log_event
from openjiuwen.core.common.logging.events import SysOperationEvent
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.utils.callable_schema_extractor import CallableSchemaExtractor
from openjiuwen.core.sys_operation.config import LocalWorkConfig, SandboxGatewayConfig


class OperationMode(str, Enum):
    """Enum for operation mode."""
    LOCAL = "local"
    SANDBOX = "sandbox"


class BaseOperation:
    """BaseOperation for file, code, shell and so on."""

    def __init__(
            self,
            name: str,
            mode: OperationMode,
            description: str,
            run_config: Union[LocalWorkConfig, SandboxGatewayConfig]):
        self.name = name
        self.mode = mode
        self.description = description
        self._run_config = run_config

    @staticmethod
    def _safe_model_dump(obj: BaseModel, default=None) -> Dict[str, Any]:
        """Safely call model_dump method of an object, return default value if failed

        Args:
            obj: The target object to call model_dump on
            default: Default return value when model_dump fails (default: {"error": "model_dump failed"})

        Returns:
            dict: Result of model_dump or default value
        """
        if default is None:
            default = {"error": "model_dump failed"}
        try:
            # Check if the object has valid model_dump method before calling
            if hasattr(obj, "model_dump") and callable(obj.model_dump):
                return obj.model_dump()
            return default
        # Catch all possible exceptions (can be refined to specific exceptions like AttributeError/ValueError)
        except Exception:
            return default

    def list_tools(self) -> List[ToolCard]:
        """Retrieves a list of tool cards.

        Returns:
            List of ToolCard objects containing tool information
        """
        pass

    def _create_sys_operation_event(
            self,
            *,
            event_type: LogEventType | str,
            method_name: str,
            method_params: Optional[Dict[str, Any]] = None,
            method_result: Optional[Dict[str, Any]] = None,
            method_exec_time_ms: Optional[float] = None,
            **kwargs) -> SysOperationEvent | None:
        """Creates a system operation log event with contextual information.

        Args:
            event_type: Type of the system operation event (enum or string)
            method_name: Name of the method/function being logged
            method_params: Optional dictionary of parameters passed to the method
            method_result: Optional dictionary of results returned by the method
            method_exec_time_ms: Optional execution time of the method in milliseconds
            **kwargs: Additional arbitrary parameters to include in the log event

        Returns:
            Created log event object from create_log_event
        """
        if "module_id" not in kwargs:
            kwargs["module_id"] = "sys_operation"
        if "module_name" not in kwargs:
            kwargs["module_name"] = "sys_operation"
        event = create_log_event(
            event_type=event_type,
            operation_name=self.name,
            operation_mode=self.mode,
            operation_desc=self.description,
            method_name=method_name,
            method_params=method_params,
            method_result=method_result,
            method_exec_time_ms=method_exec_time_ms,
            **kwargs
        )
        return event if isinstance(event, SysOperationEvent) else None

    def _generate_tool_cards(self, method_names: List[str]) -> List[ToolCard]:
        """Generate tool cards based on method names.
        
        Args:
            method_names: List of method names to be exposed as tools.
        
        Returns:
            List of ToolCard objects.
        """
        tool_cards = []
        for method_name in method_names:
            if hasattr(self, method_name):
                method = getattr(self, method_name)
                tool_cards.append(
                    ToolCard(
                        name=method_name,
                        description=CallableSchemaExtractor.extract_function_description(method),
                        input_params=CallableSchemaExtractor.generate_schema(method)
                    )
                )
        return tool_cards
