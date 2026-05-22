"""企业级模型策略解析单元测试。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from jiuwenclaw.agentserver.enterprise_config.expressions import (
    agent_rule_matches,
    evaluate_match_expr,
    resolve_model_slot_ref,
    service_rule_matches,
    substitute_template,
)
from jiuwenclaw.agentserver.enterprise_config.routing import RoutingContext


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
        jiuwenclaw_id="sp-demo",
        group_id="g_demo_sales",
        bot_id="bot_main",
        user_id="alice",
        service_id="g_demo_sales::bot_main",
        agent_id="alice",
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
    assert service_rule_matches(rule, sales_ctx) is True


def test_service_rule_empty_match_expr_matches_any_group(
    sales_ctx: RoutingContext,
) -> None:
    """空 match_expr 表示全匹配；service_id 不参与选路（演示配置应避免留空）。"""
    rule = {
        "service_id": "${group_id}::${bot_id}",
        "priority": 100,
        "match_expr": "",
    }
    unknown = replace(
        sales_ctx,
        group_id="g_unknown",
        service_id="g_unknown::bot_main",
    )
    assert service_rule_matches(rule, sales_ctx) is True
    assert service_rule_matches(rule, unknown) is True


def test_service_rule_ignores_service_id_field(sales_ctx: RoutingContext) -> None:
    rule = {
        "service_id": "fixed::wrong",
        "match_expr": "group_id == 'g_demo_sales'",
    }
    assert service_rule_matches(rule, sales_ctx) is True


def test_service_rule_service_id_template_does_not_imply_match(
    sales_ctx: RoutingContext,
) -> None:
    rule = {
        "service_id": "${group_id}::${bot_id}",
        "match_expr": "group_id == 'g_unknown'",
    }
    assert service_rule_matches(rule, sales_ctx) is False


def test_service_rule_sales_group_only(sales_ctx: RoutingContext) -> None:
    rule = {
        "service_id": "${group_id}::${bot_id}",
        "priority": 100,
        "match_expr": "group_id == 'g_demo_sales'",
    }
    unknown = replace(
        sales_ctx,
        group_id="g_unknown",
        service_id="g_unknown::bot_main",
    )
    assert service_rule_matches(rule, sales_ctx) is True
    assert service_rule_matches(rule, unknown) is False


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
async def test_resolve_model_slot_ref_mapping_or_fallback(
    sales_ctx: RoutingContext,
) -> None:
    async def _lookup(
        jiuwenclaw_id: str,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> str | None:
        if group_id == "g_demo_sales":
            return "2"
        return None

    store = _MappingStore(_lookup)
    ref = await resolve_model_slot_ref(
        "${group::g_demo_sales} or 1",
        sales_ctx,
        store=store,
    )
    assert ref == "2"

    async def _lookup_user_only(
        jiuwenclaw_id: str,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> str | None:
        if user_id == "carol":
            return "4"
        return None

    ref_user = await resolve_model_slot_ref(
        "${user::carol} or 1",
        replace(sales_ctx, user_id="carol", agent_id="carol"),
        store=_MappingStore(_lookup_user_only),
    )
    assert ref_user == "4"


@pytest.mark.asyncio
async def test_resolve_model_slot_ref_rejects_nested_and_ambiguous(
    sales_ctx: RoutingContext,
) -> None:
    async def _lookup(*_args: object, **_kwargs: object) -> str | None:
        return "2"

    store = _MappingStore(_lookup)

    assert (
        await resolve_model_slot_ref(
            "${group::${group_id}} or 1",
            sales_ctx,
            store=store,
        )
        == "1"
    )
    assert (
        await resolve_model_slot_ref(
            "${g_demo_sales} or 1",
            sales_ctx,
            store=store,
        )
        == "1"
    )
    assert (
        await resolve_model_slot_ref(
            "${group_id} or 1",
            sales_ctx,
            store=store,
        )
        == "1"
    )
    async def _lookup_carol(jiuwenclaw_id: str, *, user_id: str | None = None, **_: object) -> str | None:
        return "99" if user_id == "carol" else None

    ref = await resolve_model_slot_ref(
        "${user_id} or 4",
        replace(sales_ctx, user_id="carol"),
        store=_MappingStore(_lookup_carol),
    )
    assert ref == "4"


@pytest.mark.asyncio
async def test_resolve_model_slot_ref_or_literal_when_no_mapping(
    sales_ctx: RoutingContext,
) -> None:
    async def _lookup(*_args: object, **_kwargs: object) -> str | None:
        return None

    store = _MappingStore(_lookup)

    ref = await resolve_model_slot_ref(
        "${group::g_demo_sales} or 1",
        sales_ctx,
        store=store,
    )
    assert ref == "1"
