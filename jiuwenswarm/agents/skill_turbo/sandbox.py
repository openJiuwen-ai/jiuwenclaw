# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""统一运行时 import 拦截器：复用 AST 校验器的 CodeValidationPolicy 策略。

将 PlanCodeValidator 的静态 import 校验逻辑（``is_import_allowed``）桥接到运行时
``__import__`` 替代函数，使 ``exec_module`` 加载的 turbo code 模块在运行时也受到与
AST 校验完全一致的 import 约束（exact 白名单 + prefix + 黑名单）。

**信任边界说明**：
- turbo code（外部 skill 的 ``turbo/`` 包）是受控代码，经 review/CI 保证安全，非 LLM 生成代码。
- 沙箱机制用于纵深防御：防御编码失误或依赖链污染，而非防御恶意攻击者。
- AST 校验 + 运行时拦截两层防御：
  - AST 层：拦截 ``exec``/``eval``/``open``/``getattr`` 等危险调用 + 非白名单 import
  - 运行时层：
    1. 拦截 ``__import__`` 绕过（如动态拼接模块名）
    2. 将 ``policy.denied_call_names`` 中的危险 builtins 替换为拒绝桩
       （即使 AST 被绕过或遗漏，运行时仍会失败）
- 相对导入放行：turbo code 包内导入（如 ``from .ppt_common import ...``）是内部受控
  代码间的依赖，inherently safe，不受白名单限制。

设计要点：
- ``create_safe_import(validator)``: 创建 ``__import__`` 替代函数
  · 绝对导入：用 ``validator.is_import_allowed`` 判定（与 AST 校验一致）
  · 相对导入：按 ``policy.deny_relative_import`` 决定是否放行
    （``builtin_skill_code`` 策略 ``deny_relative_import=False``，包内导入放行）
  · fromlist：拦截 ``__import__``/``__builtins__``/``exec``/``eval`` 等危险名
- ``inject_import_sandbox(module, validator)``: 注入沙箱 ``__builtins__`` 到模块
  保留合法标准 builtins（``super``/``property``/``staticmethod``/``classmethod``/``hasattr`` 等），
  替换 ``__import__`` 为安全版本，并把 ``denied_call_names`` 对应项换成拒绝桩

与批量执行器 ``executor.py`` ``_safe_import`` 的区别：
- 批量 ``_safe_import`` 用 ``plan_code`` 策略（只检查 prefix），适用于 ``exec(plan_code, namespace)``
- 本模块用 ``validator.is_import_allowed``（exact + prefix + 黑名单），适用于 ``exec_module``
- 批量 ``_safe_import`` 禁止相对导入（plan_code 无包上下文）
- 本模块按 ``policy.deny_relative_import`` 决定（turbo_codes 包内导入放行）
"""

from __future__ import annotations

import builtins as _builtins
import importlib
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jiuwenswarm.agents.skill_turbo.validator import PlanCodeValidator

__all__ = ["create_safe_import", "inject_import_sandbox"]


# fromlist 中禁止导入的名称：这些名称可能被用于获取危险内建对象
_DENIED_FROMLIST_NAMES: frozenset[str] = frozenset({
    "__import__", "__builtins__", "__build_class__",
    "exec", "eval", "compile", "open", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr", "type",
})

# 缓存原始 __import__（用于相对导入委托）
_ORIGINAL_IMPORT = _builtins.__import__


def _make_denied_builtin(name: str) -> Any:
    """构造运行时拒绝桩：调用即抛，防止 AST 漏检后仍能执行危险 builtin。"""

    def _denied(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(
            f"SkillTurbo sandbox denied builtin at runtime: {name}"
        )

    _denied.__name__ = name
    _denied.__qualname__ = name
    return _denied


def create_safe_import(validator: "PlanCodeValidator") -> Any:
    """创建运行时 ``__import__`` 替代函数.

    使用 ``validator.is_import_allowed`` 判定逻辑，与静态 AST 校验完全一致。
    复刻 ``__import__`` 语义：fromlist 非空时返回 name 指定的模块；
    fromlist 为空时返回顶层包（即 name 的第一段）。
    """
    policy = validator.policy

    def _safe_import(
        name: str,
        globals_: dict[str, Any] | None = None,
        locals_: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] | list[str] | None = None,
        level: int = 0,
    ) -> Any:
        # 规范化 fromlist：Python 编译 `import x` 时传 None，`from x import y` 传 tuple
        # 统一转为 tuple 以便迭代判定
        fromlist_tuple: tuple[str, ...] = tuple(fromlist) if fromlist else ()

        if level:
            # 相对导入：按策略决定是否放行
            if policy.deny_relative_import:
                raise ImportError(
                    f"SkillTurbo 禁止相对 import (runtime sandbox): level={level}"
                )
            # 相对导入是包内导入，使用标准 __import__ 处理
            return _ORIGINAL_IMPORT(name, globals_, locals_, fromlist_tuple, level)

        # 绝对导入：用 AST 校验器的策略判定（exact 白名单 + prefix + 黑名单）
        if not validator.is_import_allowed(name):
            raise ImportError(f"SkillTurbo 禁止 import (runtime sandbox): {name}")

        # 校验 fromlist：防止 from xxx import __import__ 等绕过
        for item_name in fromlist_tuple:
            if item_name in _DENIED_FROMLIST_NAMES:
                raise ImportError(
                    f"SkillTurbo 禁止从 {name} 导入: {item_name}"
                )

        # 使用 importlib.import_module（Python import 系统自动处理 BOM）
        module = importlib.import_module(name)
        if fromlist_tuple:
            # 确保子模块属性可访问（from x.y import a 语义）
            for item_name in fromlist_tuple:
                if not hasattr(module, item_name):
                    try:
                        importlib.import_module(f"{name}.{item_name}")
                    except ImportError:
                        # 非子模块（普通属性）时忽略，与 __import__ 行为一致
                        pass
            return module
        # fromlist 为空：import x.y 返回顶层包 x
        top = name.split(".")[0]
        return sys.modules[top] if "." in name else module

    return _safe_import


def inject_import_sandbox(module: Any, validator: "PlanCodeValidator") -> None:
    """注入沙箱 ``__builtins__`` 到模块.

    保留合法标准 builtins（``super``/``property``/``staticmethod``/``classmethod``/``hasattr`` 等），
    替换 ``__import__`` 为安全版本，并把 ``policy.denied_call_names`` 中已存在的 builtins
    换成拒绝桩。用于 ``exec_module`` 前注入 ``module.__dict__``。

    设计理由：turbo code 大量使用 ``super()``/``@classmethod``/``@staticmethod``/``hasattr()``，
    若用受限白名单（如批量执行器的 ``_SAFE_BUILTINS``）会破坏这些合法用法。
    保留合法 builtins + 按策略拒绝危险项是纵深防御：
    - AST 校验器拦截直接危险调用（含 ``__builtins__['exec']`` 下标形式）
    - 运行时拒绝桩拦截 AST 遗漏/动态绕过后的危险 builtin 调用
    - 运行时 ``__import__`` 替代函数拦截危险 import
    """
    safe_builtins = dict(vars(_builtins))
    safe_builtins["__import__"] = create_safe_import(validator)
    # 与 AST denied_call_names 对齐：运行时再挡一层（不含 __import__，已单独替换）
    for name in validator.policy.denied_call_names:
        if name == "__import__":
            continue
        if name in safe_builtins:
            safe_builtins[name] = _make_denied_builtin(name)
    module.__dict__["__builtins__"] = safe_builtins
