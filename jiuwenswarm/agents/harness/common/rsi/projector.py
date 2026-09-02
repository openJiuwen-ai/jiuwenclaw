"""RsiProjector：单投影函数（事件 ↔ tree/进度/推送 同源；内部 v3 §4.4）。

推/拉同源：``derive_progress`` / ``derive_tree`` / ``derive_tree_delta`` 都取自
``nodes``（根 + 派生节点投影缓存）与 ``metric``（最新 progress.metric 快照）。
事件尽力而为、快照兜底（一致性 §8.6）：节点树持久化在内存 + ``tree.json`` 落盘，
进程重启后由 C3（read_state 恢复通道）重建——恢复通道为 ⚠️外部，本期提供落盘 + 重建接口。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
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

    def _tree_path(self, task_id: str) -> Path:
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
            self._ref_index.setdefault(task_id, {})["root"] = _ROOT
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
            parent_id = self._map_parent_id(task_id, parent_ref)
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

    def on_provider_node(self, task_id: str, node: Any) -> RsiTreeNode | None:
        """Project an agent-core ``EventNode`` without renumbering its ID.

        Artifact Providers persist a complete node and use that ID in their
        report/tree/artifact references.  Keeping the ID intact is what lets
        a tree push and a later tree query refer to the same node.
        """
        from jiuwenswarm.agents.harness.common.rsi.artifact_adapter import provider_node_to_dict

        raw = provider_node_to_dict(node)
        node_id = str(raw.get("node_id") or "")
        if not node_id:
            return None
        changes = raw.get("changes")
        tree_node = RsiTreeNode(
            node_id=node_id,
            iteration=_safe_int(raw.get("iteration")),
            parent_id=str(raw["parent_id"]) if raw.get("parent_id") is not None else None,
            type=str(raw.get("type") or "REJECTED").upper(),
            adopted=bool(raw.get("adopted")),
            score=_safe_float(raw.get("score")),
            description=raw.get("description"),
            snapshot_artifact_id=raw.get("snapshot_artifact_id"),
            failure_reason=raw.get("failure_reason"),
            failure_class=raw.get("failure_class"),
            changes=[dict(item) for item in changes if isinstance(item, dict)] if isinstance(changes, list) else None,
            extra=dict(raw.get("extra") or {}) if isinstance(raw.get("extra"), dict) else None,
        )
        with self._lock:
            self._nodes.setdefault(task_id, {})[node_id] = tree_node
            self._ref_index.setdefault(task_id, {})[node_id] = node_id
            self._persist_locked(task_id)
        return tree_node

    def sync_provider_tree(self, task_id: str, tree: Any) -> dict[str, Any]:
        """Replace the artifact projection from a Provider tree snapshot."""
        from jiuwenswarm.agents.harness.common.rsi.artifact_adapter import provider_tree_to_web

        raw = provider_tree_to_web(tree)
        nodes: dict[str, RsiTreeNode] = {}
        ref_index: dict[str, str] = {}
        for item in raw.get("nodes", []):
            node = self._tree_node_from_web(item)
            if node is None:
                continue
            nodes[node.node_id] = node
            ref_index[node.node_id] = node.node_id
        with self._lock:
            self._nodes[task_id] = nodes
            self._ref_index[task_id] = ref_index
            metric = self._metric.setdefault(task_id, {})
            metric["iteration"] = _safe_int(raw.get("iteration"))
            self._persist_locked(task_id)
        return self.derive_tree(task_id)

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
        # 真未知任务（无节点也无 metric 快照）才抛 404；仅有 metric 的合法中间态
        # （root 注册前 P2 链路可能先收到 progress.metric）仍返回零值进度。
        if nodes is None and not metric:
            raise RsiTaskNotFound(task_id)
        iteration = _safe_int(metric.get("iteration")) or (len(nodes) - 1 if nodes else 0)
        return {
            "iteration": iteration,
            "total_iterations": _safe_int(metric.get("total_iterations")),
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
            "iteration": _safe_int(metric.get("iteration")),
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
                self._ref_index[task_id] = {
                    node_id: node_id for node_id in self._nodes[task_id]
                }
            if isinstance(data, dict) and isinstance(data.get("metric"), dict):
                self._metric[task_id] = dict(data["metric"])
            self._persist_locked(task_id)

    # -- 内部 --

    def _persist_locked(self, task_id: str) -> None:
        if not self._nodes.get(task_id):
            return
        path = Path(self.tasks_root) / task_id
        path.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [n.to_dict() for n in self._nodes[task_id].values()],
            "metric": self._metric.get(task_id, {}),
            "saved_at": utcnow_iso(),
        }
        with (path / "tree.json").open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def _map_parent_id(self, task_id: str, parent_ref: Any) -> str | None:
        """引擎侧稳定引用 → 服务侧节点 ID 反查（node.created 先于子节点出现）。"""
        nodes = self._nodes.get(task_id) or {}
        if not parent_ref or str(parent_ref).lower() == "root":
            return _ROOT if _ROOT in nodes else None
        mapped = self._ref_index.get(task_id, {}).get(str(parent_ref))
        return mapped if mapped in nodes else None

    @staticmethod
    def _tree_node_from_web(raw: dict[str, Any]) -> RsiTreeNode | None:
        node_id = str(raw.get("node_id") or "")
        if not node_id:
            return None
        changes = raw.get("changes")
        return RsiTreeNode(
            node_id=node_id,
            iteration=_safe_int(raw.get("iteration")),
            parent_id=str(raw["parent_id"]) if raw.get("parent_id") is not None else None,
            type=str(raw.get("type") or "REJECTED").upper(),
            adopted=bool(raw.get("adopted")),
            score=_safe_float(raw.get("score")),
            description=raw.get("description"),
            snapshot_artifact_id=raw.get("snapshot_artifact_id"),
            failure_reason=raw.get("failure_reason"),
            failure_class=raw.get("failure_class"),
            changes=[dict(item) for item in changes if isinstance(item, dict)] if isinstance(changes, list) else None,
            extra=dict(raw.get("extra") or {}) if isinstance(raw.get("extra"), dict) else None,
        )

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


def _safe_int(value: Any) -> int:
    """畸形引擎载荷防御（PR !5798 #10）：返回 0 而非抛 ValueError/TypeError。"""
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
