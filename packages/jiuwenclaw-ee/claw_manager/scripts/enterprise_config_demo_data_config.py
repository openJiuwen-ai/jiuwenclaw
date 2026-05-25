#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""按《企业级claw数据模型设计.md》配置生效示例，向 Manager / Gateway 写入演示数据。

前置：已完成 ``provision-local``，Claw Manager 已启动（默认 ``http://127.0.0.1:8765``），
且目标实例 Gateway 可达。脚本经 Manager API 写入 **Gateway 库 + Manager 库**（双写，id 与 Gateway 一致）。

若升级 Manager 后首次双写失败，请删除 ``claw_manager.db`` 后重启 Manager 再执行。

执行顺序（与文档一致，跳过步骤 0 provision）：

1. 五条 ``model_template``（M1–M5；每次执行均新建，``template_name`` 可重复）
2. 四条 ``extension-config-templates``（E1–E4；每次执行均新建）
3. 三条 ``skill-whitelist-templates``（W1–W3；每次执行均新建）
4. 两条 ``service-config-templates``（S1–S2；每次执行均新建）
5. 两条 ``config-effective/service-policies``（``template_ref`` 含模型 / 白名单 / 扩展 / 服务配置槽位）
6. 两条 ``config-effective/agent-policies``（依赖上一步销售服务策略 id）
7. ``config-effective/global-policies``（每实例唯一；已存在则 PUT 更新）
8. 两条 ``config-default-template-mappings``

典型用法（PowerShell 一行）::

    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_config_demo_data_config.py sp-xxxxxxxxxxxx

可选环境变量 ``CLAWMANAGER_BASE_URL`` 覆盖 Manager 根地址。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError as _httpx_import_error:  # pragma: no cover
    httpx = None  # type: ignore[assignment,misc]
else:
    _httpx_import_error = None


def _configure_cli_logging() -> None:
    """INFO 走 stdout，ERROR 走 stderr。"""
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


class ManagerApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {path} -> HTTP {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class SeedDemoConfigError(RuntimeError):
    """演示种子数据写入前置条件不满足或业务校验失败。"""


class ManagerClient:
    def __init__(self, base_url: str, jiuwenclaw_id: str, *, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._jid = jiuwenclaw_id.strip()
        self._timeout = timeout
        if not self._jid:
            raise ValueError("jiuwenclaw_id 不能为空")

    @property
    def jiuwenclaw_id(self) -> str:
        return self._jid

    @property
    def base_url(self) -> str:
        return self._base

    def _url(self, path: str) -> str:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            return f"{self._base}/api/v1{path}"
        return f"{self._base}/api/v1/instances/{self._jid}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path)
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.request(method, url, json=json_body)
        if resp.status_code >= 400:
            detail = resp.text.strip()
            try:
                payload = resp.json()
                detail = json.dumps(payload, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            raise ManagerApiError(method, path, resp.status_code, detail)
        if not resp.content:
            return {}
        data = resp.json()
        if not isinstance(data, dict):
            raise ManagerApiError(method, path, resp.status_code, f"非 JSON 对象: {data!r}")
        code = data.get("code", 200)
        if code not in (200, None):
            raise ManagerApiError(
                method,
                path,
                resp.status_code,
                f"code={code} message={data.get('message')!r}",
            )
        inner = data.get("data")
        if inner is None:
            return {}
        if not isinstance(inner, dict):
            return {"value": inner}
        return inner

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, json_body=body)

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", path, json_body=body)

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def list_items(self, path: str, *, page_size: int = 50) -> list[dict[str, Any]]:
        data = self.get(f"{path}?page=1&page_size={page_size}")
        items = data.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
        return []


def _require_id(data: dict[str, Any], label: str) -> int:
    raw = data.get("id")
    if raw is None:
        raise ManagerApiError("POST", label, 200, f"响应缺少 id: {data!r}")
    return int(raw)


def _require_template_id(data: dict[str, Any], label: str) -> str:
    raw = data.get("template_id")
    if raw is None or not str(raw).strip():
        raise ManagerApiError("POST", label, 200, f"响应缺少 template_id: {data!r}")
    return str(raw).strip()


def _model_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "M1 全局兜底-经济型",
            {
                "template_name": "全局兜底-经济型",
                "description": "无服务/Agent 命中时使用",
                "model_type": "default",
                "model_tags": ["chat"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-global",
                "model_id": "gpt-4o-mini",
                "model_provider": "openai",
                "parameters": {"temperature": 0.7, "max_tokens": 4096},
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M2 销售组-标准型",
            {
                "template_name": "销售组-标准型",
                "model_type": "default",
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-sales",
                "model_id": "gpt-4o",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M3 VIP-加强对话",
            {
                "template_name": "VIP-加强对话",
                "model_type": ["default", "vision"],
                "model_tags": ["chat", "vision"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-vip",
                "model_id": "gpt-5",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M4 Carol 默认映射模型",
            {
                "template_name": "Carol 默认映射模型",
                "model_type": "default",
                "api_base": "https://api.deepseek.com/v1",
                "api_key": "sk-demo-carol",
                "model_id": "deepseek-v3",
                "model_provider": "deepseek",
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M5 销售组映射专用",
            {
                "template_name": "销售组映射专用",
                "model_type": "default",
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-group-map",
                "model_id": "gpt-4o-group-map",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
    ]


def _extension_config_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "E1 Gateway 请求前鉴权",
            {
                "template_name": "Gateway 请求前鉴权",
                "description": "请求前参数校验与权限检查（gateway）",
                "component": "gateway",
                "hook_type": "pre_request",
                "hook_config": {
                    "handler": "hooks.auth.pre_request",
                    "params": {"require_token": True, "allowed_roles": ["user", "admin"]},
                },
                "custom_config": {"auth_header": "Authorization"},
                "enabled": True,
                "data": {"demo": "e1"},
            },
        ),
        (
            "E2 Gateway 请求后日志",
            {
                "template_name": "Gateway 请求后日志",
                "description": "请求完成后记录访问日志（gateway）",
                "component": "gateway",
                "hook_type": "post_request",
                "hook_config": {
                    "handler": "hooks.logging.post_request",
                    "params": {"log_level": "info", "include_body": False},
                },
                "custom_config": {},
                "enabled": True,
                "data": {"demo": "e2"},
            },
        ),
        (
            "E3 Agent Server 错误恢复",
            {
                "template_name": "Agent Server 错误恢复",
                "description": "请求失败时告警与降级（agent_server）",
                "component": "agent_server",
                "hook_type": "error",
                "hook_config": {
                    "handler": "hooks.recovery.on_error",
                    "params": {"notify_channel": "demo-alerts", "max_retries": 1},
                },
                "custom_config": {"fallback_message": "服务暂时不可用，请稍后重试"},
                "enabled": True,
                "data": {"demo": "e3"},
            },
        ),
        (
            "E4 Gateway 定时清理",
            {
                "template_name": "Gateway 定时清理",
                "description": "定时清理临时缓存与会话残留（gateway）",
                "component": "gateway",
                "hook_type": "schedule",
                "hook_config": {
                    "handler": "hooks.maintenance.cleanup",
                    "schedule": "0 */5 * * *",
                    "params": {"ttl_seconds": 3600},
                },
                "custom_config": {"workspace": "demo"},
                "enabled": True,
                "data": {"demo": "e4"},
            },
        ),
    ]


def _skill_whitelist_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "W1 销售组-天气 Skill",
            {
                "template_name": "销售组-天气 Skill",
                "description": "销售通道允许 search/weather",
                "skill_id": "search/weather",
                "skill_version": "1.2.0",
                "skill_source": "https://skillhub.example.com/",
                "enabled": True,
                "data": {"demo": "w1"},
            },
        ),
        (
            "W2 销售组-CRM Skill",
            {
                "template_name": "销售组-CRM Skill",
                "description": "销售通道允许 crm/lead_lookup",
                "skill_id": "crm/lead_lookup",
                "skill_version": "2.0.1",
                "skill_source": "https://skillhub.example.com/",
                "enabled": True,
                "data": {"demo": "w2"},
            },
        ),
        (
            "W3 全局兜底 Skill",
            {
                "template_name": "全局兜底 Skill",
                "description": "未命中服务策略时的最小 Skill 白名单",
                "skill_id": "search/weather",
                "skill_version": "1.0.0",
                "skill_source": "https://skillhub.example.com/",
                "enabled": True,
                "data": {"demo": "w3"},
            },
        ),
    ]


def _service_config_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "S1 销售组 AgentServer 池",
            {
                "template_name": "销售组 AgentServer 池",
                "description": "销售通道 g_demo_sales 使用的 AgentServer 动态池",
                "agent_image": "jiuwenclaw/agent-server:latest",
                "namespace": "jiuwenclaw",
                "container_name": "agent-server",
                "container_port": 8080,
                "min_idle_services": 2,
                "max_services": 10,
                "service_concurrency": 5,
                "enabled": True,
                "data": {"demo": "s1"},
            },
        ),
        (
            "S2 全局兜底 AgentServer 池",
            {
                "template_name": "全局兜底 AgentServer 池",
                "description": "未命中服务策略时的最小 AgentServer 池",
                "agent_image": "jiuwenclaw/agent-server:latest",
                "namespace": "jiuwenclaw",
                "container_name": "agent-server",
                "container_port": 8080,
                "min_idle_services": 1,
                "max_services": 5,
                "enabled": True,
                "data": {"demo": "s2"},
            },
        ),
    ]


def _upsert_global_policy(client: ManagerClient, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return client.post("/config-effective/global-policies", body)
    except ManagerApiError as exc:
        if exc.status != 400 or "already exists" not in exc.detail.lower():
            raise
    rows = client.list_items("/config-effective/global-policies", page_size=5)
    if not rows:
        raise SeedDemoConfigError(
            "全局策略创建失败且列表为空，请检查 Manager / Gateway 日志。"
        )
    policy_id = int(rows[0]["id"])
    logger.info("  [global] 已存在，PUT 更新 id=%s", policy_id)
    return client.put(f"/config-effective/global-policies/{policy_id}", body)


def seed_demo_config(client: ManagerClient) -> dict[str, Any]:
    result: dict[str, Any] = {
        "jiuwenclaw_id": client.jiuwenclaw_id,
        "model_templates": {},
        "extension_config_templates": {},
        "skill_whitelist_templates": {},
        "service_config_templates": {},
    }

    logger.info("[1/8] 创建 model_template（M1–M5）")
    template_ids: list[str] = []
    for label, body in _model_templates():
        row = client.post("/model-templates", body)
        tid = _require_template_id(row, "/model-templates")
        template_ids.append(tid)
        key = f"m{len(template_ids)}"
        result["model_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)

    m1, m2, m3, m4, m5 = template_ids
    group_map_default_model = f"${{group::g_demo_sales}} or {m1}"

    logger.info("[2/8] 创建 extension-config-templates（E1–E4）")
    extension_ids: list[str] = []
    for label, body in _extension_config_templates():
        row = client.post("/extension-config-templates", body)
        tid = _require_template_id(row, "/extension-config-templates")
        extension_ids.append(tid)
        key = f"e{len(extension_ids)}"
        result["extension_config_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)

    e1, e2, e3, e4 = extension_ids
    result["extension_template_id_literals"] = {
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "e4": e4,
    }

    logger.info("[3/8] 创建 skill-whitelist-templates（W1–W3）")
    whitelist_ids: list[str] = []
    for label, body in _skill_whitelist_templates():
        row = client.post("/skill-whitelist-templates", body)
        tid = _require_template_id(row, "/skill-whitelist-templates")
        whitelist_ids.append(tid)
        key = f"w{len(whitelist_ids)}"
        result["skill_whitelist_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)

    w1, w2, w3 = whitelist_ids
    result["skill_whitelist_template_id_literals"] = {
        "w1": w1,
        "w2": w2,
        "w3": w3,
    }

    logger.info("[4/8] 创建 service-config-templates（S1–S2）")
    service_config_ids: list[str] = []
    for label, body in _service_config_templates():
        row = client.post("/service-config-templates", body)
        tid = _require_template_id(row, "/service-config-templates")
        service_config_ids.append(tid)
        key = f"s{len(service_config_ids)}"
        result["service_config_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)

    s1, s2 = service_config_ids
    result["service_config_template_id_literals"] = {
        "s1": s1,
        "s2": s2,
    }

    logger.info("[5/8] 创建 service-policies")
    sales = client.post(
        "/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 100,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {
                "default_model": [m2],
                "vision_model": [m2],
                "skill_whitelist": [w1, w2],
                "extension_config": [e1, e2],
                "service_config": [s1],
            },
            "enabled": True,
            "data": {
                "note": "服务策略匹配仅看 match_expr；service_id 仅为业务标识"
            },
        },
    )
    sales_id = _require_id(sales, "service-policies/sales")
    result["service_policy_sales_id"] = sales_id
    logger.info(
        "  [2.1] 销售通道 priority=100 -> id=%s (default_model=%s, skills=%s,%s, ext=%s,%s, service=%s)",
        sales_id,
        m2,
        w1,
        w2,
        e1,
        e2,
        s1,
    )

    fallback = client.post(
        "/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 10,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
            "data": {},
        },
    )
    fallback_id = _require_id(fallback, "service-policies/fallback")
    result["service_policy_fallback_id"] = fallback_id
    logger.info("  [2.2] 低优先级兜底 -> id=%s (default_model=%s)", fallback_id, m1)

    logger.info("[6/8] 创建 agent-policies")
    vip = client.post(
        "/config-effective/agent-policies",
        {
            "agent_id": "${user_id}",
            "service_policy_id": sales_id,
            "priority": 100,
            "match_expr": "user_id == 'alice'",
            "template_ref": {
                "default_model": [m3],
                "vision_model": [m3],
                "skill_whitelist": [w1],
                "extension_config": [e3],
            },
            "enabled": True,
            "data": {
                "demo_context": {
                    "group_id": "g_demo_sales",
                    "bot_id": "bot_main",
                    "user_id": "alice",
                }
            },
        },
    )
    vip_id = _require_id(vip, "agent-policies/vip")
    result["agent_policy_vip_id"] = vip_id
    logger.info(
        "  [3.1] VIP alice -> id=%s (default_model=%s, skill=%s, ext=%s)",
        vip_id,
        m3,
        w1,
        e3,
    )

    mapping_rule = client.post(
        "/config-effective/agent-policies",
        {
            "agent_id": "${user_id}",
            "service_policy_id": sales_id,
            "priority": 0,
            "match_expr": "",
            "template_ref": {"default_model": [group_map_default_model]},
            "enabled": True,
            "data": {
                "remark": f"group:: 仅按 group_id 查步骤 5.2；or 右侧 {m1} 为 M1 的 template_id 回退"
            },
        },
    )
    mapping_id = _require_id(mapping_rule, "agent-policies/mapping")
    result["agent_policy_mapping_id"] = mapping_id
    logger.info(
        "  [3.2] 组映射表达式 -> id=%s (default_model=%s)",
        mapping_id,
        group_map_default_model,
    )

    logger.info("[7/8] 创建 / 更新 global-policies")
    global_row = _upsert_global_policy(
        client,
        {
            "priority": 0,
            "template_ref": {
                "default_model": [m1],
                "video_model": [m1],
                "audio_model": [m1],
                "vision_model": [m1],
                "skill_whitelist": [w3],
                "extension_config": [e4],
                "service_config": [s2],
            },
            "enabled": True,
            "data": {},
        },
    )
    global_id = _require_id(global_row, "global-policies")
    result["global_policy_id"] = global_id
    logger.info(
        "  [4] 全局兜底 -> id=%s (四槽位=%s, skill=%s, ext=%s, service=%s)",
        global_id,
        m1,
        w3,
        e4,
        s2,
    )

    logger.info("[8/8] 创建 config-default-template-mappings")
    carol_map = client.post(
        "/config-default-template-mappings",
        {
            "user_id": "carol",
            "group_id": None,
            "priority": 0,
            "template_id": m4,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "未命中 Agent/服务策略时的用户级 default_model 映射"},
        },
    )
    carol_map_id = _require_id(carol_map, "mapping/carol")
    result["mapping_carol_id"] = carol_map_id
    logger.info("  [5.1] user carol -> template_id=%s (id=%s)", m4, carol_map_id)

    group_map = client.post(
        "/config-default-template-mappings",
        {
            "user_id": None,
            "group_id": "g_demo_sales",
            "priority": 0,
            "template_id": m5,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "组级 default_model 映射，供 3.2 ${group::g_demo_sales} 解析"},
        },
    )
    group_map_id = _require_id(group_map, "mapping/group")
    result["mapping_group_id"] = group_map_id
    logger.info("  [5.2] group g_demo_sales -> template_id=%s (id=%s)", m5, group_map_id)

    result["template_id_literals"] = {"m1": m1, "m2": m2, "m3": m3, "m4": m4, "m5": m5}
    return result


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="按企业级配置生效示例文档写入演示策略与模型模板",
    )
    p.add_argument(
        "jiuwenclaw_id",
        help="provision-local 返回的 jiuwenclaw_id（如 sp-xxxxxxxxxxxx）",
    )
    p.add_argument(
        "--manager-base",
        default=os.environ.get("CLAWMANAGER_BASE_URL", "http://127.0.0.1:8765"),
        help="Claw Manager 根 URL（默认 http://127.0.0.1:8765 或 CLAWMANAGER_BASE_URL）",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="单次 HTTP 请求超时（秒）",
    )
    p.add_argument(
        "--json-out",
        action="store_true",
        help="完成后在 stdout 打印完整结果 JSON",
    )
    return p.parse_args()


def main() -> None:
    _configure_cli_logging()
    if _httpx_import_error is not None:
        logger.error(
            "缺少 httpx，请在 claw_manager 目录执行: uv sync\n或: pip install httpx"
        )
        sys.exit(1)

    args = _parse_args()
    client = ManagerClient(args.manager_base, args.jiuwenclaw_id, timeout=args.timeout)
    logger.info("[seed] jiuwenclaw_id=%s manager=%s", client.jiuwenclaw_id, client.base_url)

    try:
        summary = seed_demo_config(client)
    except SeedDemoConfigError as exc:
        logger.error("[failed] %s", exc)
        raise SystemExit(1) from exc
    except ManagerApiError as exc:
        logger.error("[failed] %s", exc)
        raise SystemExit(1) from exc
    except httpx.ConnectError as exc:
        logger.error("[connect-failed] %s", exc)
        logger.error(
            "请确认 Claw Manager 已在 %s 启动，且实例 %s 已 provision。",
            args.manager_base,
            args.jiuwenclaw_id,
        )
        raise SystemExit(1) from exc

    logger.info("")
    logger.info("[done] 演示配置已写入。预期解析（各模型槽位可不同）：")
    logger.info("  alice + g_demo_sales::bot_main")
    logger.info("    default/vision -> M3 VIP-加强对话 (Agent 3.1)")
    logger.info("    video/audio -> M1 全局兜底-经济型 (全局 4 回填)")
    logger.info(
        "    skills=[W1 销售组-天气 Skill]; ext=E3 Agent Server 错误恢复（覆盖服务 E1+E2）"
    )
    logger.info("  bob   + g_demo_sales::bot_main")
    logger.info("    default -> M5 销售组映射专用 (Agent 3.2 + 映射 5.2)")
    logger.info("    vision -> M2 销售组-标准型 (继承服务 2.1)")
    logger.info("    video/audio -> M1 全局兜底-经济型 (全局 4 回填)")
    logger.info("    skills=[W1 销售组-天气 Skill, W2 销售组-CRM Skill] 继承服务级")
    logger.info("    ext=E1 Gateway 请求前鉴权 + E2 Gateway 请求后日志（继承服务 2.1）")
    logger.info("  g_unknown::bot_main")
    logger.info("    default/vision/video/audio -> M1 全局兜底-经济型 (全局兜底)")
    logger.info("    skill=W3 全局兜底 Skill; ext=E4 Gateway 定时清理")
    logger.info("")
    logger.info("Gateway Runtime service_config 验证（§7）：")
    logger.info(
        "  uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_service_config.py "
        "--all-scenarios %s",
        summary.get("jiuwenclaw_id", "{JIUWENCLAW_ID}"),
    )
    logger.info("")
    logger.info("AgentServer 聊天联调（§6）：")
    logger.info(
        "  uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_config_chat.py "
        "--group-id g_demo_sales --bot-id bot_main --user-id alice "
        "--web-port {WEB_PORT}"
    )

    if args.json_out:
        logger.info("%s", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
