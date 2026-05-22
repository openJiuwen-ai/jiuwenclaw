"""企业级模型策略解析单元测试。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from jiuwenclaw.agentserver.enterprise_config.expressions import (
    agent_rule_matches,
    evaluate_match_expr,
    resolve_template_slot_ref,
    substitute_template,
)
from jiuwenclaw.agentserver.enterprise_config.schemas import RoutingContext

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


def test_substitute_service_id(sales_ctx: RoutingContext) -> None:
    assert (
        substitute_template("${group_id}::${bot_id}", sales_ctx)
        == "g_demo_sales::bot_main"
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
    assert agent_rule_matches(rule, sales_ctx) is True
    assert agent_rule_matches(rule, replace(sales_ctx, user_id="bob")) is False


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

    from jiuwenclaw.agentserver.enterprise_config import gateway_db

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
    from jiuwenclaw.agentserver.enterprise_config import gateway_db

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

    from jiuwenclaw.agentserver.enterprise_config import gateway_db

    monkeypatch.setattr(gateway_db, "list_records", _list_records)

    ref = await resolve_template_slot_ref(
        f"${{group::g_demo_sales}} or {_FALLBACK_TEMPLATE_ID}",
        sales_ctx,
    )
    assert ref == _FALLBACK_TEMPLATE_ID
