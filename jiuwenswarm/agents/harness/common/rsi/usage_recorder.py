"""RsiUsageRecorder：progress.usage 事件聚合（内部 v3 §4.6）。

- ``record(task_id, node_ref, model_call)``：每次模型调用一条；服务侧按节点/任务聚合。
- ``get(task_id)``：出参 = web §3.4 usage 统一结构 + per_iteration/usage_by_node（§8.2）。
- 费用单价算法（C1）为**中优先级预留**：``cost_estimate`` 默认 0.0 占位，不实现单价。
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.agents.harness.common.rsi.errors import RsiTaskNotFound
from jiuwenswarm.agents.harness.common.rsi.models import RsiModelCall, Usage

_ROOT_NODE = "ROOT"


class RsiUsageRecorder:
    """用量归属与聚合（内存态；任务级持久化由 TaskStore for 后续版本）。

    约束：接口返回统一 usage 结构（§3.4）；单价算法遗留（§13 TODO 2），
    仅做归属与聚合，保证字段形状可用。
    """

    def __init__(self) -> None:
        self._by_node: dict[str, dict[str, Usage]] = {}  # task_id -> {node_id: Usage}
        self._node_sequence: dict[str, list[str]] = {}  # task_id -> 派生节点序

    def record(
        self,
        task_id: str,
        node_ref: str | None,
        model_call: RsiModelCall,
    ) -> None:
        node_id = node_ref or _ROOT_NODE
        if task_id not in self._by_node:
            self._by_node[task_id] = {}
        usage = self._by_node[task_id].setdefault(node_id, Usage())
        if node_id != _ROOT_NODE and node_id not in self._node_sequence.setdefault(task_id, []):
            self._node_sequence[task_id].append(node_id)
        usage.merge(
            Usage(
                tokens=model_call.tokens,
                call_count=model_call.call_count,
            )
        )

    def record_engine_event(self, task_id: str, payload: dict[str, Any]) -> None:
        """从 ``progress.usage`` 事件载荷归一记录（内部 v3 §3.3）。"""
        model_call_raw = payload.get("model_call")
        if not isinstance(model_call_raw, dict):
            return
        from jiuwenswarm.agents.harness.common.rsi.models import Tokens

        tokens_raw = model_call_raw.get("tokens") or {}
        tokens = Tokens(
            input=int(tokens_raw.get("input") or 0),
            output=int(tokens_raw.get("output") or 0),
            cache_hit=int(tokens_raw.get("cache_hit") or 0),
        )
        model_call = RsiModelCall(
            model=str(model_call_raw.get("model") or ""),
            call_count=int(model_call_raw.get("call_count") or 1),
            tokens=tokens,
        )
        self.record(task_id, payload.get("node_ref"), model_call)

    def get(self, task_id: str) -> dict[str, Any]:
        """``rsi.usage.get`` 出参（web §8.2）。"""
        if task_id not in self._by_node:
            raise RsiTaskNotFound(task_id)
        nodes = self._by_node.get(task_id, {})
        total = Usage()
        for usage in nodes.values():
            total.merge(usage)
        per_iteration = []
        for index, node_id in enumerate(self._node_sequence.get(task_id, []), start=1):
            per_iteration.append(
                {"iteration": index, "usage": nodes.get(node_id, Usage()).to_dict()}
            )
        usage_by_node = {node_id: usage.to_dict() for node_id, usage in nodes.items()}
        return {
            "usage": total.to_dict(),
            "per_iteration": per_iteration,
            "usage_by_node": usage_by_node,
        }

    def usage_summary(self, task_id: str) -> dict[str, Any] | None:
        """``rsi.task.get`` 的 usage 摘要（仅当有记录）。"""
        try:
            return self.get(task_id)["usage"]
        except RsiTaskNotFound:
            return None