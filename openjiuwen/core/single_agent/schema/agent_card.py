"""AgentCard Definition

Main classes included:
 - AgentCard: Agent card format definition

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from typing import Optional, Any, Type
from pydantic import BaseModel, Field

from openjiuwen.core.common.schema.card import BaseCard
from openjiuwen.core.foundation.tool import ToolInfo


class AgentCard(BaseCard):
    """Agent Card Data Class
    """
    input_params: Optional[dict[str, Any] | Type[BaseModel]] = Field(default=None)
    output_params: Optional[dict[str, Any] | Type[BaseModel]] = Field(default=None)

    def tool_info(self):
        return ToolInfo(
            name=self.name,
            description=self.description,
            parameters=self.input_params if self.input_params else {}
        )
