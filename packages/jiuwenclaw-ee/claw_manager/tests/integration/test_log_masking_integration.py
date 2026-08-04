# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""《日志脱敏规则下发.md》§10 验证方案的集成测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw_manager.core.application_config.log_masking_rule import (
    _builtin_seed_rows,
)
from jiuwenclaw_manager.core.instance.instance_service import (
    bootstrap_gateway_log_masking,
    is_log_masking_seeded,
    merge_instance_data,
)
from jiuwenclaw_manager.infrastructure.utils import utc_now
from jiuwenclaw_manager.models.instance_models import INSTANCE_INFO_TABLE_DEF

from conftest import LogMaskingIntegrationHarness, probe_sample

from jiuwenclaw.infrastructure.log_masking.engine import LogMaskingEngine
from jiuwenclaw.infrastructure.log_masking.probes import LOG_MASKING_PROBE_SAMPLES

pytestmark = pytest.mark.integration

S2 = probe_sample("S2")
S3 = probe_sample("S3")
_BUILTIN_IDS = {row["rule_id"] for row in _builtin_seed_rows("placeholder")}

# 与 probes.py / 《日志脱敏规则下发.md》§10.4 对齐的期望
_PROBE_SAMPLE_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "S1": {
        "secrets": ["mySecret", "user=alice"],
        "preserved": ["password="],
    },
    "S2": {
        "secrets": ["user@example.com", "13800138000"],
        "preserved": ["contact:", "phone:"],
    },
    "S3": {
        "secrets": ["ORD-1234567890"],
        "preserved": ["order", "shipped"],
        "requires_ord_rule": True,
    },
    "S4": {
        "secrets": ["sk-plain-no-quotes"],
        "preserved": ["api_key="],
    },
    "S5": {
        "secrets": ["secret-in-single-quotes"],
        "preserved": ["LONG_TOKEN:"],
    },
    "S6": {
        "secrets": ["a-b c"],
        "preserved": ["CAT_TOKEN"],
    },
    "S7": {
        "secrets": ["sk-abc"],
        "preserved": ['"note": "ok"'],
    },
    "S8": {
        "secrets": ["eyJhbGciOiJIUzI1NiJ9.payload.sig"],
        "preserved": ["refresh_token:"],
    },
    "S9": {
        "secrets": ["110101199003078431"],
        "preserved": ["id_card=", "verified"],
    },
    "S10": {
        "secrets": ["bafjdksjfksajf", "wandhfk"],
        "preserved": ['"OFEICE_CIAW_CAT_ID":"assistant"'],
    },
}


async def _ensure_ord_rule(h: LogMaskingIntegrationHarness) -> None:
    resp = await h.http.post(
        h.api_prefix(),
        json={
            "rule_name": "订单号脱敏",
            "pattern": r"ORD-[0-9]{10,}",
            "replacement": "******",
            "priority": 60,
            "enabled": True,
        },
    )
    assert resp.status_code == 200


async def _create_instance_row(
    handler: Any,
    jiuwenclaw_id: str,
    *,
    data: dict | None = None,
) -> None:
    now = utc_now()
    await handler.create(
        INSTANCE_INFO_TABLE_DEF.table_name,
        {
            "jiuwenclaw_id": jiuwenclaw_id,
            "jiuwenclaw_name": "integration-test",
            "creator_id": "tester",
            "description": None,
            "k8s_master_host": "127.0.0.1",
            "k8s_auth_type": "none",
            "k8s_auth_config": "{}",
            "k8s_namespace": "default",
            "status": "online",
            "data": data,
            "created_at": now,
            "updated_at": now,
        },
    )


# ---------------------------------------------------------------------------
# §10.3 REST CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_list_and_get_after_bootstrap(log_masking_harness: LogMaskingIntegrationHarness):
    """§10.3.1：bootstrap 后列表含 builtin 种子，单条 builtin_email 可查询。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()

    list_resp = await h.http.get(h.api_prefix())
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["code"] == 200
    items = list_body["data"]["items"]
    assert items
    rule_ids = {item["rule_id"] for item in items}
    assert "builtin_email" in rule_ids
    assert _BUILTIN_IDS.issubset(rule_ids)

    enabled_resp = await h.http.get(h.api_prefix(), params={"enabled": "true"})
    assert enabled_resp.status_code == 200
    enabled_ids = {
        item["rule_id"] for item in enabled_resp.json()["data"]["items"]
    }
    assert "builtin_email" in enabled_ids

    get_resp = await h.http.get(f"{h.api_prefix()}/builtin_email")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["rule_id"] == "builtin_email"


@pytest.mark.asyncio
async def test_rest_create_patch_delete_lifecycle(log_masking_harness: LogMaskingIntegrationHarness):
    """§10.3.2–10.3.4：POST 生成 UUID → PATCH → DELETE → GET 404。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()

    create_resp = await h.http.post(
        h.api_prefix(),
        json={
            "rule_name": "订单号脱敏",
            "description": "integration test",
            "pattern": r"ORD-[0-9]{10,}",
            "replacement": "******",
            "priority": 60,
            "enabled": True,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    assert created["source"] == "custom"
    rule_id = created["rule_id"]
    assert rule_id
    assert len(rule_id) >= 8

    patch_resp = await h.http.patch(
        f"{h.api_prefix()}/{rule_id}",
        json={"enabled": True, "replacement": "REDACT", "priority": 120},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()["data"]
    assert patched["replacement"] == "REDACT"
    assert patched["priority"] == 120

    delete_resp = await h.http.delete(f"{h.api_prefix()}/{rule_id}")
    assert delete_resp.status_code == 200

    missing_resp = await h.http.get(f"{h.api_prefix()}/{rule_id}")
    assert missing_resp.status_code == 404


@pytest.mark.asyncio
async def test_rest_create_invalid_pattern_returns_400(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """§10.3.2：非法 pattern 返回 400，MDB 无新增行。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()
    before = await h.mdb_rule_ids()

    bad_resp = await h.http.post(
        h.api_prefix(),
        json={"rule_name": "bad", "pattern": "(", "enabled": True},
    )
    assert bad_resp.status_code == 400
    assert await h.mdb_rule_ids() == before


# ---------------------------------------------------------------------------
# §10.1 同步可靠性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mdb_gdb_align_after_bootstrap_and_create(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """MDB ↔ GDB：bootstrap 与 REST create 后两侧 rule_id 集合一致。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()
    assert await h.mdb_rule_ids() == await h.gdb_rule_ids()

    create_resp = await h.http.post(
        h.api_prefix(),
        json={
            "rule_name": "sync-check",
            "pattern": r"SYNC-[0-9]+",
            "priority": 50,
            "enabled": True,
        },
    )
    assert create_resp.status_code == 200
    assert await h.mdb_rule_ids() == await h.gdb_rule_ids()


@pytest.mark.asyncio
async def test_bootstrap_seed_once_sets_log_masking_seeded_flag(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """Gateway 注册 bootstrap：仅首次 seed；``log_masking_seeded=true`` 后不再 seed。"""
    h = log_masking_harness
    await _create_instance_row(h.manager_handler, h.jiuwenclaw_id)

    with patch(
        "jiuwenclaw_manager.core.application_config.log_masking_rule.seed_builtin_log_masking_rules",
        new_callable=AsyncMock,
        return_value=4,
    ) as seed_mock:
        await bootstrap_gateway_log_masking(h.manager_handler, h.jiuwenclaw_id)

    seed_mock.assert_awaited_once()
    assert await is_log_masking_seeded(h.manager_handler, h.jiuwenclaw_id)

    with patch(
        "jiuwenclaw_manager.core.application_config.log_masking_rule.seed_builtin_log_masking_rules",
        new_callable=AsyncMock,
    ) as seed_mock:
        await bootstrap_gateway_log_masking(h.manager_handler, h.jiuwenclaw_id)

    seed_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_instance_data_preserves_log_masking_seeded(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    h = log_masking_harness
    await _create_instance_row(
        h.manager_handler,
        h.jiuwenclaw_id,
        data={"gateway_version": "1.0"},
    )

    await merge_instance_data(
        h.manager_handler, h.jiuwenclaw_id, {"log_masking_seeded": True}
    )
    row = await h.manager_handler.get(
        INSTANCE_INFO_TABLE_DEF.table_name,
        {"jiuwenclaw_id": h.jiuwenclaw_id},
    )
    assert row.data["log_masking_seeded"] is True
    assert row.data["gateway_version"] == "1.0"


# ---------------------------------------------------------------------------
# §10.4 热更新（Gateway 进程；AgentServer 仅冷启动）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hot_reload_h1_s2_masks_email_and_mobile(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """H1：``builtin_email`` 启用时 S2 邮箱/手机号被掩码。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()

    out = h.sanitize(S2)
    assert "example.com" not in out
    assert "13800138000" not in out
    assert "******" in out


@pytest.mark.asyncio
async def test_hot_reload_h2_disable_builtin_email(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """H2：关闭 ``builtin_email`` 后 S2 邮箱保持明文（手机号仍可能被其它规则掩码）。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()

    patch_resp = await h.http.patch(
        f"{h.api_prefix()}/builtin_email",
        json={"enabled": False},
    )
    assert patch_resp.status_code == 200

    out = h.sanitize(S2)
    assert "user@example.com" in out


@pytest.mark.asyncio
async def test_hot_reload_h3_to_h5_ord_rule_lifecycle(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """H3–H5：ORD 规则创建、关闭、replacement 热生效。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()

    create_resp = await h.http.post(
        h.api_prefix(),
        json={
            "rule_name": "订单号脱敏",
            "pattern": r"ORD-[0-9]{10,}",
            "replacement": "******",
            "priority": 60,
            "enabled": True,
        },
    )
    assert create_resp.status_code == 200
    rule_id = create_resp.json()["data"]["rule_id"]

    # H3
    assert "ORD-1234567890" not in h.sanitize(S3)
    assert "******" in h.sanitize(S3)

    # H4
    disable_resp = await h.http.patch(
        f"{h.api_prefix()}/{rule_id}",
        json={"enabled": False},
    )
    assert disable_resp.status_code == 200
    assert "ORD-1234567890" in h.sanitize(S3)

    # H5
    enable_resp = await h.http.patch(
        f"{h.api_prefix()}/{rule_id}",
        json={"replacement": "REDACT", "enabled": True},
    )
    assert enable_resp.status_code == 200
    out = h.sanitize(S3)
    assert "REDACT" in out
    assert "ORD-1234567890" not in out


@pytest.mark.asyncio
async def test_agentserver_cold_start_loads_rules_from_gdb(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """AgentServer 初始化：``reload_log_masking_rule`` 从 GDB 加载 enabled 规则。"""
    h = log_masking_harness
    LogMaskingEngine.reset_for_tests()
    await h.bootstrap_builtin_and_sync()

    # 模拟新 AgentServer 进程冷启动
    await h.agentserver_cold_start_reload()

    out = h.sanitize(S2)
    assert "example.com" not in out
    assert "13800138000" not in out


@pytest.mark.asyncio
async def test_sanitize_does_not_break_on_bad_rule_and_empty_rows_fallback(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """§10.1 安全边界：无 enabled 行时回退 defaults；sanitize 不因单条坏规则抛错。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()

    # 全部禁用 → 引擎回退内置 defaults（仍会对 token= 等 KV 脱敏）
    for rule_id in await h.mdb_rule_ids():
        await h.http.patch(
            f"{h.api_prefix()}/{rule_id}",
            json={"enabled": False},
        )

    out = h.sanitize("api_key=sk-secretvalue")
    assert "sk-secretvalue" not in out

    poison = "x" * 5000 + ' token="'
    assert h.sanitize(poison)  # 不抛异常


@pytest.mark.parametrize("label", [spec[0] for spec in LOG_MASKING_PROBE_SAMPLES])
@pytest.mark.asyncio
async def test_probe_sample_masked_after_bootstrap(
    log_masking_harness: LogMaskingIntegrationHarness,
    label: str,
):
    """bootstrap 后 §10.4 探针样例 S1–S10 敏感片段被掩码（S3 需额外 ORD 规则）。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()

    expectation = _PROBE_SAMPLE_EXPECTATIONS[label]
    if expectation.get("requires_ord_rule"):
        await _ensure_ord_rule(h)

    text = probe_sample(label)
    out = h.sanitize(text)

    for secret in expectation["secrets"]:
        assert secret not in out, f"{label}: secret {secret!r} still visible in {out!r}"
    for fragment in expectation.get("preserved", []):
        assert fragment in out, f"{label}: expected {fragment!r} in {out!r}"
    assert "******" in out, f"{label}: expected replacement marker in {out!r}"


@pytest.mark.asyncio
async def test_all_probe_samples_masked_after_bootstrap(
    log_masking_harness: LogMaskingIntegrationHarness,
):
    """§10.4：一次性验证全部 10 条探针样例（与文档清单对齐）。"""
    h = log_masking_harness
    await h.bootstrap_builtin_and_sync()
    await _ensure_ord_rule(h)

    for label, text in LOG_MASKING_PROBE_SAMPLES:
        expectation = _PROBE_SAMPLE_EXPECTATIONS[label]
        out = h.sanitize(text)
        for secret in expectation["secrets"]:
            assert secret not in out, f"{label}: {secret!r}"
        for fragment in expectation.get("preserved", []):
            assert fragment in out, f"{label}: missing {fragment!r}"
