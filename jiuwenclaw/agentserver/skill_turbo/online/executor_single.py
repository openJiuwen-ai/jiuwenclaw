# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillCodeExecutor —— 单节点执行 API（在线模式，无 root，逐节点懒加载）。

职责（设计 §5.6）：
从 colocated ``<skill_root>/turbo/turbo_codes_<scenario>/`` 包加载单个 PlanNode，
注册 runtime callbacks（复用现有 executor 的注入逻辑），跑单节点
``_execute``/``_execute_stream``，返回 node_outputs。

与现有批量 executor 的关系：
- 复用现有 ``SkillTurboExecutor`` 实例的 callback 注入（``_bind_node_callbacks``）
  + runtime callbacks（``use_tool``/``call_llm``/``stream_llm``/``fallback``/...）
- 不复用 ``_prepare_root_node``（在线无 root，按 ``getattr(module, plan_name)`` 取实例）
- group 节点内部仍批量自主编排（sub_plans + inputs.update），group 之间由 Agent 在线驱动

设计 §8.5 代码加载：
临时把 ``<skill_root>/turbo/`` 加入 ``sys.path``（引用计数，退出时移除）→
``importlib`` 加载包 ``turbo_codes_<scenario>.<module>``（包内相对 import 在包内解析）→
``getattr(module, plan_name)`` 取实例执行。
"""

from __future__ import annotations

import copy
import importlib
import logging
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from jiuwenclaw.agentserver.skill_turbo.plan_node import PlanNode

logger = logging.getLogger(__name__)

# turbo_dir → 引用计数：并发加载时避免过早从 sys.path 移除
_turbo_path_refcount: dict[str, int] = {}
_turbo_path_lock = threading.Lock()


class NodeLoadError(Exception):
    """单节点加载失败（模块/实例不存在、类型不符）。"""


@contextmanager
def _turbo_dir_on_sys_path(turbo_dir: str) -> Iterator[None]:
    """临时把 turbo_dir 加入 sys.path，退出时按引用计数移除（防进程级泄漏）。"""
    if not turbo_dir:
        yield
        return
    normalized = str(Path(turbo_dir).resolve())
    with _turbo_path_lock:
        count = _turbo_path_refcount.get(normalized, 0)
        if count == 0 and normalized not in sys.path:
            sys.path.insert(0, normalized)
            logger.debug("[SkillCodeExecutor] added to sys.path: %s", normalized)
        _turbo_path_refcount[normalized] = count + 1
    try:
        yield
    finally:
        with _turbo_path_lock:
            count = _turbo_path_refcount.get(normalized, 1) - 1
            if count <= 0:
                _turbo_path_refcount.pop(normalized, None)
                try:
                    sys.path.remove(normalized)
                    logger.debug(
                        "[SkillCodeExecutor] removed from sys.path: %s", normalized,
                    )
                except ValueError:
                    pass
            else:
                _turbo_path_refcount[normalized] = count


class SkillCodeExecutor:
    """单节点执行器（在线模式，无 root，逐节点懒加载）。

    复用现有 ``SkillTurboExecutor`` 实例提供 runtime callbacks，保证节点内
    ``call_tool``/``call_llm``/``stream_llm``/``has_tool`` 行为与批量模式完全一致。
    """

    def __init__(
        self,
        environment: Any,
        *,
        request_id: str = "",
        channel_id: str = "",
    ) -> None:
        self._env = environment
        self._request_id = request_id
        self._channel_id = channel_id
        # turbo 策略校验器（允许包内相对 import，禁危险调用）
        from jiuwenclaw.agentserver.skill_turbo.validator import PlanCodeValidator
        self._validator = PlanCodeValidator.for_turbo_skill_code(
            allowed_import_prefixes=getattr(environment, "skill_code_import_prefixes", None)
        )

    # ── 节点定位与加载 ──

    def _locate_module(
        self,
        turbo_dir: str,
        scenario: str,
        plan_name: str,
        schema: dict,
    ) -> tuple[str, str]:
        """按 schema.code_plan_names 定位 plan_name 所在文件。

        Returns:
            (module_dotted_name, source_file_path)
        """
        code_plan_names = schema.get("code_plan_names", [])
        if not isinstance(code_plan_names, list):
            raise NodeLoadError(f"schema.code_plan_names 不是列表: {type(code_plan_names)}")
        for entry in code_plan_names:
            if not isinstance(entry, dict):
                continue
            file_name = entry.get("file", "")
            plan_names = entry.get("plan_names", [])
            if not file_name or not isinstance(plan_names, list):
                continue
            if plan_name in plan_names:
                from jiuwenclaw.agentserver.skill_turbo.online.skill_name_guard import (
                    InvalidScenarioError,
                    validate_scenario,
                )
                try:
                    scenario = validate_scenario(scenario)
                except InvalidScenarioError as exc:
                    raise NodeLoadError(str(exc)) from exc
                stem = Path(file_name).stem
                dotted = f"turbo_codes_{scenario}.{stem}"
                source_path = str(
                    Path(turbo_dir) / f"turbo_codes_{scenario}" / Path(file_name).name
                )
                return dotted, source_path
        raise NodeLoadError(
            f"plan_name={plan_name!r} 未在 schema.code_plan_names 中找到对应文件"
        )

    def _validate_source(self, source_path: str) -> None:
        """用 turbo_skill_code 策略对源文件做 AST 校验（优化修复 F8）。

        默认告警不阻断：colocated turbo_codes 含 Path IO，与 AST 黑名单不完全对齐。
        环境属性 ``skill_turbo_validate_on_load=True`` 时改为严格阻断。
        """
        path = Path(source_path)
        if not path.is_file():
            raise NodeLoadError(f"节点源文件不存在: {source_path}")
        try:
            source_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "[SkillCodeExecutor] read source for validate failed %s: %s",
                source_path, exc,
            )
            raise NodeLoadError(f"无法读取节点源文件: {source_path}: {exc}") from exc
        errors = self._validator.validate(source_text)
        if not errors:
            return
        strict = bool(getattr(self._env, "skill_turbo_validate_on_load", False))
        msg = f"turbo 代码校验{'失败' if strict else '告警'} {path.name}: {'; '.join(errors[:5])}"
        if strict:
            raise NodeLoadError(msg)
        logger.warning("[SkillCodeExecutor] %s", msg)

    def load_node(
        self,
        turbo_dir: str,
        scenario: str,
        plan_name: str,
        schema: dict,
    ) -> PlanNode:
        """公共接口：加载单节点 PlanNode 实例。"""
        return self._load_node_instance(turbo_dir, scenario, plan_name, schema)

    def _load_node_instance(
        self,
        turbo_dir: str,
        scenario: str,
        plan_name: str,
        schema: dict,
    ) -> PlanNode:
        """加载 ``turbo_codes_<scenario>.<module>``，``getattr(module, plan_name)`` 取实例。

        - 临时把 turbo_dir 加入 sys.path（上下文管理器退出时按引用计数移除）
        - importlib.import_module 加载包（包内相对 import 在包内解析）
        - getattr(module, plan_name) 取模块级实例
        - 深拷贝（避免并发共享回调，与现有 _prepare_root_node 同范式）
        - 校验是 PlanNode 实例
        """
        module_dotted, source_path = self._locate_module(
            turbo_dir, scenario, plan_name, schema,
        )
        self._validate_source(source_path)
        with _turbo_dir_on_sys_path(turbo_dir):
            try:
                module = importlib.import_module(module_dotted)
            except Exception as exc:
                raise NodeLoadError(
                    f"加载模块 {module_dotted} 失败: {exc}"
                ) from exc
            node = getattr(module, plan_name, None)
            if node is None:
                raise NodeLoadError(
                    f"模块 {module_dotted} 无模块级实例 {plan_name!r}"
                )
            if not isinstance(node, PlanNode):
                raise NodeLoadError(
                    f"模块 {module_dotted}.{plan_name} 不是 PlanNode 实例: {type(node).__name__}"
                )
        # 深拷贝：plan_code 通过 import 导入的是模块级单例，多个并发请求共享同一棵节点树。
        # _bind_node_callbacks 会递归覆盖回调为当前 executor 的绑定方法，若不拷贝，
        # 后启动的请求会覆盖先启动请求的回调。与现有 _prepare_root_node 同范式。
        node = copy.deepcopy(node)
        logger.debug(
            "[SkillCodeExecutor] loaded node plan_name=%s module=%s sub_plans=%d",
            plan_name, module_dotted, len(node.sub_plans),
        )
        return node

    # ── 单节点执行 ──

    async def run_single_node(
        self,
        *,
        turbo_dir: str,
        scenario: str,
        plan_name: str,
        schema: dict,
        node_inputs: dict[str, Any],
        parent_session: Any = None,
        executor: Any = None,
    ) -> dict[str, Any]:
        """执行单个 PlanNode，返回 node_outputs。

        流程（设计 §5.6）：
        1. node = self._load_node_instance(...)
        2. 通过 executor（现有 SkillTurboExecutor）绑定 callbacks：
           executor._bind_node_callbacks(node)  # 复用现有 callback 注入
        3. result = await node.run(node_inputs)
           - AbortError → 透传（HITL，不走 fallback）
           - 其他异常：node.run() 内部已调 fallback callback 兜底返回（不抛）；
             若未注入 callback 则抛给调用方
        4. 提取 node_outputs（dict）：result 是 dict 则直接用，否则包成 {"result": result}
        5. 若 result 带 fallback 标记 → 在返回中带 fallback=True
        6. 返回 node_outputs

        Args:
            turbo_dir: turbo/ 目录绝对路径。
            scenario: 任务切面，如 "create_ppt"。
            plan_name: 要执行的节点名（group 入口或叶节点）。
            schema: scenario 对应的 schema dict。
            node_inputs: 组装好的节点输入（从 ContextStore.accumulator 取 + increment 覆盖）。
            parent_session: 父会话（流式事件转发用）。
            executor: 现有 SkillTurboExecutor 实例（提供 callback 注入）。

        Returns:
            node_outputs dict。若走了 fallback，带 ``fallback=True`` 标记。

        Raises:
            AbortError: HITL 中断（透传，不走 fallback）。
            NodeLoadError: 节点加载失败。
            Exception: 节点执行失败且无 fallback callback 兜底。
        """
        node = self._load_node_instance(turbo_dir, scenario, plan_name, schema)

        # 复用现有 executor 的 callback 注入（has_tool/use_tool/call_llm/stream_llm/
        # fallback/fallback_stream/extract_json/log/before_subplan_execute/after_subplan_execute）
        if executor is not None:
            bind = getattr(executor, "bind_node_callbacks", None) or getattr(
                executor, "_bind_node_callbacks", None,
            )
            if callable(bind):
                bind(node)
            else:
                logger.warning(
                    "[SkillCodeExecutor] executor 无 bind_node_callbacks，节点回调未注入"
                )
        else:
            logger.warning(
                "[SkillCodeExecutor] executor=None，节点回调未注入（仅测试场景可用）"
            )

        logger.info(
            "[SkillCodeExecutor] run_single_node plan_name=%s input_keys=%s sub_plans=%d",
            plan_name, list(node_inputs.keys()), len(node.sub_plans),
        )

        # node.run() 是模板方法，自带 fallback：
        # - AbortError → 直接抛（HITL，不走 fallback）
        # - 其他异常 → 若注入了 fallback callback → callback 兜底返回（不抛）；
        #   若未注入 → 抛给调用方
        result = await node.run(node_inputs)

        # 提取 node_outputs
        if isinstance(result, dict):
            node_outputs = dict(result)
        else:
            node_outputs = {"result": result}

        # fallback 标记透传（fallback_handler 返回的结果带 fallback=True）
        is_fallback = bool(node_outputs.get("fallback", False))
        if is_fallback:
            logger.info(
                "[SkillCodeExecutor] node ran via fallback plan_name=%s", plan_name,
            )

        logger.info(
            "[SkillCodeExecutor] run_single_node done plan_name=%s output_keys=%s fallback=%s",
            plan_name,
            list(node_outputs.keys()) if isinstance(node_outputs, dict) else "?",
            is_fallback,
        )
        return node_outputs

    # ── HITL 单节点重放 ──

    def set_pending_resume(
        self,
        executor: Any,
        tool_call_id: str,
        user_input: Any,
    ) -> None:
        """HITL 单节点重放注入点（透传给底层 executor）。

        恢复时重新加载中断的 group/叶节点，从该节点开头重放，重放到同一
        tool_call_id 时注入 user_input，继续后续子步骤（设计 §6.5）。
        """
        if executor is None:
            return
        setter = getattr(executor, "set_pending_resume", None)
        if callable(setter):
            setter(tool_call_id, user_input)
        else:
            logger.warning(
                "[SkillCodeExecutor] executor 无 set_pending_resume，HITL 重放注入未生效"
            )


__all__ = [
    "NodeLoadError",
    "SkillCodeExecutor",
]
