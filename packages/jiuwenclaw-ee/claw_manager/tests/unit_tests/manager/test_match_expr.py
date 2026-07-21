# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""match_expr 写入校验单元测试。"""

from __future__ import annotations

import pytest

from jiuwenclaw_manager.infrastructure.match_expr import validate_match_expr

pytestmark = pytest.mark.unit


def test_validate_match_expr_accepts_supported_forms() -> None:
    validate_match_expr(None)
    validate_match_expr("")
    validate_match_expr([])
    validate_match_expr("[]")
    validate_match_expr("always")
    validate_match_expr("group_id == 'g_demo_sales'")
    validate_match_expr("user_id == 'alice' and group_id == 'g_demo_sales'")
    validate_match_expr(["group_id == 'g_demo_sales'", "group_id == 'g_vip'"])


@pytest.mark.parametrize(
    ("expr", "needle"),
    [
        ("group_id === 'g'", "=== / !=="),
        ("group_id > 'g'", "ordering operators"),
        ("service_id == 'x'", "unknown name"),
        ("${user::carol}", r"\$\{\.\.\.\}"),
        ("len(user_id) == 1", "function calls"),
        ("[", "JSON array"),
    ],
)
def test_validate_match_expr_rejects_invalid(expr: str, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        validate_match_expr(expr)
