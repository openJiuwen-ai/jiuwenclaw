# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import ast
import operator
import re
from typing import Any

from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow.components.condition.condition import Condition
from openjiuwen.core.session import BaseSession
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.common.constants.constant import MAX_COLLECTION_SIZE, MAX_EXPRESSION_LENGTH, MAX_AST_DEPTH

RULES = [
    (re.compile(r"&&"), "and"),
    (re.compile(r"\|\|"), "or"),
    (re.compile(r"true"), "True"),
    (re.compile(r"false"), "False"),
    (re.compile(r"length\("), "len("),
    (re.compile(r"not_in"), "not in"),
    (re.compile(r"is_empty\("), "_safe_is_empty("),
    (re.compile(r"is_not_empty\("), "_safe_is_not_empty("),
]


class ExpressionCondition(Condition):
    def __init__(self, expression: str):
        super().__init__()
        self._expression = expression
        pattern = r"\$\{[^}]*\}"
        self._matches = re.findall(pattern, self._expression)
        # Check expression length limit
        if len(expression) > MAX_EXPRESSION_LENGTH:
            raise build_error(
                StatusCode.EXPRESSION_EVAL_ERROR,
                error_msg=f"expression length exceeds maximum allowed length of {MAX_EXPRESSION_LENGTH}"
            )

    def trace_info(self, session: BaseSession = None):
        return {
            "bool_expression": self._expression,
            "inputs": self._get_inputs(session)
        }

    def _get_inputs(self, session: BaseSession) -> dict:
        if len(self._expression) == 0 or session is None:
            return {}
        inputs = {}
        for match in self._matches:
            inputs[match] = session.state().get_global(match[2:-1])
        return inputs

    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        if len(self._expression) == 0:
            return True
        return self._evaluate_expression(self._expression, self._get_inputs(session))

    def __call__(self, session: BaseSession) -> bool:
        if len(self._expression) == 0:
            return True
        return self._evaluate_expression(self._expression, self._get_inputs(session))

    def _evaluate_expression(self, expression, inputs) -> bool:
        processed_expression = convert_condition(expression, inputs)

        var_pattern = r'\$\{([^{}]*)\}'
        var_mapping = {}
        for i, match in enumerate(re.findall(var_pattern, processed_expression)):
            full_match = f'${{{match}}}'
            safe_var_name = f'var_{i}'
            var_mapping[full_match] = safe_var_name

        for full_match, safe_var_name in sorted(var_mapping.items(), key=lambda x: len(x[0]), reverse=True):
            processed_expression = processed_expression.replace(full_match, safe_var_name)

        runtime = {
            "len": len,
            "bool": bool,
            "not": operator.not_,
            "and": operator.and_,
            "or": operator.or_,
            "in": operator.contains,
            "sum": sum,
            "inputs": inputs,
            "_safe_is_empty": _safe_is_empty,
            "_safe_is_not_empty": _safe_is_not_empty,
            "is_empty": _safe_is_empty,
            "is_not_empty": _safe_is_not_empty
        }

        # Add variable values to the runtime environment
        for full_match, safe_var_name in var_mapping.items():
            if full_match in inputs:
                runtime[safe_var_name] = inputs[full_match]

        try:
            tree = ast.parse(processed_expression, mode='eval')

            # Check AST depth before evaluation
            _check_ast_depth(tree)
            result = _evaluate_ast(tree, runtime)

            return result
        except SyntaxError as e:
            raise build_error(
                StatusCode.EXPRESSION_SYNTAX_ERROR,
                error_msg=str(e),
                cause=e
            ) from e
        except NameError as e:
            # Handle undefined variable cases
            raise build_error(
                StatusCode.EXPRESSION_EVAL_ERROR,
                error_msg=str(e),
                cause=e
            ) from e
        except BaseError as e:
            # Re-raise existing BaseError
            raise e
        except Exception as e:
            raise build_error(
                StatusCode.EXPRESSION_EVAL_ERROR,
                error_msg=str(e),
                cause=e
            ) from e


def convert_condition(condition, inputs):
    # Extract and protect string literals to prevent replacement inside strings
    string_literals = []
    placeholder_pattern = r'__STRING_LITERAL_{}__'

    # Pattern to match string literals (single quotes, double quotes, with escape handling)
    # This regex matches: "..." or '...' including escaped quotes
    string_pattern = r'("(?:[^"\\]|\\.)*")|(\'(?:[^\'\\]|\\.)*\')'

    def replace_string(match):
        """Replace matched string literal with placeholder"""
        string_literal = match.group(0)
        index = len(string_literals)
        string_literals.append(string_literal)
        return placeholder_pattern.format(index)

    # Step 1: Extract and replace all string literals with placeholders
    protected_condition = re.sub(string_pattern, replace_string, condition)

    # Step 2: Apply replacement rules to the protected condition
    for pattern, replacement in RULES:
        protected_condition = pattern.sub(replacement, protected_condition)

    # Step 3: Restore original string literals
    for i, original_string in enumerate(string_literals):
        protected_condition = protected_condition.replace(
            placeholder_pattern.format(i),
            original_string
        )

    return protected_condition


def _safe_is_empty(value):
    """Safely check if a value is empty"""
    if value is None:
        return True
    # Check if value is a non-collection type that shouldn't be checked for emptiness
    if isinstance(value, (int, float, bool)):
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"cannot check emptiness of {type(value).__name__} type"
        )

    # Check collection size limit
    if not hasattr(value, "__len__"):
        return False
    length = len(value)
    if length > MAX_COLLECTION_SIZE:
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"collection size exceeds maximum allowed size of {MAX_COLLECTION_SIZE}"
        )
    return length == 0


def _safe_is_not_empty(value):
    """Safely check if a value is not empty"""
    return not _safe_is_empty(value)


def _check_ast_depth(node: ast.AST, current_depth: int = 0) -> int:
    """
    Recursively check the nesting depth of an AST node and raise exception if it exceeds the limit.
    """
    # Check if current depth exceeds the maximum allowed depth
    if current_depth > MAX_AST_DEPTH:
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"expression nesting depth exceeds maximum allowed depth of {MAX_AST_DEPTH}"
        )

    # Base case: if node is a leaf node
    if isinstance(node, (ast.Constant, ast.Name)):
        return current_depth
    # For binary operations, comparisons, etc.
    max_depth = current_depth
    # Check child nodes based on node type
    if isinstance(node, ast.BinOp):
        max_depth = max(max_depth, _check_ast_depth(node.left, current_depth + 1))
        max_depth = max(max_depth, _check_ast_depth(node.right, current_depth + 1))
    elif isinstance(node, ast.UnaryOp):
        max_depth = max(max_depth, _check_ast_depth(node.operand, current_depth + 1))
    elif isinstance(node, ast.Compare):
        max_depth = max(max_depth, _check_ast_depth(node.left, current_depth + 1))
        for comparator in node.comparators:
            max_depth = max(max_depth, _check_ast_depth(comparator, current_depth + 1))
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            max_depth = max(max_depth, _check_ast_depth(value, current_depth + 1))
    elif isinstance(node, ast.Call):
        max_depth = max(max_depth, _check_ast_depth(node.func, current_depth + 1))
        for arg in node.args:
            max_depth = max(max_depth, _check_ast_depth(arg, current_depth + 1))
    elif isinstance(node, ast.Subscript):
        max_depth = max(max_depth, _check_ast_depth(node.value, current_depth + 1))
        if hasattr(node.slice, 'lower') and node.slice.lower:
            max_depth = max(max_depth, _check_ast_depth(node.slice.lower, current_depth + 1))
        if hasattr(node.slice, 'upper') and node.slice.upper:
            max_depth = max(max_depth, _check_ast_depth(node.slice.upper, current_depth + 1))
        if hasattr(node.slice, 'step') and node.slice.step:
            max_depth = max(max_depth, _check_ast_depth(node.slice.step, current_depth + 1))
    elif isinstance(node, ast.Attribute):
        max_depth = max(max_depth, _check_ast_depth(node.value, current_depth + 1))
    elif isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            max_depth = max(max_depth, _check_ast_depth(elt, current_depth + 1))
    elif isinstance(node, ast.Dict):
        for key, value in zip(node.keys or [], node.values or []):
            if key:
                max_depth = max(max_depth, _check_ast_depth(key, current_depth + 1))
            if value:
                max_depth = max(max_depth, _check_ast_depth(value, current_depth + 1))
    elif isinstance(node, ast.Expression):
        max_depth = max(max_depth, _check_ast_depth(node.body, current_depth + 1))
    return max_depth


def _evaluate_ast(node: Any, runtime: dict) -> Any:
    try:
        result = None
        if isinstance(node, ast.BoolOp):
            result = _evaluate_bool_op(node, runtime)
        elif isinstance(node, ast.Compare):
            result = _evaluate_compare(node, runtime)
        elif isinstance(node, ast.Name):
            result = _evaluate_name(node, runtime)
        elif isinstance(node, ast.Constant):
            result = node.value
        elif isinstance(node, ast.Subscript):
            result = _evaluate_subscript(node, runtime)
        elif isinstance(node, ast.Attribute):
            result = _evaluate_attribute(node, runtime)
        elif isinstance(node, ast.Call):
            result = _evaluate_call(node, runtime)
        elif isinstance(node, ast.List):
            result = _evaluate_list(node, runtime)
        elif isinstance(node, ast.Tuple):
            result = _evaluate_tuple(node, runtime)
        elif isinstance(node, ast.Dict):
            result = _evaluate_dict(node, runtime)
        elif isinstance(node, ast.Expression):
            result = _evaluate_ast(node.body, runtime)
        elif isinstance(node, ast.UnaryOp):
            result = _evaluate_unary_op(node, runtime)
        elif isinstance(node, ast.BinOp):
            result = _evaluate_bin_op(node, runtime)
        else:
            raise build_error(
                StatusCode.EXPRESSION_EVAL_ERROR,
                error_msg=f"unsupported AST node type: {type(node).__name__}"
            )
        return result
    except BaseError:
        raise
    except Exception as e:
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"error evaluating AST: {str(e)}",
            cause=e
        ) from e


# Handle unary operators
def _evaluate_unary_op(node: ast.UnaryOp, runtime: dict) -> Any:
    operand = _evaluate_ast(node.operand, runtime)
    if isinstance(node.op, ast.USub):
        return -operand
    elif isinstance(node.op, ast.UAdd):
        return +operand
    elif isinstance(node.op, ast.Not):
        return not operand
    elif isinstance(node.op, ast.Invert):
        return ~operand
    raise build_error(
        StatusCode.EXPRESSION_EVAL_ERROR,
        error_msg=f"unsupported unary operator: {type(node.op).__name__}"
    )


# Handle binary operators
def _evaluate_bin_op(node: ast.BinOp, runtime: dict) -> Any:
    left = _evaluate_ast(node.left, runtime)
    right = _evaluate_ast(node.right, runtime)

    if isinstance(node.op, ast.Add):
        return left + right
    elif isinstance(node.op, ast.Sub):
        return left - right
    elif isinstance(node.op, ast.Mult):
        # Prevent creating excessively large data structures (e.g., [0] * 10000000000)
        if isinstance(left, (list, tuple)) and isinstance(right, int) and right > 0:
            if len(left) * right > MAX_COLLECTION_SIZE:
                raise build_error(
                    StatusCode.EXPRESSION_EVAL_ERROR,
                    error_msg=f"operation would create collection exceeding "
                              f"maximum size of {MAX_COLLECTION_SIZE}"
                )
        elif isinstance(right, (list, tuple)) and isinstance(left, int) and left > 0:
            if len(right) * left > MAX_COLLECTION_SIZE:
                raise build_error(
                    StatusCode.EXPRESSION_EVAL_ERROR,
                    error_msg=f"operation would create collection exceeding "
                              f"maximum size of {MAX_COLLECTION_SIZE}"
                )
        return left * right
    elif isinstance(node.op, ast.Div):
        return left / right
    elif isinstance(node.op, ast.Mod):
        return left % right
    elif isinstance(node.op, ast.Pow):
        # Prevent excessively large exponentiation operations
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if right > 100:  # Limit exponent size
                raise build_error(
                    StatusCode.EXPRESSION_EVAL_ERROR,
                    error_msg="exponent too large in power operation"
                )
        return left ** right
    elif isinstance(node.op, ast.FloorDiv):
        return left // right
    elif isinstance(node.op, ast.LShift):
        return left << right
    elif isinstance(node.op, ast.RShift):
        return left >> right
    elif isinstance(node.op, ast.BitOr):
        return left | right
    elif isinstance(node.op, ast.BitXor):
        return left ^ right
    elif isinstance(node.op, ast.BitAnd):
        return left & right
    raise build_error(
        StatusCode.EXPRESSION_EVAL_ERROR,
        error_msg=f"unsupported binary operator: {type(node.op).__name__}"
    )


def _evaluate_bool_op(node: ast.BoolOp, runtime: dict) -> Any:
    op = node.op
    values = [_evaluate_ast(value, runtime) for value in node.values]
    return all(values) if isinstance(op, ast.And) else any(values)


def _evaluate_compare(node: ast.Compare, runtime: dict) -> Any:
    left = _evaluate_ast(node.left, runtime)
    for operation, right in zip(node.ops, node.comparators):
        right = _evaluate_ast(right, runtime)
        if not _compare_values(left, right, operation):
            return False
    return True


def _compare_values(left: Any, right: Any, op: ast.operator) -> Any:
    if isinstance(op, ast.Eq):
        return left == right
    elif isinstance(op, ast.NotEq):
        return left != right
    elif isinstance(op, ast.Lt):
        return left < right
    elif isinstance(op, ast.LtE):
        return left <= right
    elif isinstance(op, ast.Gt):
        return left > right
    elif isinstance(op, ast.GtE):
        return left >= right
    elif isinstance(op, ast.Is):
        return left is right
    elif isinstance(op, ast.IsNot):
        return left is not right
    elif isinstance(op, ast.In):
        try:
            return left in right
        except TypeError:
            return False
    elif isinstance(op, ast.NotIn):
        try:
            return left not in right
        except TypeError:
            return False
    raise build_error(
        StatusCode.EXPRESSION_EVAL_ERROR,
        error_msg=f"unsupported comparison operator: {type(op).__name__}"
    )


def _evaluate_name(node: ast.Name, runtime: dict) -> Any:
    # First check if it's a direct key in runtime
    if node.id in runtime:
        return runtime[node.id]
    # Special handling: Ensure correct processing when referencing variables via inputs["${var}"]
    # Check if there's an inputs dictionary and node.id is a key in inputs
    if 'inputs' in runtime and node.id in runtime['inputs']:
        return runtime['inputs'][node.id]
    # Try to handle the name as a numeric literal
    try:
        # Check if it's an integer
        return int(node.id)
    except ValueError:
        try:
            # Check if it's a float
            return float(node.id)
        except ValueError as e:
            # Check if it's a boolean keyword
            if node.id == 'True':
                return True
            elif node.id == 'False':
                return False
            elif node.id == 'None':
                return None
            # According to test requirements, raise BaseError instead of NameError
            raise build_error(
                StatusCode.EXPRESSION_EVAL_ERROR,
                error_msg=f"name '{node.id}' is not defined",
                cause=e
            ) from e


def _evaluate_subscript(node: ast.Subscript, runtime: dict) -> Any:
    value = _evaluate_ast(node.value, runtime)

    # Check if the value is a collection and has a safe size
    if hasattr(value, '__len__') and len(value) > MAX_COLLECTION_SIZE:
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"collection size exceeds maximum allowed size of {MAX_COLLECTION_SIZE}"
        )

    if isinstance(node.slice, ast.Slice):
        # Handle slice operations
        lower = _evaluate_ast(node.slice.lower, runtime) if node.slice.lower else None
        upper = _evaluate_ast(node.slice.upper, runtime) if node.slice.upper else None
        step = _evaluate_ast(node.slice.step, runtime) if node.slice.step else None

        # Check for potential large slice operations
        if isinstance(lower, int) and isinstance(upper, int) and (upper - lower) > MAX_COLLECTION_SIZE:
            raise build_error(
                StatusCode.EXPRESSION_EVAL_ERROR,
                error_msg=f"slice operation would create collection exceeding "
                          f"maximum size of {MAX_COLLECTION_SIZE}"
            )

        slice_obj = slice(lower, upper, step)
        return value[slice_obj]
    else:
        # Handle regular index
        index = _evaluate_ast(node.slice, runtime)
        return value[index]


def _evaluate_attribute(node: ast.Attribute, runtime: dict) -> Any:
    value = _evaluate_ast(node.value, runtime)
    # Block access to special attributes (dunder methods/properties)
    if node.attr.startswith('__') and node.attr.endswith('__'):
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"disallowed operation: access to special attribute "
                      f"'{node.attr}' is prohibited"
        )

    # Block access to sensitive attributes even if not full dunder
    sensitive_attributes = ['__class__', '__bases__', '__subclasses__', '__module__', '__dict__']
    if node.attr in sensitive_attributes:
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"disallowed operation: Access to sensitive attribute "
                      f"'{node.attr}' is prohibited"
        )

    try:
        # First try attribute access (for objects)
        return getattr(value, node.attr)
    except AttributeError as e:
        # If attribute access fails and value is a dictionary, try dictionary access
        if isinstance(value, dict) and node.attr in value:
            return value[node.attr]
        # If both fail, raise an error
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"'dict' object has no attribute '{node.attr}'",
            cause=e
        ) from e


def _evaluate_call(node: ast.Call, runtime: dict) -> Any:
    func = _evaluate_ast(node.func, runtime)
    args = [_evaluate_ast(arg, runtime) for arg in node.args]
    if func is None or not callable(func):
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"function {func} is not defined or not callable"
        )
    return func(*args)


def _evaluate_list(node: ast.List, runtime: dict) -> Any:
    # Check if list contains too many elements
    if len(node.elts) > MAX_COLLECTION_SIZE:
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"list contains too many elements, "
                      f"maximum allowed is {MAX_COLLECTION_SIZE}"
        )
    return [_evaluate_ast(item, runtime) for item in node.elts]


def _evaluate_tuple(node: ast.Tuple, runtime: dict) -> Any:
    return tuple(_evaluate_ast(item, runtime) for item in node.elts)


def _evaluate_dict(node: ast.Dict, runtime: dict) -> Any:
    # Check if dictionary has too many key-value pairs
    if len(node.keys) > MAX_COLLECTION_SIZE:
        raise build_error(
            StatusCode.EXPRESSION_EVAL_ERROR,
            error_msg=f"dictionary contains too many key-value pairs, "
                      f"maximum allowed is {MAX_COLLECTION_SIZE}"
        )

    evaluation_dict = {}
    for key, value in zip(node.keys, node.values):
        evaluated_key = _evaluate_ast(key, runtime)
        evaluated_value = _evaluate_ast(value, runtime)
        evaluation_dict[evaluated_key] = evaluated_value
    return evaluation_dict
