# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import List, Tuple, Union
from openjiuwen.core.sys_operation.registry import OperationRegistry
from openjiuwen.core.sys_operation.sys_operation import SysOperationCard, SysOperation
from openjiuwen.core.foundation.tool import LocalFunction


class SysOperationToolAdapter:
    """
    Adapter for converting SysOperation to LocalFunction tools.
    """

    @staticmethod
    def extract_tools(card: SysOperationCard, instance: SysOperation) -> List[Tuple[str, LocalFunction]]:
        """Extract all tools from SysOperation and wrap them as LocalFunction instances.

        Args:
            card: SysOperationCard containing operation metadata
            instance: SysOperation instance to extract tools from

        Returns:
            List of (tool_id, LocalFunction) tuples ready for registration.
            tool_id format: "{card.id}.{op_type}.{tool_name}"

        """
        tools = []

        # Use dynamic list from OperationRegistry (includes both built-in and custom operations)
        for op_type in OperationRegistry.get_supported_operations(card.mode):
            # Trigger lazy loading by accessing the operation method on the instance
            sub_op_getter = getattr(instance, op_type, None)
            if not sub_op_getter or not callable(sub_op_getter):
                continue

            sub_op = sub_op_getter()
            if not sub_op:
                continue

            # Get all tool cards from this sub-operation
            tool_cards = sub_op.list_tools()
            if not tool_cards:
                continue

            for tool_card in tool_cards:
                # Generate unique tool ID
                tool_id = SysOperationCard.generate_tool_id(card.id, op_type, tool_card.name)

                # Create a copy of the card with the specific tool_id
                new_card = tool_card.model_copy()
                new_card.id = tool_id

                # Get method reference via reflection
                func = getattr(sub_op, tool_card.name, None)

                if not func or not callable(func):
                    continue

                # Wrap as LocalFunction
                local_func = LocalFunction(card=new_card, func=func)

                tools.append((tool_id, local_func))

        return tools

    @staticmethod
    def get_tool_id_prefix(sys_operation_id: Union[str, List[str]]) -> Union[str, List[str]]:
        """Deprecated: Use ToolMgr index for cleanup instead."""
        if isinstance(sys_operation_id, list):
            return [f"{op_id}." for op_id in sys_operation_id]
        else:
            return f"{sys_operation_id}."
