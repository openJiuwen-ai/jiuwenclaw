"""企业级模型策略解析单元测试。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from jiuwenswarm.server.runtime.enterprise_config.expressions import (
    agent_rule_matches,
    evaluate_match_expr,
    resolve_slot_template_id_map,
    resolve_template_slot_ref,
    substitute_template,
)
from jiuwenswarm.server.runtime.enterprise_config.schemas import (
    RoutingContext,
    normalize_template_ref,
)

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

    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

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

    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

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
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

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

    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    monkeypatch.setattr(gateway_db, "list_records", _list_records)

    ref = await resolve_template_slot_ref(
        f"${{group::g_demo_sales}} or {_FALLBACK_TEMPLATE_ID}",
        sales_ctx,
    )
    assert ref == _FALLBACK_TEMPLATE_ID


@pytest.mark.asyncio
async def test_load_effective_config_by_instance_agent_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db
    from jiuwenswarm.server.runtime.enterprise_config.loader import (
        DEFAULT_AGENT_LOAD_SLOTS,
        load_effective_enterprise_config,
    )
    from jiuwenswarm.common.schema.agent import AgentRequest

    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-demo")

    m2 = "22222222-2222-4222-8222-222222222222"
    m1 = "11111111-1111-4111-8111-111111111111"
    w1, w2 = "w1", "w2"
    e1, e2 = "e1", "e2"
    jid = "sp-demo"
    resource_id = "bot_main"
    ref_template_id = "agent-tmpl-1"

    async def _list_records(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        scoped = dict(filters or {})
        if table == "instance_agent_resource":
            if scoped.get("resource_id") != resource_id:
                return []
            return [
                {
                    "jiuwenclaw_id": jid,
                    "resource_id": resource_id,
                    "resource_name": "main",
                    "ref_template_id": ref_template_id,
                    "grants": [{"match_expr": "", "enabled": True}],
                }
            ]
        if table == "agent_template":
            if scoped.get("template_id") != ref_template_id:
                return []
            return [
                {
                    "jiuwenclaw_id": jid,
                    "template_id": ref_template_id,
                    "enabled": True,
                    "template_ref": {
                        "default_model": [m2],
                        "vision_model": [m2],
                        "video_model": [m1],
                        "audio_model": [m1],
                        "skill_whitelist": [w1, w2],
                        "extension_config": [e1, e2],
                    },
                    "data": {"workspace_dir": "/ws/bot_main"},
                }
            ]
        return []

    async def _fetch_template_by_slot(slot: str, template_id: str) -> dict | None:
        return {"template_id": template_id, "model_id": template_id, "slot": slot}

    monkeypatch.setattr(gateway_db, "list_records", _list_records)
    monkeypatch.setattr(gateway_db, "fetch_template_by_slot", _fetch_template_by_slot)

    request = AgentRequest(
        request_id="req-test-bob",
        params={},
        metadata={
            "routing": {
                "group_id": "g_demo_sales",
                "bot_id": resource_id,
                "user_id": "bob",
            }
        },
    )
    loaded = await load_effective_enterprise_config(
        request,
        DEFAULT_AGENT_LOAD_SLOTS,
    )
    assert loaded is not None
    assert loaded.resource_id == resource_id
    assert loaded.ref_template_id == ref_template_id
    assert loaded.agent_id == resource_id
    assert loaded.workspace_dir == "/ws/bot_main"
    assert loaded.template_ref["default_model"] == [m2]
    assert loaded.template_ref["vision_model"] == [m2]
    assert loaded.template_ref["video_model"] == [m1]
    assert loaded.template_ref["audio_model"] == [m1]
    assert loaded.template_ref["skill_whitelist"] == [w1, w2]
    assert loaded.template_ref["extension_config"] == [e1, e2]


@pytest.mark.asyncio
async def test_load_effective_config_skips_mapping_expr_in_template_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db
    from jiuwenswarm.server.runtime.enterprise_config.loader import (
        DEFAULT_AGENT_LOAD_SLOTS,
        load_effective_enterprise_config,
    )
    from jiuwenswarm.common.schema.agent import AgentRequest

    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    monkeypatch.setenv("JIUWENCLAW_ID", "sp-current")
    e4 = "44444444-4444-4444-8444-444444444444"
    m1 = "11111111-1111-4111-8111-111111111111"
    resource_id = "bot_main"
    ref_template_id = "agent-tmpl-ext"

    async def _list_records(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        scoped = dict(filters or {})
        if table == "instance_agent_resource":
            if scoped.get("resource_id") != resource_id:
                return []
            return [
                {
                    "jiuwenclaw_id": "sp-current",
                    "resource_id": resource_id,
                    "ref_template_id": ref_template_id,
                    "grants": [{"match_expr": "", "enabled": True}],
                }
            ]
        if table == "agent_template":
            if scoped.get("template_id") != ref_template_id:
                return []
            return [
                {
                    "jiuwenclaw_id": "sp-current",
                    "template_id": ref_template_id,
                    "enabled": True,
                    "template_ref": {
                        "default_model": [f"${{group::g_demo_sales}} or {m1}"],
                        "extension_config": [e4],
                    },
                }
            ]
        return []

    async def _fetch_template_by_slot(slot: str, template_id: str) -> dict | None:
        return {
            "template_id": template_id,
            "template_name": "Gateway 定时清理" if template_id == e4 else "other",
            "component": "gateway" if template_id == e4 else "agent_server",
            "slot": slot,
        }

    monkeypatch.setattr(gateway_db, "list_records", _list_records)
    monkeypatch.setattr(gateway_db, "fetch_template_by_slot", _fetch_template_by_slot)

    request = AgentRequest(
        request_id="req-test-unknown",
        params={},
        metadata={
            "routing": {
                "group_id": "g_unknown",
                "bot_id": resource_id,
                "user_id": "bob",
            }
        },
    )
    loaded = await load_effective_enterprise_config(
        request,
        DEFAULT_AGENT_LOAD_SLOTS,
    )
    assert loaded is not None
    assert "default_model" not in loaded.template_ref
    assert loaded.template_ref["extension_config"] == [e4]
    assert loaded.extension_config is not None
    assert loaded.extension_config[0]["template_name"] == "Gateway 定时清理"


def test_embedding_slot_is_loaded_separately_from_model_slots() -> None:
    from jiuwenswarm.server.runtime.enterprise_config.schemas import (
        DEFAULT_AGENT_LOAD_SLOTS,
        MODEL_SLOT_KEYS,
        SLOT_ENTITY_TABLE,
        TemplateRefSlot,
    )

    assert TemplateRefSlot.EMBEDDING_MODEL.value == "embedding_model"
    assert SLOT_ENTITY_TABLE[TemplateRefSlot.EMBEDDING_MODEL] == "embedding_template"
    assert TemplateRefSlot.EMBEDDING_MODEL in DEFAULT_AGENT_LOAD_SLOTS
    assert TemplateRefSlot.EMBEDDING_MODEL not in MODEL_SLOT_KEYS


def test_enterprise_embedding_maps_to_embed_config_section() -> None:
    from jiuwenswarm.server.runtime.enterprise_config.apply_models import (
        apply_enterprise_models_to_config,
    )
    from jiuwenswarm.server.runtime.enterprise_config.schemas import (
        EffectiveEnterpriseConfig,
        RoutingContext,
    )

    enterprise = EffectiveEnterpriseConfig(
        routing=RoutingContext(group_id="g", bot_id="b", user_id="u"),
        embedding=[
            {
                "api_key": "policy-key",
                "api_base": "https://embedding.example.com/v1",
                "model_id": "text-embedding-3-large",
            }
        ],
    )

    merged, applied = apply_enterprise_models_to_config(
        {
            "embed": {
                "embed_api_key": "local-key",
                "embed_base_url": "https://local.example.com/v1",
                "embed_model": "local-model",
            }
        },
        enterprise,
    )

    assert applied is True
    assert merged["embed"] == {
        "embed_api_key": "policy-key",
        "embed_base_url": "https://embedding.example.com/v1",
        "embed_model": "text-embedding-3-large",
    }


def test_get_embed_config_prefers_db_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.agents.harness.common.memory import config as memory_config

    monkeypatch.setattr(
        memory_config,
        "_load_config",
        lambda: {
            "embed": {
                "embed_api_key": "local-key",
                "embed_base_url": "https://local.example.com/v1",
                "embed_model": "local-model",
            }
        },
    )
    memory_config.clear_embed_config_db_cache()
    assert memory_config.get_embed_config()["api_key"] == "local-key"

    memory_config.set_embed_config_db_cache(
        {
            "api_key": "policy-key",
            "api_base": "https://policy.example.com/v1",
            "model_id": "policy-model",
        }
    )
    assert memory_config.get_embed_config() == {
        "api_key": "policy-key",
        "base_url": "https://policy.example.com/v1",
        "model": "policy-model",
    }

    memory_config.clear_embed_config_db_cache()
    assert memory_config.get_embed_config()["api_key"] == "local-key"


@pytest.mark.asyncio
async def test_load_effective_config_loads_embedding_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db
    from jiuwenswarm.server.runtime.enterprise_config.loader import (
        DEFAULT_AGENT_LOAD_SLOTS,
        load_effective_enterprise_config,
    )
    from jiuwenswarm.server.runtime.enterprise_config.schemas import TemplateRefSlot

    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    jid = "embedding-demo"
    monkeypatch.setenv("JIUWENCLAW_ID", jid)
    embedding_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    resource_id = "bot_embed"
    ref_template_id = "agent-tmpl-embed"

    async def _list_records(
        table: str,
        *,
        filters: dict | None = None,
        order_by: str = "",
    ) -> list[dict]:
        scoped = dict(filters or {})
        if table == "instance_agent_resource":
            if scoped.get("resource_id") != resource_id:
                return []
            return [
                {
                    "jiuwenclaw_id": jid,
                    "resource_id": resource_id,
                    "ref_template_id": ref_template_id,
                    "grants": [{"match_expr": "", "enabled": True}],
                }
            ]
        if table == "agent_template":
            if scoped.get("template_id") != ref_template_id:
                return []
            return [
                {
                    "jiuwenclaw_id": jid,
                    "template_id": ref_template_id,
                    "enabled": True,
                    "template_ref": {"embedding_model": [embedding_id]},
                }
            ]
        return []

    async def _fetch_template_by_slot(slot: str, template_id: str) -> dict | None:
        assert slot == TemplateRefSlot.EMBEDDING_MODEL
        assert template_id == embedding_id
        return {
            "template_id": template_id,
            "api_base": "https://embedding.example.com/v1",
            "api_key": "secret",
            "model_id": "text-embedding-3-large",
        }

    monkeypatch.setattr(gateway_db, "list_records", _list_records)
    monkeypatch.setattr(gateway_db, "fetch_template_by_slot", _fetch_template_by_slot)

    loaded = await load_effective_enterprise_config(
        AgentRequest(
            request_id="req-embedding",
            params={},
            metadata={"routing": {"bot_id": resource_id}},
        ),
        DEFAULT_AGENT_LOAD_SLOTS,
    )

    assert loaded is not None
    assert loaded.embedding == [
        {
            "template_id": embedding_id,
            "api_base": "https://embedding.example.com/v1",
            "api_key": "secret",
            "model_id": "text-embedding-3-large",
        }
    ]
