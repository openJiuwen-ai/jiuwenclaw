"""企业级模型策略解析单元测试。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from jiuwenclaw.infrastructure.module_importer import (
    import_manager_ws_client_module,
)


def _load_manager_ws_utils() -> Any:
    return import_manager_ws_client_module("infrastructure.utils")


from jiuwenclaw.agentserver.enterprise_config.loader import (
    DEFAULT_AGENT_LOAD_SLOTS,
    TemplateRefSlot,
    gateway_db,
    load_effective_enterprise_config,
    schemas,
)
from jiuwenclaw.schema.agent import AgentRequest

_utils = _load_manager_ws_utils()
_gateway_db_mod = import_manager_ws_client_module("core.enterprise_config.gateway_db")
GatewayDb = _gateway_db_mod.GatewayDb
expressions = import_manager_ws_client_module("core.enterprise_config.expressions")


def _bind_gateway_db(monkeypatch: pytest.MonkeyPatch, jiuwenclaw_id: str) -> GatewayDb:
    """与生产一致：设置 JIUWENCLAW_ID 并 bind GatewayDb（import 时 bind 的 id 可能为空）。"""
    monkeypatch.setenv("JIUWENCLAW_ID", jiuwenclaw_id)
    return GatewayDb.bind(jiuwenclaw_id)


def _patch_gateway_queries(
    monkeypatch: pytest.MonkeyPatch,
    db: GatewayDb,
    *,
    list_records: Any,
    fetch_template_by_slot: Any,
) -> None:
    monkeypatch.setattr(db, "list_records", list_records)
    monkeypatch.setattr(db, "fetch_template_by_slot", fetch_template_by_slot)

RoutingContext = schemas.RoutingContext
normalize_template_ref = _utils.normalize_template_ref
fill_missing_template_ref_slots = _utils.fill_missing_template_ref_slots
evaluate_match_expr = expressions.evaluate_match_expr
resolve_slot_template_id_map = expressions.resolve_slot_template_id_map
resolve_template_slot_ref = expressions.resolve_template_slot_ref
substitute_template = expressions.substitute_template

_FALLBACK_TEMPLATE_ID = "11111111-1111-4111-8111-111111111111"


class _MappingStore:
    """测试用的映射 store，模拟模板映射查询。"""

    def __init__(self, lookup_fn: Any) -> None:
        self._lookup_fn = lookup_fn

    async def lookup_template_mapping_ref(
        self,
        jiuwenclaw_id: str,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> str | None:
        return await self._lookup_fn(
            jiuwenclaw_id, user_id=user_id, group_id=group_id
        )


@pytest.fixture
def sales_ctx() -> RoutingContext:
    return RoutingContext(
        group_id="g_demo_sales",
        bot_id="bot_main",
        user_id="alice",
    )


def test_normalize_template_ref_accepts_list() -> None:
    assert normalize_template_ref(None) == {}
    assert normalize_template_ref(
        {
            "default_model": ["f2222222-2222-4222-8222-222222222202"],
            "vision_model": ["f2222222-2222-4222-8222-222222222202"],
            "skill_whitelist": [
                "a1000001-0000-4000-8000-000000000001",
                "abc",
            ],
        }
    ) == {
        "default_model": ["f2222222-2222-4222-8222-222222222202"],
        "vision_model": ["f2222222-2222-4222-8222-222222222202"],
        "skill_whitelist": [
            "a1000001-0000-4000-8000-000000000001",
            "abc",
        ],
    }


def test_normalize_template_ref_rejects_string_slot_value() -> None:
    with pytest.raises(ValueError, match="must be a list"):
        normalize_template_ref(
            {"default_model": "f2222222-2222-4222-8222-222222222202"},
        )


@pytest.mark.asyncio
async def test_resolve_slot_template_id_map_resolves_each_array_item(
    sales_ctx: RoutingContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _list_records(*_args: object, **_kwargs: object) -> list[dict]:
        return []

    monkeypatch.setattr(gateway_db, "list_records", _list_records)

    refs = {
        "default_model": [f"${{group::g_demo_sales}} or {_FALLBACK_TEMPLATE_ID}"],
        "skill_whitelist": [
            "a1000001-0000-4000-8000-000000000001",
            "abc",
        ],
    }
    resolved = await resolve_slot_template_id_map(refs, sales_ctx)
    assert resolved == {
        "default_model": [_FALLBACK_TEMPLATE_ID],
        "skill_whitelist": [
            "a1000001-0000-4000-8000-000000000001",
            "abc",
        ],
    }


def test_substitute_service_id(sales_ctx: RoutingContext) -> None:
    assert (
        substitute_template("${group_id}::${bot_id}", sales_ctx)
        == "g_demo_sales::bot_main"
    )
    assert (
        substitute_template("${group_id} : : ${bot_id}", sales_ctx)
        == "g_demo_sales : : bot_main"
    )


def test_service_rule_priority_match(sales_ctx: RoutingContext) -> None:
    rule = {
        "service_id": "${group_id}::${bot_id}",
        "priority": 100,
        "match_expr": "group_id == 'g_demo_sales'",
    }
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is True


def test_service_rule_empty_match_expr_matches_any_group(
    sales_ctx: RoutingContext,
) -> None:
    """空 match_expr 表示全匹配；service_id 不参与选路（演示配置应避免留空）。"""
    rule = {
        "service_id": "${group_id}::${bot_id}",
        "priority": 100,
        "match_expr": "",
    }
    unknown = replace(sales_ctx, group_id="g_unknown")
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is True
    assert evaluate_match_expr(rule.get("match_expr"), unknown) is True


def test_service_rule_ignores_service_id_field(sales_ctx: RoutingContext) -> None:
    rule = {
        "service_id": "fixed::wrong",
        "match_expr": "group_id == 'g_demo_sales'",
    }
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is True


def test_service_rule_service_id_template_does_not_imply_match(
    sales_ctx: RoutingContext,
) -> None:
    rule = {
        "service_id": "${group_id}::${bot_id}",
        "match_expr": "group_id == 'g_unknown'",
    }
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is False


def test_service_rule_sales_group_only(sales_ctx: RoutingContext) -> None:
    rule = {
        "service_id": "${group_id}::${bot_id}",
        "priority": 100,
        "match_expr": "group_id == 'g_demo_sales'",
    }
    unknown = replace(sales_ctx, group_id="g_unknown")
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is True
    assert evaluate_match_expr(rule.get("match_expr"), unknown) is False


def test_agent_rule_user_match(sales_ctx: RoutingContext) -> None:
    rule = {
        "agent_id": "${user_id}",
        "match_expr": "user_id == 'alice'",
    }
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is True
    assert evaluate_match_expr(rule.get("match_expr"), replace(sales_ctx, user_id="bob")) is False


def test_agent_rule_agent_id_template_does_not_filter_match(
    sales_ctx: RoutingContext,
) -> None:
    """``agent_id`` 模板不参与匹配，仅 ``match_expr`` 决定命中。"""
    rule = {
        "agent_id": "${group_id}",
        "match_expr": "",
    }
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is True
    assert evaluate_match_expr(rule.get("match_expr"), replace(sales_ctx, user_id="bob")) is True


def test_agent_rule_fixed_agent_id_does_not_filter_match(sales_ctx: RoutingContext) -> None:
    rule = {
        "agent_id": "default_agent_id_1",
        "match_expr": "",
    }
    assert evaluate_match_expr(rule.get("match_expr"), sales_ctx) is True
    assert evaluate_match_expr(rule.get("match_expr"), replace(sales_ctx, user_id="bob")) is True


def test_match_expr_empty_is_true(sales_ctx: RoutingContext) -> None:
    assert evaluate_match_expr("", sales_ctx) is True
    assert evaluate_match_expr(None, sales_ctx) is True


@pytest.mark.asyncio
async def test_resolve_template_slot_ref_mapping_or_fallback(
    sales_ctx: RoutingContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _list_records(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        if table == "config_default_template_mapping" and (filters or {}).get("group_id") == "g_demo_sales":
            return [{"template_id": "2"}]
        return []

    monkeypatch.setattr(gateway_db, "list_records", _list_records)

    ref = await resolve_template_slot_ref(
        f"${{group::g_demo_sales}} or {_FALLBACK_TEMPLATE_ID}",
        sales_ctx,
    )
    assert ref == "2"

    async def _list_records_user(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        if table == "config_default_template_mapping" and (filters or {}).get("user_id") == "carol":
            return [{"template_id": "4"}]
        return []

    monkeypatch.setattr(gateway_db, "list_records", _list_records_user)
    ref_user = await resolve_template_slot_ref(
        f"${{user::carol}} or {_FALLBACK_TEMPLATE_ID}",
        replace(sales_ctx, user_id="carol"),
    )
    assert ref_user == "4"


@pytest.mark.asyncio
async def test_resolve_template_slot_ref_rejects_nested_and_ambiguous(
    sales_ctx: RoutingContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _list_records(*_args: object, **_kwargs: object) -> list[dict]:
        return [{"template_id": "2"}]

    monkeypatch.setattr(gateway_db, "list_records", _list_records)

    assert (
        await resolve_template_slot_ref(
            f"${{group::${{group_id}}}} or {_FALLBACK_TEMPLATE_ID}",
            sales_ctx,
        )
        == _FALLBACK_TEMPLATE_ID
    )
    assert (
        await resolve_template_slot_ref(
            f"${{g_demo_sales}} or {_FALLBACK_TEMPLATE_ID}",
            sales_ctx,
        )
        == _FALLBACK_TEMPLATE_ID
    )
    assert (
        await resolve_template_slot_ref(
            f"${{group_id}} or {_FALLBACK_TEMPLATE_ID}",
            sales_ctx,
        )
        == _FALLBACK_TEMPLATE_ID
    )

    async def _list_records_carol(
        table: str,
        *,
        filters: dict | None = None,
        **_: object,
    ) -> list[dict]:
        if table == "config_default_template_mapping" and (filters or {}).get("user_id") == "carol":
            return [{"template_id": "99"}]
        return []

    monkeypatch.setattr(gateway_db, "list_records", _list_records_carol)
    assert (
        await resolve_template_slot_ref(
            f"${{user_id}} or 44444444-4444-4444-8444-444444444444",
            replace(sales_ctx, user_id="carol"),
        )
        == "44444444-4444-4444-8444-444444444444"
    )


@pytest.mark.asyncio
async def test_resolve_template_slot_ref_or_literal_when_no_mapping(
    sales_ctx: RoutingContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _list_records(*_args: object, **_kwargs: object) -> list[dict]:
        return []

    monkeypatch.setattr(gateway_db, "list_records", _list_records)

    ref = await resolve_template_slot_ref(
        f"${{group::g_demo_sales}} or {_FALLBACK_TEMPLATE_ID}",
        sales_ctx,
    )
    assert ref == _FALLBACK_TEMPLATE_ID


def test_fill_missing_template_ref_slots() -> None:
    merged = {
        "default_model": ["m3"],
        "vision_model": ["m2"],
    }
    global_refs = {
        "default_model": ["m1"],
        "video_model": ["m1"],
        "audio_model": ["m1"],
        "skill_whitelist": ["w3"],
    }
    out = fill_missing_template_ref_slots(merged, global_refs)
    assert out == {
        "default_model": ["m3"],
        "vision_model": ["m2"],
        "video_model": ["m1"],
        "audio_model": ["m1"],
        "skill_whitelist": ["w3"],
    }


@pytest.mark.asyncio
async def test_load_effective_config_fills_missing_slots_from_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jid = "sp-demo"
    db = _bind_gateway_db(monkeypatch, jid)

    m2 = "22222222-2222-4222-8222-222222222222"
    m5 = "55555555-5555-4555-8555-555555555555"
    m1 = "11111111-1111-4111-8111-111111111111"
    w1, w2, w3 = "w1", "w2", "w3"
    e1, e2, e4 = "e1", "e2", "e4"

    async def _list_records(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        scoped = db.apply_instance_scope(table, dict(filters or {}))
        if table == "config_effective_service_policy":
            if scoped.get("jiuwenclaw_id") not in (None, jid):
                return []
            return [
                {
                    "id": 1,
                    "jiuwenclaw_id": jid,
                    "match_expr": "group_id == 'g_demo_sales'",
                    "template_ref": {
                        "default_model": [m2],
                        "vision_model": [m2],
                        "skill_whitelist": [w1, w2],
                        "extension_config": [e1, e2],
                    },
                }
            ]
        if table == "config_effective_agent_policy":
            if scoped.get("jiuwenclaw_id") not in (None, jid):
                return []
            return [
                {
                    "id": 10,
                    "jiuwenclaw_id": jid,
                    "agent_id": "${user_id}",
                    "match_expr": "",
                    "template_ref": {
                        "default_model": [f"${{group::g_demo_sales}} or {m1}"],
                    },
                }
            ]
        if table == "config_effective_global_policy":
            if scoped.get("jiuwenclaw_id") == jid:
                return [
                    {
                        "id": 99,
                        "jiuwenclaw_id": jid,
                        "template_ref": {
                            "default_model": [m1],
                            "vision_model": [m1],
                            "video_model": [m1],
                            "audio_model": [m1],
                            "skill_whitelist": [w3],
                            "extension_config": [e4],
                        },
                    }
                ]
            if scoped.get("jiuwenclaw_id") == "sp-other":
                return [
                    {
                        "id": 1,
                        "jiuwenclaw_id": "sp-other",
                        "template_ref": {"extension_config": ["e3-old"]},
                    }
                ]
            return []
        if table == "config_default_template_mapping":
            if scoped.get("group_id") == "g_demo_sales":
                return [{"template_id": m5}]
        return []

    async def _fetch_template_by_slot(slot: str, template_id: str) -> dict | None:
        return {"template_id": template_id, "model_id": template_id, "slot": slot}

    _patch_gateway_queries(
        monkeypatch,
        db,
        list_records=_list_records,
        fetch_template_by_slot=_fetch_template_by_slot,
    )

    request = AgentRequest(
        request_id="req-test-bob",
        params={
            "group_id": "g_demo_sales",
            "bot_id": "bot_main",
            "user_id": "bob",
        },
    )
    loaded = await load_effective_enterprise_config(
        request,
        DEFAULT_AGENT_LOAD_SLOTS,
    )
    assert loaded is not None
    assert loaded.template_ref["default_model"] == [m5]
    assert loaded.template_ref["vision_model"] == [m2]
    assert loaded.template_ref["video_model"] == [m1]
    assert loaded.template_ref["audio_model"] == [m1]
    assert loaded.template_ref["skill_whitelist"] == [w1, w2]
    assert loaded.template_ref["extension_config"] == [e1, e2]
    assert loaded.global_policy_id == 99


@pytest.mark.asyncio
async def test_load_effective_config_scopes_global_policy_by_jiuwenclaw_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _bind_gateway_db(monkeypatch, "sp-current")
    e4 = "44444444-4444-4444-8444-444444444444"
    e3_old = "33333333-3333-4333-8333-333333333333"
    m1 = "11111111-1111-4111-8111-111111111111"
    w3 = "w3"

    async def _list_records(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        scoped = db.apply_instance_scope(table, dict(filters or {}))
        if table == "config_effective_service_policy":
            return []
        if table == "config_effective_global_policy":
            jid = scoped.get("jiuwenclaw_id")
            if jid == "sp-current":
                return [
                    {
                        "id": 4,
                        "jiuwenclaw_id": "sp-current",
                        "template_ref": {
                            "default_model": [m1],
                            "vision_model": [m1],
                            "video_model": [m1],
                            "audio_model": [m1],
                            "skill_whitelist": [w3],
                            "extension_config": [e4],
                        },
                    }
                ]
            return []
        return []

    async def _fetch_template_by_slot(slot: str, template_id: str) -> dict | None:
        return {
            "template_id": template_id,
            "template_name": "Gateway 定时清理" if template_id == e4 else "Agent Server 错误恢复",
            "component": "gateway" if template_id == e4 else "agent_server",
            "slot": slot,
        }

    _patch_gateway_queries(
        monkeypatch,
        db,
        list_records=_list_records,
        fetch_template_by_slot=_fetch_template_by_slot,
    )

    request = AgentRequest(
        request_id="req-test-unknown",
        params={
            "group_id": "g_unknown",
            "bot_id": "bot_main",
            "user_id": "bob",
        },
    )
    loaded = await load_effective_enterprise_config(
        request,
        DEFAULT_AGENT_LOAD_SLOTS,
    )
    assert loaded is not None
    assert loaded.global_policy_id == 4
    assert loaded.template_ref["extension_config"] == [e4]
    assert loaded.extension_config is not None
    assert loaded.extension_config[0]["template_name"] == "Gateway 定时清理"


@pytest.mark.asyncio
async def test_load_service_config_returns_resolved_service_and_agent_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jid = "sp-demo"
    db = _bind_gateway_db(monkeypatch, jid)
    s1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    async def _list_records(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        scoped = db.apply_instance_scope(table, dict(filters or {}))
        if table == "config_effective_service_policy":
            if scoped.get("jiuwenclaw_id") not in (None, jid):
                return []
            return [
                {
                    "id": 1,
                    "jiuwenclaw_id": jid,
                    "service_id": "${group_id}::${bot_id}",
                    "match_expr": "group_id == 'g_demo_sales'",
                    "template_ref": {"service_config": [s1]},
                }
            ]
        if table == "config_effective_agent_policy":
            if scoped.get("jiuwenclaw_id") not in (None, jid):
                return []
            return [
                {
                    "id": 10,
                    "jiuwenclaw_id": jid,
                    "agent_id": "${user_id}",
                    "match_expr": "user_id == 'alice'",
                    "template_ref": {},
                }
            ]
        if table == "config_effective_global_policy":
            if scoped.get("jiuwenclaw_id") == jid:
                return [
                    {
                        "id": 99,
                        "jiuwenclaw_id": jid,
                        "template_ref": {"service_config": ["global-s2"]},
                    }
                ]
            return []
        return []

    async def _fetch_template_by_slot(slot: str, template_id: str) -> dict | None:
        return {
            "template_id": template_id,
            "template_name": "demo-pool",
            "min_idle_services": 2,
            "max_services": 10,
        }

    _patch_gateway_queries(
        monkeypatch,
        db,
        list_records=_list_records,
        fetch_template_by_slot=_fetch_template_by_slot,
    )

    alice_request = AgentRequest(
        request_id="req-alice",
        params={
            "group_id": "g_demo_sales",
            "bot_id": "bot_main",
            "user_id": "alice",
        },
    )
    alice_loaded = await load_effective_enterprise_config(
        alice_request,
        [TemplateRefSlot.SERVICE_CONFIG],
    )
    assert alice_loaded is not None
    assert alice_loaded.service_id == "g_demo_sales::bot_main"
    assert alice_loaded.agent_id == "alice"

    bob_request = AgentRequest(
        request_id="req-bob",
        params={
            "group_id": "g_demo_sales",
            "bot_id": "bot_main",
            "user_id": "bob",
        },
    )
    bob_loaded = await load_effective_enterprise_config(
        bob_request,
        [TemplateRefSlot.SERVICE_CONFIG],
    )
    assert bob_loaded is not None
    assert bob_loaded.service_id == "g_demo_sales::bot_main"
    assert bob_loaded.agent_id is None
