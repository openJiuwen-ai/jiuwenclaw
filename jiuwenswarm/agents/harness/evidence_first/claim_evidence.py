# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ClaimEvidence — 声明-证据绑定与 Replay Certificate。

框架贡献：把论文声明与「任务 → 工具输出 → 运行配置 → 固定种子」绑定，
并提供可离线的确定性复现验证：

- `bind_claim(claim, tool_outputs, task, config)`：生成声明-证据绑定对象，
  记录证据来源（task_id、工具输出、配置名）。
- `evidence_binding_ok(binding)`：检查绑定字段是否闭合（claim、task_id、
  tool_output 至少其一、config 存在）。
- `ReplayCertificate`：从固定种子确定性重建任务集，并与已存任务逐条比对；
  同时校验每个账本条目 → 任务、账本条目 → 配置 的绑定，输出产物 SHA-256 指纹。
- `replay_certificate(task_file, ledger_file, build_fn, seed)`：一键生成证书。

纯 Python、不依赖 openjiuwen，可离线单测。逻辑与 scripts/replay_verify.py 对齐。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TIER_SEED_OFFSET = {"smoke": 0, "verify": 1000, "full": 2000}

# 证据绑定需要出现的字段（值非空才算闭合）。
_BINDING_REQUIRED = ("claim", "task_id", "config")


@dataclass
class ClaimBinding:
    """一条声明的证据绑定记录。"""

    claim: str
    task_id: str = ""
    tool_name: str = ""
    tool_output: str = ""
    config: str = ""
    seed: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim, "task_id": self.task_id,
            "tool_name": self.tool_name, "tool_output": self.tool_output,
            "config": self.config, "seed": self.seed, "extra": self.extra,
        }


def bind_claim(
    claim: str,
    tool_outputs: list[dict[str, Any]],
    *,
    task_id: str = "",
    config: str = "",
    seed: int = 0,
) -> ClaimBinding:
    """绑定声明到最近一次成功工具输出（证据优先）。

    tool_outputs 形如 [{"tool": "run_python", "output": "144"}, ...]，
    取最后一条非空输出作为证据来源。
    """
    binding = ClaimBinding(claim=claim, task_id=task_id, config=config, seed=seed)
    for entry in reversed(tool_outputs or []):
        output = str(entry.get("output") or "").strip()
        if output:
            binding.tool_name = str(entry.get("tool") or "")
            binding.tool_output = output
            break
    return binding


def evidence_binding_ok(binding: ClaimBinding | dict[str, Any]) -> bool:
    """绑定是否闭合：claim/task_id/config 与至少一条证据文本都存在。"""
    d = binding if isinstance(binding, dict) else binding.to_dict()
    has_binding_fields = all(bool(str(d.get(k) or "").strip()) for k in _BINDING_REQUIRED)
    has_evidence = bool(str(d.get("tool_output") or "").strip())
    return has_binding_fields and has_evidence


class ReplayCertificate:
    """确定性复现证书：重建任务 + 校验账本绑定 + 产物指纹。"""

    def __init__(
        self,
        *,
        seed: int,
        build_fn: Callable[[int], list[dict[str, Any]]],
        task_file: Path | str,
        ledger_file: Path | str | None = None,
        task_id_key: str = "id",
        config_field: str = "config",
        config_name_key: str = "name",
        allowed_configs: list[str] | None = None,
    ) -> None:
        self.seed = seed
        self.build_fn = build_fn
        self.task_file = Path(task_file)
        self.ledger_file = Path(ledger_file) if ledger_file else None
        self.task_id_key = task_id_key
        self.config_field = config_field
        self.config_name_key = config_name_key
        self.allowed_configs = allowed_configs

    def verify(self) -> dict[str, Any]:
        """执行全部核验，返回证书内容（JSON 可序列化）。"""
        stored = json.loads(self.task_file.read_text(encoding="utf-8"))
        rebuilt = self.build_fn(self.seed)

        tasks_match = len(stored) == len(rebuilt) and all(
            self._task_equal(a, b) for a, b in zip(stored, rebuilt)
        )

        result: dict[str, Any] = {
            "seed": self.seed,
            "task_file": str(self.task_file),
            "tasks_deterministic": tasks_match,
            "stored_n": len(stored),
            "rebuilt_n": len(rebuilt),
            "task_sha256": self._sha256(self.task_file),
            "binding": {"ok": True, "checked": 0, "errors": []},
            "ledger_sha256": None,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }

        if self.ledger_file and self.ledger_file.exists():
            ledger = json.loads(self.ledger_file.read_text(encoding="utf-8"))
            result["ledger_sha256"] = self._sha256(self.ledger_file)
            task_by_id = {self._task_id(t): t for t in rebuilt}
            errors: list[str] = []
            checked = 0
            for entry in ledger:
                checked += 1
                tid = str(entry.get(self.task_id_key) or "")
                task = task_by_id.get(tid)
                if task is None:
                    errors.append(f"{tid}: 账本引用任务不存在于重建任务集")
                    continue
                if not self._task_equal(task, entry):
                    errors.append(f"{tid}: 账本字段与重建任务不一致")
                cfg = entry.get(self.config_field) or {}
                cfg_name = cfg.get(self.config_name_key) if isinstance(cfg, dict) else None
                if self.allowed_configs and cfg_name not in self.allowed_configs:
                    errors.append(f"{tid}: 配置 {cfg_name} 不在允许列表")
            result["binding"] = {"ok": not errors, "checked": checked, "errors": errors[:10]}

        result["overall"] = "PASS" if (tasks_match and result["binding"]["ok"]) else "FAIL"
        return result

    @staticmethod
    def _task_id(task: dict[str, Any]) -> str:
        return str(task.get("id") or task.get("task_id") or "")

    @staticmethod
    def _task_equal(task: dict[str, Any], other: dict[str, Any]) -> bool:
        oid = str(other.get("id") or other.get("task_id") or other.get("claim_id") or "")
        return (
            task.get("id") == oid
            and task.get("kind") == other.get("kind")
            and task.get("gt") == other.get("gt")
            and task.get("prompt") == other.get("prompt")
        )

    @staticmethod
    def _sha256(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    @staticmethod
    def save(cert: dict[str, Any], path: Path | str) -> Path:
        out = Path(path)
        out.write_text(json.dumps(cert, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
