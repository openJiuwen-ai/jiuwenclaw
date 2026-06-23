#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证 Gateway Runtime Management 按路由上下文加载正确的 ``service_config`` 模板。

与 ``RuntimeManagementAgentClient.send_request`` / ``send_request_stream`` 使用同一套
``load_effective_enterprise_config(..., slots=[service_config])`` 读库逻辑（不依赖 K8s 池）。
加载 ``service_config`` 槽位时，还会解析命中策略上的 ``service_id`` / ``agent_id`` 模板
（如 ``${group_id}::${bot_id}``、``${user_id}``），与 Runtime 转发前写入 ``AgentRequest`` 的行为一致。

前置：已完成 ``provision-local``、``enterprise_config_demo_data_config.py`` 写入演示策略。
数据库连接与 Gateway 进程一致，由 ``manager_ws_client.infrastructure.db.Database`` 按 ``GATEWAY_*`` /
``.env`` 解析（支持 sqlite / mysql），脚本侧无需再传数据目录。

典型用法（PowerShell，项目根目录；请在仓库根 ``.env`` 中配置与 Gateway 相同的 ``GATEWAY_*``）::

    # 单场景：alice → S1 销售组 AgentServer 池（2.5.1）
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_service_config.py \\
        --group-id g_demo_sales --bot-id bot_main --user-id alice b26bc496-dfee-488b-a2ab-8bae8ce94985

    # 一次跑完文档 3.2.1–3.2.3 三个演示场景
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_service_config.py \\
        --provision-json provision.json --all-scenarios

``--provision-json`` 可自动读取 ``jiuwenclaw_id``。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceConfigExpectation:
    label: str
    template_name: str
    min_idle_services: int
    max_services: int
    policy_source: str
    service_id: str | None
    agent_id: str | None


_DEMO_SCENARIOS: dict[tuple[str, str], ServiceConfigExpectation] = {
    ("g_demo_sales", "alice"): ServiceConfigExpectation(
        label="S1 销售组 AgentServer 池",
        template_name="销售组 AgentServer 池",
        min_idle_services=2,
        max_services=10,
        policy_source="2.5.1",
        service_id="g_demo_sales::bot_main",
        agent_id="alice",
    ),
    ("g_demo_sales", "bob"): ServiceConfigExpectation(
        label="S1 销售组 AgentServer 池",
        template_name="销售组 AgentServer 池",
        min_idle_services=2,
        max_services=10,
        policy_source="继承 2.5.1",
        service_id="g_demo_sales::bot_main",
        agent_id="default_agent_id_1",
    ),
    ("g_unknown", "bob"): ServiceConfigExpectation(
        label="S2 全局兜底 AgentServer 池",
        template_name="全局兜底 AgentServer 池",
        min_idle_services=1,
        max_services=5,
        policy_source="2.7",
        service_id=None,
        agent_id=None,
    ),
}

_ALL_SCENARIO_KEYS = (
    ("g_demo_sales", "bot_main", "alice"),
    ("g_demo_sales", "bot_main", "bob"),
    ("g_unknown", "bot_main", "bob"),
)


def _configure_cli_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(message)s")
    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.INFO)
    out.setFormatter(fmt)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.ERROR)
    err.setFormatter(fmt)
    root.addHandler(out)
    root.addHandler(err)


def _load_jiuwenclaw_id_from_provision(path: Path) -> str:
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        raise ValueError(f"无法在 {path} 中找到 data 对象")
    jiuwenclaw_id = str(data.get("jiuwenclaw_id") or "").strip()
    if not jiuwenclaw_id:
        raise ValueError(f"无法在 {path} 中找到 data.jiuwenclaw_id")
    return jiuwenclaw_id


def _bootstrap_modules() -> tuple[Any, Any, Any, Any, Any]:
    from jiuwenclaw.infrastructure.module_importer import (
        ensure_manager_ws_client_package,
        import_manager_ws_client_module,
    )

    ext_root = ensure_manager_ws_client_package()
    loader_mod = import_manager_ws_client_module("core.enterprise_config.loader")
    schemas_mod = import_manager_ws_client_module("core.enterprise_config.schemas")
    utils_mod = import_manager_ws_client_module("infrastructure.utils")
    db_mod = import_manager_ws_client_module("infrastructure.db")
    gateway_db_mod = import_manager_ws_client_module("core.enterprise_config.gateway_db")
    database = db_mod.Database(relative_root=ext_root)
    return (
        loader_mod.load_effective_enterprise_config,
        schemas_mod.TemplateRefSlot.SERVICE_CONFIG,
        utils_mod.set_jiuwenclaw_id,
        database,
        gateway_db_mod,
    )


async def _ensure_gateway_database(database: Any) -> None:
    """与 Gateway / enterprise_config 读库相同：经 ``Database.ensure_ready`` 连接。"""
    await database.ensure_ready(log_prefix="enterprise_runtime_verify")
    summary = database.config_summary()
    logger.info("[db] gateway database ready: %s", summary)
    if summary.get("db_type") == "sqlite":
        sqlite_path = Path(str(summary.get("sqlite_path", "")))
        if not sqlite_path.is_file():
            logger.warning("[db] sqlite 文件不存在: %s", sqlite_path)


async def _close_gateway_databases(database: Any, gateway_db_mod: Any) -> None:
    """关闭脚本与 ``enterprise_config.gateway_db`` 各自持有的连接。"""
    enterprise_db = getattr(gateway_db_mod, "_db", None)
    for label, db in (
        ("enterprise_runtime_verify", database),
        ("enterprise_config", enterprise_db),
    ):
        if db is None:
            continue
        try:
            await db.close()
        except Exception as exc:
            logger.warning("[db] %s disconnect error: %s", label, exc)


class _RoutingRequest:
    def __init__(
        self,
        *,
        group_id: str,
        bot_id: str,
        user_id: str,
    ) -> None:
        self.params = {
            "group_id": group_id,
            "bot_id": bot_id,
            "user_id": user_id,
        }


def _log_expectation(group_id: str, user_id: str) -> ServiceConfigExpectation | None:
    expect = _DEMO_SCENARIOS.get((group_id, user_id))
    if expect is None:
        expect = _DEMO_SCENARIOS.get((group_id, "bob"))
    if expect is None:
        logger.warning("[expect] 未找到演示 seed 对照表（group_id=%s user_id=%s）", group_id, user_id)
        return None
    logger.info(
        "[expect] service_config=%s (%s); template_name=%r; min_idle=%s max_services=%s; "
        "service_id=%r agent_id=%r",
        expect.label,
        expect.policy_source,
        expect.template_name,
        expect.min_idle_services,
        expect.max_services,
        expect.service_id,
        expect.agent_id,
    )
    return expect


def _entity_matches(entity: dict[str, Any], expect: ServiceConfigExpectation) -> list[str]:
    errors: list[str] = []
    name = str(entity.get("template_name") or "")
    if name != expect.template_name:
        errors.append(f"template_name={name!r} 预期 {expect.template_name!r}")
    for field in ("min_idle_services", "max_services"):
        actual = entity.get(field)
        expected = getattr(expect, field)
        try:
            if int(actual) != int(expected):
                errors.append(f"{field}={actual} 预期 {expected}")
        except (TypeError, ValueError):
            errors.append(f"{field}={actual!r} 无法解析为整数")
    return errors


def _policy_ids_match(loaded: Any, expect: ServiceConfigExpectation) -> list[str]:
    errors: list[str] = []
    if loaded.service_id != expect.service_id:
        errors.append(
            f"service_id={loaded.service_id!r} 预期 {expect.service_id!r}"
        )
    if loaded.agent_id != expect.agent_id:
        errors.append(
            f"agent_id={loaded.agent_id!r} 预期 {expect.agent_id!r}"
        )
    return errors


async def _verify_one(
    *,
    jiuwenclaw_id: str,
    group_id: str,
    bot_id: str,
    user_id: str,
    load_fn: Any,
    service_config_slot: Any,
) -> int:
    expect = _log_expectation(group_id, user_id)
    logger.info(
        "[load] jiuwenclaw_id=%s group_id=%s bot_id=%s user_id=%s",
        jiuwenclaw_id,
        group_id,
        bot_id,
        user_id,
    )

    request = _RoutingRequest(group_id=group_id, bot_id=bot_id, user_id=user_id)
    loaded = await load_fn(request, [service_config_slot])
    if loaded is None:
        logger.error("[fail] 未加载到 service_config（策略未命中或模板不存在）")
        return 1

    entities = loaded.service_config or []
    if not entities:
        logger.error("[fail] service_config 为空 template_ref=%s", loaded.template_ref)
        return 1

    entity = entities[0]
    logger.info(
        "[loaded] template_id=%s template_name=%s min_idle_services=%s max_services=%s "
        "service_policy_id=%s global_policy_id=%s service_id=%s agent_id=%s",
        entity.get("template_id"),
        entity.get("template_name"),
        entity.get("min_idle_services"),
        entity.get("max_services"),
        loaded.service_policy_id,
        loaded.global_policy_id,
        loaded.service_id,
        loaded.agent_id,
    )

    if expect is None:
        logger.info("[ok] 已加载 service_config（无演示对照表，跳过字段断言）")
        return 0

    errors = _entity_matches(entity, expect)
    errors.extend(_policy_ids_match(loaded, expect))
    if errors:
        for err in errors:
            logger.error("[fail] %s", err)
        return 1

    logger.info("[ok] service_config 与演示 seed 预期一致（%s）", expect.label)
    return 0


async def _run(args: argparse.Namespace) -> int:
    jiuwenclaw_id = (args.jiuwenclaw_id or "").strip()

    if args.provision_json is not None and not jiuwenclaw_id:
        jiuwenclaw_id = _load_jiuwenclaw_id_from_provision(args.provision_json)

    if not jiuwenclaw_id:
        logger.error("缺少 jiuwenclaw_id（ positional 或 --provision-json）")
        return 1

    load_fn, service_config_slot, set_jiuwenclaw_id, database, gateway_db_mod = (
        _bootstrap_modules()
    )
    set_jiuwenclaw_id(jiuwenclaw_id)
    await _ensure_gateway_database(database)

    try:
        if args.all_scenarios:
            exit_code = 0
            for group_id, bot_id, user_id in _ALL_SCENARIO_KEYS:
                logger.info("")
                logger.info("=== 场景 group_id=%s user_id=%s ===", group_id, user_id)
                code = await _verify_one(
                    jiuwenclaw_id=jiuwenclaw_id,
                    group_id=group_id,
                    bot_id=bot_id,
                    user_id=user_id,
                    load_fn=load_fn,
                    service_config_slot=service_config_slot,
                )
                if code != 0:
                    exit_code = code
            return exit_code

        return await _verify_one(
            jiuwenclaw_id=jiuwenclaw_id,
            group_id=args.group_id,
            bot_id=args.bot_id,
            user_id=args.user_id,
            load_fn=load_fn,
            service_config_slot=service_config_slot,
        )
    finally:
        await _close_gateway_databases(database, gateway_db_mod)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="验证 Gateway Runtime Management 的 service_config 企业策略加载",
    )
    p.add_argument(
        "jiuwenclaw_id",
        nargs="?",
        default="",
        help="provision-local 返回的 jiuwenclaw_id（positional，建议放在命令末尾；可与 --provision-json 二选一）",
    )
    p.add_argument("--group-id", default="g_demo_sales", help="企业策略 group_id")
    p.add_argument("--bot-id", default="bot_main", help="企业策略 bot_id")
    p.add_argument("--user-id", default="alice", help="企业策略 user_id")
    p.add_argument(
        "--provision-json",
        type=Path,
        help="provision-local 响应 JSON（读取 jiuwenclaw_id）",
    )
    p.add_argument(
        "--all-scenarios",
        action="store_true",
        help="依次验证文档 3.2.1 alice / 3.2.2 bob / 3.2.3 g_unknown 三个 service_config 场景",
    )
    return p.parse_args()


def main() -> int:
    _configure_cli_logging()
    args = _parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error("[failed] %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
