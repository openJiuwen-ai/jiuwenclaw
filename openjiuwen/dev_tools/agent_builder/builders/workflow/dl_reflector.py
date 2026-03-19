# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import Dict, Any, List, Set, Tuple, Callable

from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.core.common.logging import LogManager
from openjiuwen.dev_tools.agent_builder.utils.utils import extract_json_from_text

logger = LogManager.get_logger("agent_builder")


def extract_placeholder_content(input_str: str) -> Tuple[bool, List[str]]:
    """Extract placeholder content.

    Args:
        input_str: Input string

    Returns:
        Tuple[bool, List[str]]: (has_placeholder, matches)
            - has_placeholder: Whether contains placeholder
            - matches: List of placeholder content
    """
    pattern = r'\$\{([^}]+)\}'
    matches = re.findall(pattern, input_str)
    has_placeholder = len(matches) > 0
    return has_placeholder, matches


class Reflector:
    """DL reflector for validating DL format correctness.

    Validates node types, parameters, references, etc.

    Example:
        ```python
        reflector = Reflector()
        reflector.check_format(dl_content)
        if reflector.errors:
            print(f"Validation failed: {reflector.errors}")
        ```
    """

    def __init__(self) -> None:
        """Initialize reflector."""
        self.available_node_types: Set[str] = {
            'Start', 'End', 'Output', 'LLM', 'Questioner',
            'Plugin', 'Code', 'Branch', 'IntentDetection'
        }
        self.available_variable_types: Set[str] = {
            'String', 'Integer', 'Number', 'Boolean', 'Object',
            'Array<String>', 'Array<Integer>', 'Array<Number>',
            'Array<Boolean>', 'Array<Object>'
        }
        self.available_condition_operators: Set[str] = {
            "eq", "not_eq", "contain", "not_contain",
            "longer_than", "longer_than_or_eq",
            "short_than", "short_than_or_eq",
            "is_empty", "is_not_empty"
        }
        self.available_node_outputs: Set[str] = set()
        self.node_ids: List[str] = []
        self.node_ids_of_next: Set[str] = set()
        self.errors: List[str] = []

        self.check_functions: Dict[str, Callable[[Dict[str, Any]], None]] = {
            'Start': self._check_start_node,
            'End': self._check_end_node,
            'Output': self._check_output_node,
            'LLM': self._check_llm_node,
            'Questioner': self._check_questioner_node,
            'Plugin': self._check_plugin_node,
            'Code': self._check_code_node,
            'Branch': self._check_branch_node,
            'IntentDetection': self._check_intent_detection_node
        }

    def check_format(self, generated_dl: str) -> None:
        """Check DL format.

        Args:
            generated_dl: DL string (JSON format)
        """
        try:
            json_text = extract_json_from_text(generated_dl)
            generated_dl_dict = JsonUtils.safe_json_loads(json_text)
            if not isinstance(generated_dl_dict, list):
                raise ValueError(
                    f"DL format error: expected JSON array (list), got {type(generated_dl_dict)}"
                )
        except Exception as e:
            self.errors.append(f"JSON format error: {str(e)}")
            logger.warning("DL JSON format error", error=str(e))
            return

        for node_index, node_content in enumerate(generated_dl_dict):
            basic_has_error = self._basic_check(node_content, node_index)
            if basic_has_error:
                continue
            self.check_functions[node_content["type"]](node_content)

        for node_id in self.node_ids_of_next:
            if node_id not in self.node_ids:
                self.errors.append(f"Node ID error: {node_id} does not exist")

    def reset(self) -> None:
        """Reset reflector state."""
        self.available_node_outputs = set()
        self.node_ids = []
        self.node_ids_of_next = set()
        self.errors = []

    def _basic_check(self, node_content: Dict[str, Any], node_index: int) -> bool:
        """Basic check.

        Args:
            node_content: Node content
            node_index: Node index

        Returns:
            True if has error, False otherwise
        """
        if not isinstance(node_content, dict):
            self.errors.append(
                f"Node {node_index + 1} type error: must be dict type!"
            )
            return True

        for key_item in ['id', 'type', 'description', 'parameters']:
            if key_item not in node_content:
                self.errors.append(
                    f"Node {node_index + 1} missing '{key_item}' attribute"
                )
                return True

        if node_content["id"] in self.node_ids:
            self.errors.append(
                f"Node {node_index + 1} ID error: {node_content['id']} already exists"
            )
            return True

        self.node_ids.append(node_content["id"])

        if node_content["type"] not in self.available_node_types:
            self.errors.append(
                f"Node {node_index + 1} type error: "
                f"{node_content['type']} not in available node types"
            )
            return True

        return False

    def _check_start_node(self, node_content: Dict[str, Any]) -> None:
        """Check Start node."""
        self._check_outputs_list(node_content)
        if (
                not self.errors
                and {'name': 'query', 'description': 'user input'}
                not in node_content['parameters']['outputs']
        ):
            self.errors.append(
                "Start node 'parameters.outputs' list must contain "
                "{'name': 'query', 'description': 'user input'}"
            )
        self._check_next_missing(node_content)

    def _check_end_node(self, node_content: Dict[str, Any]) -> None:
        """Check End node."""
        self._check_inputs_list(node_content)
        self._check_configs(node_content, keys=['template'])

    def _check_output_node(self, node_content: Dict[str, Any]) -> None:
        """Check Output node."""
        self._check_inputs_list(node_content)
        self._check_configs(node_content, keys=['template'])
        self._check_next_missing(node_content)

    def _check_llm_node(self, node_content: Dict[str, Any]) -> None:
        """Check LLM node."""
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['system_prompt', 'user_prompt'])
        self._check_next_missing(node_content)

    def _check_questioner_node(self, node_content: Dict[str, Any]) -> None:
        """Check Questioner node."""
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['prompt'])
        self._check_next_missing(node_content)

    def _check_plugin_node(self, node_content: Dict[str, Any]) -> None:
        """Check Plugin node."""
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['tool_id'])
        self._check_next_missing(node_content)

    def _check_code_node(self, node_content: Dict[str, Any]) -> None:
        """Check Code node."""
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['code'])
        self._check_next_missing(node_content)

    def _check_intent_detection_node(
            self,
            node_content: Dict[str, Any]
    ) -> None:
        """Check IntentDetection node."""
        self._check_inputs_list(node_content)
        self._check_configs(node_content, keys=['prompt'])
        self._check_intent_conditions_list(node_content)

    def _check_intent_conditions_list(
            self,
            node_content: Dict[str, Any]
    ) -> None:
        """Check intent detection node conditions list."""
        if 'conditions' not in node_content['parameters']:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters' missing 'conditions' attribute"
            )
            return

        if not isinstance(node_content['parameters']['conditions'], list):
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.conditions' must be list type"
            )
            return

        node_id = node_content['id']
        has_default_branch = False

        for condition in node_content['parameters']['conditions']:
            if not isinstance(condition, dict):
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.conditions' elements must be dict type"
                )
                return

            for key in ['branch', 'description']:
                if key not in condition:
                    self.errors.append(
                        f"{node_content['id']} node, type {node_content['type']}, "
                        f"'parameters.conditions' element missing '{key}' attribute"
                    )

            if 'next' not in condition:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.conditions' element missing 'next' attribute"
                )
            else:
                self.node_ids_of_next.add(condition["next"])

            if 'expression' not in condition:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.conditions' element missing 'expression' attribute"
                )
            else:
                expression = condition['expression']
                if not isinstance(expression, str):
                    self.errors.append(
                        f"{node_content['id']} node, type {node_content['type']}, "
                        "'parameters.conditions' element 'expression' must be string type"
                    )
                    return

                if expression == 'default':
                    has_default_branch = True
                else:
                    left_val = "${" + node_id + ".rawOutput}"
                    if left_val not in expression:
                        self.errors.append(
                            f"{node_content['id']} node, type {node_content['type']}, "
                            "'parameters.conditions' element 'expression' has incorrect variable"
                        )
                    if "contain" not in expression:
                        self.errors.append(
                            f"{node_content['id']} node, type {node_content['type']}, "
                            "'parameters.conditions' element 'expression' must use 'contain'"
                        )

        if not has_default_branch:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.conditions' missing default branch"
            )

    def _check_branch_node(self, node_content: Dict[str, Any]) -> None:
        """Check Branch node."""
        if 'conditions' not in node_content['parameters']:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters' missing 'conditions' attribute"
            )
            return

        if not isinstance(node_content['parameters']['conditions'], list):
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.conditions' must be list type"
            )
            return

        has_default_branch = False

        for condition in node_content['parameters']['conditions']:
            if not isinstance(condition, dict):
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.conditions' elements must be dict type"
                )
                return

            for key in ['branch', 'description']:
                if key not in condition:
                    self.errors.append(
                        f"{node_content['id']} node, type {node_content['type']}, "
                        f"'parameters.conditions' element missing '{key}' attribute"
                    )

            if condition.get('expression') == 'default':
                has_default_branch = True
            else:
                self._check_branch_expression(condition, node_content)

            if 'next' not in condition:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.conditions' element missing 'next' attribute"
                )
            else:
                self.node_ids_of_next.add(condition["next"])

        if not has_default_branch:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.conditions' missing default branch"
            )

    def _check_branch_expression(
            self,
            condition_branch: Dict[str, Any],
            node_content: Dict[str, Any]
    ) -> None:
        """Check branch expression."""
        if 'expression' in condition_branch:
            expression = condition_branch['expression']
            if not isinstance(expression, str):
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.conditions' element 'expression' must be string type"
                )
            else:
                self._check_branch_operator(expression, node_content)
                _, placeholder_content = extract_placeholder_content(expression)
                for content in placeholder_content:
                    if content not in self.available_node_outputs:
                        self.errors.append(
                            f"{node_content['id']} node, type {node_content['type']}, "
                            "'parameters.conditions' element 'expression' references non-existent variable"
                        )
        elif 'expressions' in condition_branch:
            expressions = condition_branch['expressions']
            if not isinstance(expressions, list):
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.conditions' element 'expressions' must be list type"
                )
            else:
                for expression in expressions:
                    self._check_branch_operator(expression, node_content)
                    _, placeholder_content = extract_placeholder_content(expression)
                    for content in placeholder_content:
                        if content not in self.available_node_outputs:
                            self.errors.append(
                                f"{node_content['id']} node, type {node_content['type']}, "
                                "'parameters.conditions' element 'expression' references non-existent variable"
                            )
        else:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.conditions' element missing 'expression' or 'expressions' attribute"
            )

    def _check_branch_operator(
            self,
            expression: str,
            node_content: Dict[str, Any]
    ) -> None:
        """Check branch operator."""
        expression_list = expression.strip().split(" ")
        if expression_list[1] not in self.available_condition_operators:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.conditions' element 'expression' uses unsupported operator"
            )

    def _check_inputs_list(
            self,
            node_content: Dict[str, Any],
            check_type: bool = False
    ) -> None:
        """Check inputs list."""
        if 'inputs' not in node_content['parameters']:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters' missing 'inputs' attribute"
            )
            return

        if not isinstance(node_content['parameters']['inputs'], list):
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.inputs' must be list type"
            )
            return

        input_names: Set[str] = set()

        for input_item in node_content['parameters']['inputs']:
            if not isinstance(input_item, dict):
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.inputs' elements must be dict type"
                )
                return

            if 'name' not in input_item:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.inputs' element missing 'name' attribute"
                )
            else:
                if input_item['name'] in input_names:
                    self.errors.append(
                        f"{node_content['id']} node, type {node_content['type']}, "
                        "'parameters.inputs' element 'name' must be unique"
                    )
                input_names.add(input_item['name'])

            if 'value' not in input_item:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.inputs' element missing 'value' attribute"
                )
            else:
                value = input_item['value']
                has_placeholder, placeholder_content = extract_placeholder_content(
                    value
                )
                if has_placeholder:
                    if len(placeholder_content) > 1:
                        self.errors.append(
                            f"{node_content['id']} node, type {node_content['type']}, "
                            "'parameters.inputs' element 'value' has multiple reference variables"
                        )
                    elif (
                            len(placeholder_content) == 1
                            and placeholder_content[0] not in self.available_node_outputs
                    ):
                        self.errors.append(
                            f"{node_content['id']} node, type {node_content['type']}, "
                            "'parameters.inputs' element 'value' references non-existent variable"
                        )

            if check_type:
                if 'type' not in input_item:
                    self.errors.append(
                        f"{node_content['id']} node, type {node_content['type']}, "
                        "'parameters.inputs' element missing 'type' attribute"
                    )
                else:
                    if input_item['type'] not in self.available_variable_types:
                        self.errors.append(
                            f"{node_content['id']} node, type {node_content['type']}, "
                            f"'parameters.inputs' element 'type' must be one of {self.available_variable_types}"
                        )

    def _check_outputs_list(
            self,
            node_content: Dict[str, Any],
            check_type: bool = False
    ) -> None:
        """Check outputs list."""
        if 'outputs' not in node_content['parameters']:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters' missing 'outputs' attribute"
            )
            return

        if not isinstance(node_content['parameters']['outputs'], list):
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.outputs' must be list type"
            )
            return

        output_names: Set[str] = set()

        for output_item in node_content['parameters']['outputs']:
            if not isinstance(output_item, dict):
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.outputs' elements must be dict type"
                )
                return

            if 'name' not in output_item:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.outputs' element missing 'name' attribute"
                )
            else:
                if output_item['name'] in output_names:
                    self.errors.append(
                        f"{node_content['id']} node, type {node_content['type']}, "
                        "'parameters.outputs' element 'name' must be unique"
                    )
                output_names.add(output_item['name'])
                self.available_node_outputs.add(
                    f"{node_content['id']}.{output_item['name']}"
                )

            if 'description' not in output_item:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    "'parameters.outputs' element missing 'description' attribute"
                )

            if check_type:
                if 'type' not in output_item:
                    self.errors.append(
                        f"{node_content['id']} node, type {node_content['type']}, "
                        "'parameters.outputs' element missing 'type' attribute"
                    )
                else:
                    if output_item['type'] not in self.available_variable_types:
                        self.errors.append(
                            f"{node_content['id']} node, type {node_content['type']}, "
                            f"'parameters.outputs' element 'type' must be one of {self.available_variable_types}"
                        )

    def _check_configs(
            self,
            node_content: Dict[str, Any],
            keys: List[str]
    ) -> None:
        """Check configs."""
        if 'configs' not in node_content['parameters']:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters' missing 'configs' attribute"
            )
            return

        if not isinstance(node_content['parameters']['configs'], dict):
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "'parameters.configs' must be dict type"
            )
            return

        configs_keys = node_content['parameters']['configs'].keys()
        for key in keys:
            if key not in configs_keys:
                self.errors.append(
                    f"{node_content['id']} node, type {node_content['type']}, "
                    f"'parameters.configs' missing '{key}' attribute"
                )

    def _check_next_missing(self, node_content: Dict[str, Any]) -> None:
        """Check next attribute."""
        if 'next' not in node_content:
            self.errors.append(
                f"{node_content['id']} node, type {node_content['type']}, "
                "missing 'next' attribute"
            )
        else:
            self.node_ids_of_next.add(node_content['next'])
