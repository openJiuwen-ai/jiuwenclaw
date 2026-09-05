# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""声明-证据绑定 + Replay Certificate 离线测试。"""

from __future__ import annotations

import json

import pytest

from jiuwenswarm.agents.harness.evidence_first.claim_evidence import (
    ClaimBinding,
    ReplayCertificate,
    bind_claim,
    evidence_binding_ok,
)


def test_bind_claim_takes_last_successful_output():
    outputs = [
        {"tool": "run_python", "output": "12"},
        {"tool": "run_python", "output": ""},
        {"tool": "run_python", "output": "144"},
    ]
    b = bind_claim("第 12 项结果为 144", outputs, task_id="task_3", config="verify+ledger")
    assert isinstance(b, ClaimBinding)
    assert b.tool_output == "144"
    assert b.task_id == "task_3"
    assert b.config == "verify+ledger"


def test_evidence_binding_closed_requires_all_fields():
    good = bind_claim("c", [{"tool": "t", "output": "7"}], task_id="x", config="y")
    assert evidence_binding_ok(good)
    missing_task = ClaimBinding(claim="c", tool_output="7", config="y")
    assert not evidence_binding_ok(missing_task)


def _write_tasks(path, tasks):
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")


def test_replay_certificate_matches(tmp_path):
    tasks = [
        {"id": "t0", "kind": "positive", "gt": "144", "prompt": "p0"},
        {"id": "t1", "kind": "failure", "gt": "0", "prompt": "p1"},
    ]
    ledger = [
        {"task_id": "t0", "kind": "positive", "gt": "144", "prompt": "p0",
         "config": {"name": "no_check"}},
    ]
    task_file = tmp_path / "tasks.json"
    ledger_file = tmp_path / "ledger.json"
    _write_tasks(task_file, tasks)
    _write_tasks(ledger_file, ledger)

    cert = ReplayCertificate(
        seed=42,
        build_fn=lambda seed: [dict(t) for t in tasks],  # 确定性重建返回相同任务
        task_file=task_file, ledger_file=ledger_file,
        task_id_key="task_id", allowed_configs=["no_check", "verify+ledger"],
    )
    result = cert.verify()
    assert result["overall"] == "PASS"
    assert result["binding"]["checked"] == 1
    assert result["task_sha256"]


def test_replay_certificate_detects_binding_break(tmp_path):
    tasks = [{"id": "t0", "kind": "positive", "gt": "144", "prompt": "p0"}]
    ledger = [
        {"task_id": "t0", "kind": "positive", "gt": "999", "prompt": "p0",
         "config": {"name": "no_check"}},  # gt 被改 → 绑定断裂
        {"task_id": "t_nonexistent", "kind": "x", "gt": "1", "prompt": "x",
         "config": {"name": "no_check"}},   # 任务不存在
        {"task_id": "t0", "kind": "positive", "gt": "144", "prompt": "p0",
         "config": {"name": "bogus"}},       # 配置不在允许列表
    ]
    task_file = tmp_path / "tasks.json"
    ledger_file = tmp_path / "ledger.json"
    _write_tasks(task_file, tasks)
    _write_tasks(ledger_file, ledger)

    cert = ReplayCertificate(
        seed=1,
        build_fn=lambda seed: [dict(t) for t in tasks],
        task_file=task_file, ledger_file=ledger_file,
        task_id_key="task_id", allowed_configs=["no_check"],
    )
    result = cert.verify()
    assert result["overall"] == "FAIL"
    assert len(result["binding"]["errors"]) == 3
