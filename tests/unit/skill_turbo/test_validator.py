# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PlanCodeValidator 单元测试。"""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.skill_turbo.validator import (
    PlanCodeValidationError,
    PlanCodeValidator,
)

SKILL_CODE_PREFIXES = ["skill_codes."]
SKILL_TURBO_SKILL_CODE_PREFIXES = ["jiuwenclaw.agentserver.skill_turbo.skill_codes."]


@pytest.fixture
def validator() -> PlanCodeValidator:
    return PlanCodeValidator(allowed_import_prefixes=SKILL_CODE_PREFIXES)


class TestPlanCodeValidatorRules:
    """设计文档 §4.4 规则矩阵。"""

    @pytest.mark.unit
    def test_tv01_reject_plain_import(self, validator: PlanCodeValidator) -> None:
        errors = validator.validate("import os\n")
        assert len(errors) == 1
        assert "禁止 import 语句" in errors[0]

    @pytest.mark.unit
    def test_tv02_allow_skill_code_import(self, validator: PlanCodeValidator) -> None:
        code = "from skill_codes.ppt.plan_code import root\n"
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_tv03_reject_os_path_import(self, validator: PlanCodeValidator) -> None:
        code = "from os.path import join\n"
        errors = validator.validate(code)
        assert len(errors) == 1
        assert "禁止 import: os.path" in errors[0]

    @pytest.mark.unit
    def test_tv04_reject_dunder_attribute(self, validator: PlanCodeValidator) -> None:
        code = "x = obj.__class__\n"
        errors = validator.validate(code)
        assert len(errors) == 1
        assert "禁止访问 dunder 属性: __class__" in errors[0]

    @pytest.mark.unit
    def test_tv05_reject_relative_import(self, validator: PlanCodeValidator) -> None:
        code = "from .plan_code import root\n"
        errors = validator.validate(code)
        assert len(errors) == 1
        assert "禁止相对 import" in errors[0]

    @pytest.mark.unit
    def test_tv06_allow_valid_plan_node_definition(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
class EchoNode(PlanNode):
    async def _execute(self, inputs):
        return inputs.get("task", "")

root = EchoNode(
    plan_name="echo",
    instruction="回显用户任务",
    sub_plans=[],
)
"""
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_tv07_syntax_error(self, validator: PlanCodeValidator) -> None:
        errors = validator.validate("class (\n")
        assert len(errors) == 1
        assert errors[0].startswith("语法错误:")


class TestPlanCodeValidatorEdgeCases:
    @pytest.mark.unit
    def test_empty_code_passes(self, validator: PlanCodeValidator) -> None:
        assert validator.validate("") == []

    @pytest.mark.unit
    def test_allow_skill_codes_subpackage_import(
        self, validator: PlanCodeValidator
    ) -> None:
        code = "from skill_codes.ppt import plan_code\n"
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_reject_import_skill_codes_without_submodule(
        self, validator: PlanCodeValidator
    ) -> None:
        errors = validator.validate("import skill_codes\n")
        assert len(errors) == 1
        assert "禁止 import 语句" in errors[0]

    @pytest.mark.unit
    def test_reject_similar_but_wrong_prefix(
        self, validator: PlanCodeValidator
    ) -> None:
        code = "from skill_codes_evil.ppt.plan_code import root\n"
        errors = validator.validate(code)
        assert len(errors) == 1
        assert "禁止 import:" in errors[0]

    @pytest.mark.unit
    def test_allow_normal_attribute_access(
        self, validator: PlanCodeValidator
    ) -> None:
        code = "value = node.plan_name\n"
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_reject_multiple_violations(self, validator: PlanCodeValidator) -> None:
        code = """
import os
from os.path import join
x = obj.__dict__
"""
        errors = validator.validate(code)
        assert len(errors) == 3

    @pytest.mark.unit
    def test_no_allowed_prefixes_rejects_all_import_from(self) -> None:
        validator = PlanCodeValidator(allowed_import_prefixes=[])
        code = "from skill_codes.ppt.plan_code import root\n"
        errors = validator.validate(code)
        assert len(errors) == 1
        assert "禁止 import:" in errors[0]

    @pytest.mark.unit
    def test_validate_or_raise_success(self, validator: PlanCodeValidator) -> None:
        validator.validate_or_raise("x = 1\n")

    @pytest.mark.unit
    def test_validate_or_raise_failure(self, validator: PlanCodeValidator) -> None:
        with pytest.raises(PlanCodeValidationError) as exc_info:
            validator.validate_or_raise("import os\n")
        assert exc_info.value.errors
        assert "禁止 import 语句" in exc_info.value.errors[0]


class TestGeneratedSkillCodeValidator:
    @pytest.fixture
    def validator(self) -> PlanCodeValidator:
        return PlanCodeValidator.for_generated_skill_code()

    @pytest.mark.unit
    def test_reject_plain_import(self, validator: PlanCodeValidator) -> None:
        assert "禁止 import 语句" in validator.validate("import json\n")[0]

    @pytest.mark.unit
    def test_allow_plan_node_import(self, validator: PlanCodeValidator) -> None:
        code = "from jiuwenclaw.agentserver.skill_turbo.plan_node import PlanNode\n"
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_reject_other_imports(self, validator: PlanCodeValidator) -> None:
        errors = validator.validate("from typing import Any\n")
        assert len(errors) == 1
        assert "动态生成 skill_code 仅允许 import PlanNode" in errors[0]

    @pytest.mark.unit
    def test_reject_dangerous_calls(self, validator: PlanCodeValidator) -> None:
        code = """
value = open("x")
y = getattr(obj, "name")
z = Path("x").read_text()
"""
        errors = validator.validate(code)
        assert len(errors) == 3
        assert any("禁止调用危险函数: open" in e for e in errors)
        assert any("禁止调用危险函数: getattr" in e for e in errors)
        assert any("禁止调用危险属性: read_text" in e for e in errors)

    @pytest.mark.unit
    def test_reject_state_and_exception_syntax(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
global x
with resource:
    pass
try:
    pass
except Exception:
    pass
"""
        errors = validator.validate(code)
        assert len(errors) == 3
        assert any("禁止 global 语句" in e for e in errors)
        assert any("禁止 with 语句" in e for e in errors)
        assert any("禁止 try/except 语句" in e for e in errors)

    @pytest.mark.unit
    def test_reject_process_control_calls(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
breakpoint()
exit()
quit()
"""
        errors = validator.validate(code)
        assert len(errors) == 3
        assert any("禁止调用危险函数: breakpoint" in e for e in errors)
        assert any("禁止调用危险函数: exit" in e for e in errors)
        assert any("禁止调用危险函数: quit" in e for e in errors)

    @pytest.mark.unit
    def test_reject_memoryview(self, validator: PlanCodeValidator) -> None:
        errors = validator.validate("m = memoryview(b'x')\n")
        assert len(errors) == 1
        assert "禁止调用危险函数: memoryview" in errors[0]

    @pytest.mark.unit
    def test_reject_type_call_entirely(self, validator: PlanCodeValidator) -> None:
        code = 'Evil = type("Evil", (object,), {"x": 1})\n'
        errors = validator.validate(code)
        assert len(errors) >= 1
        assert any("禁止调用危险函数: type" in e for e in errors)

    @pytest.mark.unit
    def test_reject_del_attribute_and_subscript(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
del obj.attr
del items[key]
"""
        errors = validator.validate(code)
        assert len(errors) == 2
        assert any("禁止删除对象属性" in e for e in errors)
        assert any("禁止删除下标项" in e for e in errors)

    @pytest.mark.unit
    def test_allow_simple_generated_node(self, validator: PlanCodeValidator) -> None:
        code = """
from jiuwenclaw.agentserver.skill_turbo.plan_node import PlanNode


class SearchNode(PlanNode):
    async def _execute(self, inputs):
        result = await use_tool("web_search", {"query": inputs.get("task", "")})
        return result


root = SearchNode(plan_name="search", instruction="搜索", sub_plans=[])
"""
        assert validator.validate(code) == []


class TestBuiltinSkillCodeValidator:
    @pytest.fixture
    def validator(self) -> PlanCodeValidator:
        return PlanCodeValidator.for_builtin_skill_code(SKILL_TURBO_SKILL_CODE_PREFIXES)

    @pytest.mark.unit
    def test_allow_safe_stdlib_and_internal_imports(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
from __future__ import annotations
import json
import re
from typing import Any
from collections.abc import AsyncIterator
from pathlib import Path
from jiuwenclaw.agentserver.skill_turbo.plan_node import PlanNode
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common import PptCommon
"""
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_reject_dangerous_imports(self, validator: PlanCodeValidator) -> None:
        code = """
import os
import subprocess
from urllib.parse import urlparse
"""
        errors = validator.validate(code)
        assert len(errors) == 3
        assert any("禁止 import: os" in e for e in errors)
        assert any("禁止 import: subprocess" in e for e in errors)
        assert any("禁止 import: urllib.parse" in e for e in errors)

    @pytest.mark.unit
    def test_reject_dangerous_builtin_calls(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
value = eval("1 + 1")
name = globals()
"""
        errors = validator.validate(code)
        assert len(errors) == 2
        assert any("禁止调用危险函数: eval" in e for e in errors)
        assert any("禁止调用危险函数: globals" in e for e in errors)

    @pytest.mark.unit
    def test_reject_getattr_setattr_delattr(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
getattr(obj, "__class__")
setattr(obj, "x", 1)
delattr(obj, "x")
"""
        errors = validator.validate(code)
        assert len(errors) == 3
        assert any("禁止调用危险函数: getattr" in e for e in errors)
        assert any("禁止调用危险函数: setattr" in e for e in errors)
        assert any("禁止调用危险函数: delattr" in e for e in errors)

    @pytest.mark.unit
    def test_reject_process_control_calls(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
breakpoint()
exit()
quit()
"""
        errors = validator.validate(code)
        assert len(errors) == 3
        assert any("禁止调用危险函数: breakpoint" in e for e in errors)
        assert any("禁止调用危险函数: exit" in e for e in errors)
        assert any("禁止调用危险函数: quit" in e for e in errors)

    @pytest.mark.unit
    def test_reject_memoryview(self, validator: PlanCodeValidator) -> None:
        errors = validator.validate("m = memoryview(b'x')\n")
        assert len(errors) == 1
        assert "禁止调用危险函数: memoryview" in errors[0]

    @pytest.mark.unit
    def test_reject_path_io_even_for_builtin_skill_code(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
from pathlib import Path
path = Path("x")
text = path.read_text(encoding="utf-8")
path.write_text(text, encoding="utf-8")
path.parent.mkdir(parents=True, exist_ok=True)
"""
        errors = validator.validate(code)
        assert len(errors) == 3
        assert any("禁止调用危险属性: read_text" in e for e in errors)
        assert any("禁止调用危险属性: write_text" in e for e in errors)
        assert any("禁止调用危险属性: mkdir" in e for e in errors)

    @pytest.mark.unit
    def test_reject_del_attribute(self, validator: PlanCodeValidator) -> None:
        errors = validator.validate("del obj.attr\n")
        assert len(errors) == 1
        assert "禁止删除对象属性" in errors[0]

    @pytest.mark.unit
    def test_reject_type_three_args(self, validator: PlanCodeValidator) -> None:
        code = 'Evil = type("Evil", (object,), {"x": 1})\n'
        errors = validator.validate(code)
        assert len(errors) == 1
        assert "禁止动态类型创建" in errors[0]

    @pytest.mark.unit
    def test_allow_type_single_arg(self, validator: PlanCodeValidator) -> None:
        code = "t = type(obj)\n"
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_allow_limited_dunder_used_by_builtin_nodes(
        self, validator: PlanCodeValidator
    ) -> None:
        code = """
super().__init__(plan_name="x", instruction="x", sub_plans=[])
logger.info("node=%s", type(self).__name__)
"""
        assert validator.validate(code) == []

    @pytest.mark.unit
    def test_reject_dangerous_dunder_attribute(
        self, validator: PlanCodeValidator
    ) -> None:
        errors = validator.validate("value = obj.__class__\n")
        assert len(errors) == 1
        assert "禁止访问 dunder 属性: __class__" in errors[0]
