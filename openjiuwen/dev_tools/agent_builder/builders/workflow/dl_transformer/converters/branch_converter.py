# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Any, Optional

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import SourceType, Edge
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class BranchConverter(BaseConverter):
    """Branch node converter."""

    BRANCH_OPERATOR_MAP: Dict[str, str] = {
        "eq": "eq",
        "not_eq": "neq",
        "contain": "contains",
        "not_contain": "not_contains",
        "is_empty": "is_empty",
        "is_not_empty": "is_not_empty",
        "longer_than": "gt",
        "longer_than_or_eq": "gte",
        "short_than": "lt",
        "short_than_or_eq": "lte",
        "len_longer_than": "len_longer_than",
        "len_longer_than_or_eq": "len_longer_than_or_eq",
        "len_shorter_than": "len_shorter_than",
        "len_shorter_than_or_eq": "len_shorter_than_or_eq",
    }
    BRANCH_LOGIC_MAP: Dict[str, int] = {
        "or": 1,
        "and": 2
    }

    @staticmethod
    def _convert_branches(
            conditions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert branch list.

        Args:
            conditions: Condition list

        Returns:
            Branch list
        """
        branches: List[Dict[str, Any]] = []
        for cond in conditions:
            if "expressions" in cond:
                branches.append({
                    "conditions": [
                        BranchConverter._convert_expression(expr)
                        for expr in cond["expressions"]
                    ],
                    "logic": BranchConverter.BRANCH_LOGIC_MAP[cond["operator"]],
                    "branchId": cond["branch"]
                })
            elif cond["expression"] != "default":
                branches.append({
                    "conditions": [
                        BranchConverter._convert_expression(cond["expression"])
                    ],
                    "branchId": cond["branch"]
                })
            else:
                branches.append({
                    "conditions": [],
                    "branchId": cond["branch"]
                })
        return branches

    @staticmethod
    def _convert_expression(expression: str) -> Dict[str, Any]:
        """Convert expression.

        Args:
            expression: Expression string

        Returns:
            Converted expression dictionary
        """
        operator = next(
            (opt for opt in BranchConverter.BRANCH_OPERATOR_MAP
             if opt in expression),
            None
        )
        if not operator:
            return {}

        parts = expression.split(operator, 1)
        left_str = parts[0].strip() if parts else ""
        right_str = parts[1].strip() if len(parts) > 1 else ""

        left = BranchConverter._build_side(left_str)
        right = BranchConverter._build_side(right_str)

        if right:
            return {
                "left": left,
                "operator": BranchConverter.BRANCH_OPERATOR_MAP[operator],
                "right": right
            }
        return {
            "left": left,
            "operator": BranchConverter.BRANCH_OPERATOR_MAP[operator]
        }

    @staticmethod
    def _build_side(value_str: str) -> Optional[Dict[str, Any]]:
        """Build one side of expression.

        Args:
            value_str: Value string

        Returns:
            Built value dictionary, or None if empty
        """
        if not value_str:
            return None

        if "${" in value_str:
            return ConverterUtils.convert_ref_variable(value_str)

        return {
            "type": SourceType.constant.value,
            "content": value_str,
            "schema": {
                "type": "string",
                "extra": {"weak": True}
            }
        }

    def _convert_specific_config(self) -> None:
        """Convert Branch node specific configuration."""
        self.node.data.branches = self._convert_branches(
            self.node_data["parameters"]["conditions"]
        )

    def convert_edges(self) -> None:
        """Convert edges (Branch node has multiple branches)."""
        for cond in self.node_data["parameters"]["conditions"]:
            self.edges.append(
                Edge(
                    source_node_id=self.node_data["id"],
                    target_node_id=cond["next"],
                    source_port_id=cond["branch"]
                )
            )
            