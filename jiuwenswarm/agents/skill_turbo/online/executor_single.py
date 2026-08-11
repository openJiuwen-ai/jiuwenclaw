# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""隔离加载单个 PlanNode + runtime callback 注入 + mtime 缓存.

设计要点（§7）：
- 整包 AST 校验：pkg_dir 下所有 .py 逐一校验（第一层：静态防御）
- 运行时 import 沙箱：exec_module 前注入 __import__ 替代（第二层：运行时防御）
  复用 AST 校验器的 CodeValidationPolicy 策略，与静态校验判定一致
- 隔离加载：spec_from_file_location 显式包，不进 sys.path
- 进程级缓存：key=(turbo_dir, scenario, max_mtime)，mtime 变化自动失效
- deepcopy：每次返回独立副本，避免共享可变状态
"""

from __future__ import annotations

import copy
import importlib.util
import sys
import threading
import types
from pathlib import Path
from typing import Any

from jiuwenswarm.agents.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenswarm.agents.skill_turbo.sandbox import inject_import_sandbox
from jiuwenswarm.agents.skill_turbo.validator import PlanCodeValidator
from jiuwenswarm.common.utils import logger

__all__ = ["SkillCodeExecutor", "load_node"]


# ─────────────────────────────────────────────────────────────────────────────
# 进程级缓存（纯函数式，key→value，无副作用、无跨组件握手）：
#   _NODE_MODULE_CACHE: per-file 已 exec 的 module，key=(resolved_file, file_mtime)
#   _VALIDATED_PACKAGES: 已整包 AST 校验的包，key=(pkg_dir, scenario, max_mtime)
# ─────────────────────────────────────────────────────────────────────────────

_NODE_MODULE_CACHE: dict[tuple[str, float], Any] = {}
_VALIDATED_PACKAGES: set[tuple[str, str, float]] = set()
# 保护缓存 read-then-write 组合（同步操作，不跨 await，不阻塞事件循环）。
_CACHE_LOCK = threading.Lock()
# 容量上限
_NODE_MODULE_CACHE_MAX = 128

_TURBO_IMPORT_PREFIXES: list[str] = [
    "jiuwenswarm.agents.skill_turbo.skill_codes",
]


def _evict_if_needed() -> None:
    """超限时清空缓存（旧 mtime 条目是垃圾，clear 语义直白且不损失热数据）.

    必须在 _CACHE_LOCK 内调用。
    """
    if len(_NODE_MODULE_CACHE) > _NODE_MODULE_CACHE_MAX:
        _NODE_MODULE_CACHE.clear()
    if len(_VALIDATED_PACKAGES) > _NODE_MODULE_CACHE_MAX:
        _VALIDATED_PACKAGES.clear()


def _cleanup_package_modules(pkg_name: str) -> None:
    """从 sys.modules 清理 pkg_name 及其所有子模块。

    turbo_dir 变更或 mtime 失效时调用，避免残留旧版本模块导致相对导入拿到过期代码。
    """
    prefix = pkg_name + "."
    to_remove = [
        name for name in sys.modules
        if name == pkg_name or name.startswith(prefix)
    ]
    for name in to_remove:
        del sys.modules[name]


def _ensure_parent_package(scenario: str, pkg_dir: Path) -> str:
    """确保父包 turbo_codes_{scenario} 在 sys.modules 中注册。

    turbo_codes 包内使用相对导入（``from .ppt_common import ...``），
    Python 导入机制需要父包在 sys.modules 中且 ``__path__`` 指向 pkg_dir，
    否则相对导入会抛 ``ImportError: attempted relative import with no known parent package``。

    若 turbo_dir 变更（``__path__`` 不一致），清理旧子模块并重新注册。

    同时把框架契约符号（PlanNode / AbortError）注入到包命名空间，使包内
    子模块可通过 ``from . import PlanNode, AbortError`` 获取这些符号，
    无需依赖外部包路径。

    Returns:
        包全名（如 ``"turbo_codes_create_ppt"``）
    """
    pkg_name = f"turbo_codes_{scenario}"
    pkg_dir_str = str(pkg_dir)

    existing = sys.modules.get(pkg_name)
    if existing is not None:
        existing_paths = list(getattr(existing, "__path__", []) or [])
        if pkg_dir_str in existing_paths:
            existing.__dict__["PlanNode"] = PlanNode
            existing.__dict__["AbortError"] = AbortError
            return pkg_name
        _cleanup_package_modules(pkg_name)

    # 创建并注册父包
    pkg_module = types.ModuleType(pkg_name)
    pkg_module.__path__ = [pkg_dir_str]
    pkg_module.__package__ = pkg_name
    # 注入框架契约符号到包命名空间：子模块通过 `from . import PlanNode` 获取
    pkg_module.__dict__["PlanNode"] = PlanNode
    pkg_module.__dict__["AbortError"] = AbortError
    sys.modules[pkg_name] = pkg_module
    return pkg_name


def _build_validator() -> PlanCodeValidator:
    """构建 turbo_codes 专用校验器（复用 builtin_skill_code 策略）."""
    return PlanCodeValidator.for_builtin_skill_code(_TURBO_IMPORT_PREFIXES)


def _validate_source_file(py_path: Path, validator: PlanCodeValidator) -> None:
    """AST 校验单个 .py 文件.

    使用 utf-8-sig 编码读取，自动剥离 UTF-8 BOM (U+FEFF)。
    若用 utf-8 读取，BOM 会残留在字符串首部，导致 ast.parse()
    抛出 SyntaxError: invalid non-printable character U+FEFF。
    """
    source = py_path.read_text(encoding="utf-8-sig")
    errors = validator.validate(source)
    if errors:
        raise ValueError(
            f"turbo_codes AST 校验失败 {py_path.name}: {errors}"
        )


def _resolve_plan_file(
    plan_name: str,
    schema: dict[str, Any],
    pkg_dir: Path,
) -> Path:
    """通过 schema.code_plan_names 定位 plan_name 所在文件（产物规范 §4.7）.

    Args:
        plan_name: 节点名
        schema: schema dict（含 code_plan_names）
        pkg_dir: turbo_codes_<scenario> 目录

    Returns:
        对应 .py 文件路径

    Raises:
        KeyError: plan_name 不在 code_plan_names 中
    """
    for cpn in schema.get("code_plan_names", []):
        if not isinstance(cpn, dict):
            continue
        if plan_name in cpn.get("plan_names", []):
            file_name = cpn.get("file", "")
            if file_name:
                return pkg_dir / file_name
    raise KeyError(
        f"plan_name {plan_name!r} 不在 schema code_plan_names 中，"
        f"无法定位所属文件"
    )


def load_node(
    turbo_dir: str,
    scenario: str,
    plan_name: str,
    schema: dict[str, Any],
) -> PlanNode:
    """隔离加载并返回 plan_name 对应 PlanNode 的 deepcopy.

    Args:
        turbo_dir: turbo/ 目录路径
        scenario: 切面名
        plan_name: 节点名
        schema: schema dict（用于定位 code_plan_names）

    Returns:
        PlanNode 实例的 deepcopy

    Raises:
        FileNotFoundError: turbo_codes 目录不存在
        AttributeError: plan_name 在模块中未导出
        TypeError: 导出的实例不是 PlanNode
        ValueError: AST 校验失败
    """
    logger.info(
        "[executor_single] load_node start: turbo_dir=%s scenario=%s plan_name=%s",
        turbo_dir, scenario, plan_name,
    )
    pkg_dir = Path(turbo_dir) / f"turbo_codes_{scenario}"
    if not pkg_dir.is_dir():
        raise FileNotFoundError(f"turbo codes not found: {pkg_dir}")

    py_files = sorted(pkg_dir.rglob("*.py"))
    if not py_files:
        raise FileNotFoundError(f"no .py files in {pkg_dir}")

    # 0. 整包 AST 校验（带缓存，key=pkg_dir+scenario+max_mtime，mtime 变化自动失效）
    max_mtime = max(p.stat().st_mtime for p in py_files)
    pkg_validate_key = (str(pkg_dir.resolve()), scenario, max_mtime)
    validator = _build_validator()
    if pkg_validate_key not in _VALIDATED_PACKAGES:
        for py in py_files:
            _validate_source_file(py, validator)
        with _CACHE_LOCK:
            _VALIDATED_PACKAGES.add(pkg_validate_key)
            _evict_if_needed()
        logger.debug(
            "[executor_single] validated turbo_codes_%s (mtime=%s, %d files)",
            scenario, max_mtime, len(py_files),
        )

    # 1. 通过 code_plan_names 定位 plan_name 所在文件（产物规范 §4.7）
    target_file = _resolve_plan_file(plan_name, schema, pkg_dir)
    if not target_file.is_file():
        raise FileNotFoundError(
            f"code_plan_names 指向的文件不存在: {target_file}"
        )

    # 2. 确保父包在 sys.modules 中注册（包内相对导入必需，产物规范 §7）
    pkg_name = _ensure_parent_package(scenario, pkg_dir)
    full_module_name = f"{pkg_name}.{target_file.stem}"
    logger.info(
        "[executor_single] parent package: pkg_name=%s module=%s file=%s",
        pkg_name, full_module_name, target_file.name,
    )

    # 3. per-file 进程级缓存（key=resolved_file+file_mtime，mtime 变化自动失效）
    file_mtime = target_file.stat().st_mtime
    file_cache_key = (str(target_file.resolve()), file_mtime)

    cached_module = _NODE_MODULE_CACHE.get(file_cache_key)
    if cached_module is None:
        # 显式构造模块 spec，不进 sys.path；父包已注册到 sys.modules 使相对导入可解析
        spec = importlib.util.spec_from_file_location(
            full_module_name,
            target_file,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"无法构造 spec for {target_file}")

        cached_module = importlib.util.module_from_spec(spec)
        inject_import_sandbox(cached_module, validator)
        cached_module.__dict__["PlanNode"] = PlanNode
        cached_module.__dict__["AbortError"] = AbortError
        # 注册到 sys.modules 使相对导入在 exec 期间可解析
        sys.modules[full_module_name] = cached_module
        try:
            spec.loader.exec_module(cached_module)
        except BaseException:
            # exec 失败时清理 sys.modules，避免残留半初始化模块
            sys.modules.pop(full_module_name, None)
            raise
        with _CACHE_LOCK:
            _NODE_MODULE_CACHE[file_cache_key] = cached_module
            _evict_if_needed()
        logger.info(
            "[executor_single] module loaded: %s (mtime=%s, cache_key=%s)",
            target_file.name, file_mtime, full_module_name,
        )

    # 4. 取实例 + deepcopy（每次独立副本，避免共享可变状态）
    node = getattr(cached_module, plan_name, None)
    if node is None:
        # 列出模块中所有 PlanNode 实例名，帮助诊断
        available = [
            k for k, v in vars(cached_module).items()
            if isinstance(v, PlanNode)
        ]
        raise AttributeError(
            f"PlanNode '{plan_name}' not found in {target_file.name}. "
            f"确保模块导出同名实例：{plan_name} = XxxNode(). "
            f"可用: {available}"
        )
    if not isinstance(node, PlanNode):
        raise TypeError(f"'{plan_name}' is not a PlanNode instance (got {type(node).__name__})")

    logger.info(
        "[executor_single] load_node done: plan_name=%s node_type=%s file=%s",
        plan_name, type(node).__name__, target_file.name,
    )
    return copy.deepcopy(node)


class SkillCodeExecutor:
    """单节点隔离加载 + callback 注入。无状态（缓存是模块级共享只读）.

    用法::

        executor = SkillCodeExecutor()
        node = executor.load_node(turbo_dir, scenario, plan_name, schema)
        executor.bind_node_callbacks(node, parent_executor)
        result = await node.run(inputs)
    """

    @staticmethod
    def load_node(
        turbo_dir: str,
        scenario: str,
        plan_name: str,
        schema: dict[str, Any],
    ) -> PlanNode:
        """隔离加载单个 PlanNode（委托模块级 load_node）."""
        return load_node(turbo_dir, scenario, plan_name, schema)

    @staticmethod
    def bind_node_callbacks(node: PlanNode, parent_executor: Any) -> None:
        """复用批量 SkillTurboExecutor 的 callback 注入.

        直接委托 parent_executor._bind_node_callbacks(node)，
        它会递归调用 node.set_runtime_callbacks 注入 use_tool/call_llm/stream_llm/fallback 等。

        Args:
            node: PlanNode 实例
            parent_executor: SkillTurboExecutor 实例（提供 runtime callbacks）
        """
        # 委托给 SkillTurboExecutor._bind_node_callbacks（复用，递归注入）
        # getattr 带默认值 + 判空，避免硬取私有方法在缺失时 AttributeError
        bind_fn = getattr(parent_executor, "_bind_node_callbacks", None)
        if bind_fn is None:
            raise AttributeError(
                "parent_executor 缺少 _bind_node_callbacks 方法，无法绑定节点 callbacks"
            )
        bind_fn(node)


