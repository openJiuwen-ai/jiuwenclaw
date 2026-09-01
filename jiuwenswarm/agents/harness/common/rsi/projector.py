"""RsiProjector：单投影函数（事件 ↔ tree/进度/推送 同源；内部 v3 §4.4）。

推/拉同源：``derive_progress`` / ``derive_tree`` / ``derive_tree_delta`` 都取自
``nodes``（根 + 派生节点投影缓存）与 ``metric``（最新 progress.metric 快照）。
事件尽力而为、快照兜底（一致性 §8.6）：节点树持久化在内存 + ``tree.json`` 落盘，
进程重启后由 C3（read_state 恢复通道）重建——恢复通道为 ⚠️外部，本期提供落盘 + 重建接口。
"""

from __future__ import annotations

import json
import threading
from typing import Any

from jiuwenswarm.agents.harness.common.rsi.errors import RsiTaskNotFound
from jiuwenswarm.agents.harness.common.rsi.models import RsiTreeNode, utcnow_iso

_ROOT = "ROOT"


class RsiProjector:
    """节点/进度投影（事件投影缓存 + 快照重建，推/拉一致）。"""

    def __init__(self, tasks_root) -> None:
        self.tasks_root = tasks_root
        self._lock = threading.RLock()
        self._nodes: dict[str, dict[str, RsiTreeNode]] = {}
        self._metric: dict[str, dict[str, Any]] = {}
        # 引擎侧稳定候选引用（node.created.node.ref）→ 服务侧 node_id 索引。
        # C3 恢复通道反查（read_state candidate_gates）前，本索引是本层完成
        # stage 事件定位的关键映射（内部 v3 §4.4 投影规则：node_ref → N<序号>）。
        self._ref_index: dict[str, dict[str, str]] = {}

    def _tree_path(self, task_id: str):
        from pathlib import Path

        return Path(self.tasks_root) / task_id / "tree.json"

    def register_root(self, task_id: str, *, baseline: float | None = None, description: str = "基线") -> None:
        """注册根节点（服务侧在任务创建/首跑时调用）。"""
        with self._lock:
            nodes = self._nodes.setdefault(task_id, {})
            if _ROOT in nodes:
                return
            nodes[_ROOT] = RsiTreeNode(
                node_id=_ROOT,
                iteration=0,
                parent_id=None,
                type="ROOT",
                adopted=True,
                score=baseline,
                description=description,
            )
            self._persist_locked(task_id)

    # -- 事件消费 --

    def on_node_created(self, task_id: str, payload: dict[str, Any]) -> RsiTreeNode | None:
        """``node.created`` 事件 → 节点投影（内部 v3 §4.4 规则表）。"""
        node = payload.get("node") if isinstance(payload.get("node"), dict) else payload
        node_ref = str(node.get("ref") or "") if isinstance(node, dict) else ""
        if not node_ref:
            return None
        with self._lock:
            nodes = self._nodes.setdefault(task_id, {})
            # 服务侧稳定 ID：根=ROOT，其余 N<序号>（按派生序）
            derived_seq = sum(1 for n in nodes.values() if n.node_id != _ROOT)
            node_id = _ROOT if node_ref == "root" else f"N{derived_seq + 1}"
            if node_id in nodes:
                return nodes[node_id]
            parent_ref = node.get("parent_ref") if isinstance(node, dict) else None
            parent_id = self._map_parent_id(nodes, parent_ref)
            node_type = self._map_type(node)
            tree_node = RsiTreeNode(
                node_id=node_id,
                iteration=len(nodes),
                parent_id=parent_id,
                type=node_type,
                adopted=bool(node.get("accepted", False)),
                score=_safe_float(node.get("score")),
                description=str(node.get("summary") or "") or None,
                failure_reason=str(node.get("failure_reason") or "") or None,
                failure_class=str(node.get("failure_class") or "") or None,
                changes=node.get("changes") if isinstance(node.get("changes"), list) else None,
            )
            nodes[node_id] = tree_node
            self._ref_index.setdefault(task_id, {})[node_ref] = node_id
            self._persist_locked(task_id)
            return tree_node

    def persist_node_update(self, task_id: str, node: RsiTreeNode) -> None:
        """快照/描述等节点字段变更后回写落盘（单节点更新）。"""
        with self._lock:
            nodes = self._nodes.get(task_id)
            if nodes is None:
                return
            existing = nodes.get(node.node_id)
            if existing is None:
                return
            nodes[node.node_id] = node
            self._persist_locked(task_id)

    def on_node_stage(self, task_id: str, payload: dict[str, Any]) -> RsiTreeNode | None:
        """``node.stage`` 事件 → 同节点 description 动态更新（web §9.2）。"""
        node_ref = str(payload.get("node_ref") or "")
        if not node_ref:
            return None
        stage = payload.get("stage") if isinstance(payload.get("stage"), dict) else {}
        stage_name = str(stage.get("name") or "") or None
        with self._lock:
            nodes = self._nodes.get(task_id)
            if not nodes:
                return None
            node = self._resolve_node(task_id, node_ref)
            if node is None:
                return None
            if stage_name:
                base = node.description or ""
                if base and not base.endswith(stage_name):
                    node.description = f"{base} › {stage_name}"
                else:
                    node.description = base or stage_name
            self._persist_locked(task_id)
            return node

    def on_progress_metric(self, task_id: str, payload: dict[str, Any]) -> None:
        """``progress.metric`` → 最新值语义快照（可合并丢弃中间值）。"""
        with self._lock:
            metric = self._metric.setdefault(task_id, {})
            for key in ("iteration", "total_iterations", "score", "baseline", "node_ref", "metrics"):
                if key in payload:
                    metric[key] = payload[key]

    # -- 拉取（web） --

    def derive_progress(self, task_id: str) -> dict[str, Any]:
        """``rsi.task.get`` 的 progress（web §6.3）与 P2 推送同源。"""
        with self._lock:
            nodes = self._nodes.get(task_id)
            metric = self._metric.get(task_id, {})
        if nodes is None and metric is None:
            raise RsiTaskNotFound(task_id)
        iteration = int(metric.get("iteration") or (len(nodes) - 1 if nodes else 0))
        return {
            "iteration": iteration,
            "total_iterations": int(metric.get("total_iterations") or 0),
            "score": _safe_float(metric.get("score")),
            "baseline": _safe_float(metric.get("baseline")),
        }

    def derive_tree(self, task_id: str) -> dict[str, Any]:
        """``rsi.tree.get`` 全量出参（web §9.1）。"""
        with self._lock:
            nodes = self._nodes.get(task_id)
            metric = self._metric.get(task_id, {})
        if nodes is None:
            raise RsiTaskNotFound(task_id)
        depth = 0
        for node in nodes.values():
            d = 0
            cursor: RsiTreeNode | None = node
            while cursor is not None and cursor.parent_id is not None:
                d += 1
                cursor = nodes.get(cursor.parent_id)
            depth = max(depth, d)
        return {
            "nodes": [node.to_dict() for node in sorted(nodes.values(), key=lambda n: n.iteration)],
            "depth": depth,
            "iteration": int(metric.get("iteration") or 0),
        }

    def derive_tree_delta(self, task_id: str, since_node_ids: set[str]) -> dict[str, Any]:
        """P3 推送增量：基于事件而非文件 diff（内部 v3 §4.4）。"""
        with self._lock:
            nodes = self._nodes.get(task_id, {})
        return {
            "nodes": [
                node.to_dict()
                for node in sorted(nodes.values(), key=lambda n: n.iteration)
                if node.node_id not in since_node_ids
            ]
        }

    # -- 恢复 --

    def load_from_disk(self, task_id: str) -> None:
        """进程重启/晚订阅：读 tree.json 重建节点缓存（快照兜底）。"""
        path = self._tree_path(task_id)
        if not path.is_file():
            return
        with self._lock:
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                return
            raw_nodes = data.get("nodes") if isinstance(data, dict) else None
            if isinstance(raw_nodes, list):
                self._nodes[task_id] = {
                    item.get("node_id"): RsiTreeNode.from_dict(item) if isinstance(item, dict) else item
                    for item in raw_nodes
                    if isinstance(item, dict) and item.get("node_id")
                }
            if isinstance(data, dict) and isinstance(data.get("metric"), dict):
                self._metric[task_id] = dict(data["metric"])
            self._persist_locked(task_id)

    # -- 内部 --

    def _persist_locked(self, task_id: str) -> None:
        if not self._nodes.get(task_id):
            return
        from pathlib import Path

        path = Path(self.tasks_root) / task_id
        path.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [n.to_dict() for n in self._nodes[task_id].values()],
            "metric": self._metric.get(task_id, {}),
            "saved_at": utcnow_iso(),
        }
        with (path / "tree.json").open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    @staticmethod
    def _map_parent_id(nodes: dict[str, RsiTreeNode], parent_ref: Any) -> str | None:
        if not parent_ref or str(parent_ref).lower() == "root":
            return _ROOT if _ROOT in nodes else None
        # 引擎侧稳定候选引用 → 服务侧节点映射：默认按派生序指针表（C3 恢复通道后升级为 read_state 反查）
        return None

    @staticmethod
    def _map_type(node: dict[str, Any]) -> str:
        outcome = str(node.get("outcome") or "").upper()
        if outcome in {"ADOPTED", "REJECTED", "PROVISIONAL", "PRUNED"}:
            return outcome
        return "ADOPTED" if bool(node.get("accepted", False)) else "REJECTED"

    def _resolve_node(self, task_id: str, node_ref: str) -> RsiTreeNode | None:
        nodes = self._nodes.get(task_id)
        if not nodes:
            return None
        if node_ref.lower() == "root":
            return nodes.get(_ROOT)
        if node_ref in nodes:
            return nodes[node_ref]
        mapped = self._ref_index.get(task_id, {}).get(node_ref)
        if mapped and mapped in nodes:
            return nodes[mapped]
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None