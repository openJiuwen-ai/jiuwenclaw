#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""按《企业级claw数据模型设计.md》配置生效示例，向 Manager / Gateway 写入演示数据。

前置：已完成 ``provision-local``，Claw Manager 已启动（默认 ``http://127.0.0.1:8765``），
且目标实例 Gateway 可达。脚本经 Manager API 写入 **Gateway 库 + Manager 库**（双写，id 与 Gateway 一致）。

若升级 Manager 后首次双写失败，请删除 ``claw_manager.db`` 后重启 Manager 再执行。

执行顺序（与文档 §2.1–§2.9 一致，跳过步骤 1 provision）：

1. §2.1 五条 ``model_template``（M1–M5；2.1.1–2.1.5）
2. §2.2 三条 ``embedding-template``（B1–B3；全局、销售组、VIP）
3. §2.3 四条 ``extension-config-templates``（E1–E4）
4. §2.4 三条 ``skill-whitelist-templates``（W1–W3）
5. §2.5 两条 ``service-config-templates``（S1–S2）
6. §2.6 两条 ``config-effective/service-policies``
7. §2.7 两条 ``config-effective/agent-policies``（依赖 2.6.1 的 ``policy_id`` UUID）
8. §2.8 ``config-effective/global-policies``（可多条，运行时取 enabled 且 priority 最高者）
9. §2.9 两条 ``config-default-template-mappings``

典型用法（PowerShell，项目根目录）::

    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_config_demo_data_config.py \\
        b26bc496-dfee-488b-a2ab-8bae8ce94985

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

# 与 Gateway ``AGENT_SERVER_IMAGE`` / ``runtime_management_client`` K8s ``ContainerSpec`` 对齐
_DEMO_AGENT_SERVER_IMAGE = (
    "swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.0.45k"
)


def _demo_agent_server_base() -> dict[str, Any]:
    """``service_config`` 模板公共字段（覆盖 Runtime 中 ``cfg`` 可读的键）。"""
    return {
        "agent_image": _DEMO_AGENT_SERVER_IMAGE,
        "namespace": "jiuwenclaw",
        "container_name": "agent-server",
        "container_port": 18092,
        "port_name": "http1",
        "image_pull_policy": "IfNotPresent",
        "readiness_initial_delay": 10,
        "readiness_period": 5,
        "ready_timeout": 300,
        "ready_poll_interval": 5,
        "service_ttl": 180,
        "autoscale_interval": 5,
        "message_timeout": 60,
        "session_concurrency": 3,
        "session_ttl": 60,
    }


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
        if path.startswith(
            (
                "/model-templates",
                "/embedding-templates",
                "/extension-config-templates",
                "/skill-whitelist-templates",
                "/service-config-templates",
            )
        ):
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
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
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


def _require_policy_id(data: dict[str, Any], label: str) -> str:
    raw = data.get("policy_id")
    if raw is None or not str(raw).strip():
        raise ManagerApiError("POST", label, 200, f"响应缺少 policy_id: {data!r}")
    return str(raw).strip()


def _model_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "M1 全局兜底-经济型",
            {
                "template_name": "全局兜底-经济型",
                "description": "无服务/Agent 命中时使用",
                "model_type": ["default"],
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
                "model_type": ["default"],
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
                "model_type": ["default"],
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
                "model_type": ["default"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-group-map",
                "model_id": "gpt-4o-group-map",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
    ]


def _embedding_templates() -> list[tuple[str, dict[str, Any]]]:
    base = {
        "api_base": "https://api.openai.com/v1",
        "model_provider": "openai",
        "parameters": {"encoding_format": "float"},
        "client_config": {
            "timeout": 60,
            "retry_count": 3,
            "verify_ssl": True,
        },
        "enabled": True,
    }
    return [
        (
            "B1 全局兜底向量模型",
            {
                **base,
                "template_name": "全局兜底向量模型",
                "description": "未命中服务策略时使用",
                "embed_tags": ["memory", "global"],
                "api_key": "sk-demo-embed-global",
                "model_id": "text-embedding-3-small",
                "data": {"demo": "b1"},
            },
        ),
        (
            "B2 销售组向量模型",
            {
                **base,
                "template_name": "销售组向量模型",
                "description": "销售组记忆检索使用",
                "embed_tags": ["memory", "sales"],
                "api_key": "sk-demo-embed-sales",
                "model_id": "text-embedding-3-large",
                "parameters": {
                    "encoding_format": "float",
                    "dimensions": 1536,
                },
                "data": {"demo": "b2"},
            },
        ),
        (
            "B3 VIP 向量模型",
            {
                **base,
                "template_name": "VIP 向量模型",
                "description": "VIP 用户主动记忆使用",
                "embed_tags": ["memory", "vip"],
                "api_key": "sk-demo-embed-vip",
                "model_id": "text-embedding-3-large",
                "parameters": {
                    "encoding_format": "float",
                    "dimensions": 3072,
                },
                "data": {"demo": "b3"},
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
    base = _demo_agent_server_base()
    return [
        (
            "S1 销售组 AgentServer 池",
            {
                **base,
                "template_name": "销售组 AgentServer 池",
                "description": "销售通道 g_demo_sales 使用的 AgentServer 动态池",
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
                **base,
                "template_name": "全局兜底 AgentServer 池",
                "description": "未命中服务策略时的最小 AgentServer 池",
                "min_idle_services": 1,
                "max_services": 5,
                "service_concurrency": 10,
                "enabled": True,
                "data": {"demo": "s2"},
            },
        ),
    ]


def seed_demo_config(client: ManagerClient) -> dict[str, Any]:
    result: dict[str, Any] = {
        "jiuwenclaw_id": client.jiuwenclaw_id,
        "model_templates": {},
        "embedding_templates": {},
        "extension_config_templates": {},
        "skill_whitelist_templates": {},
        "service_config_templates": {},
    }

    logger.info("[1/9] 创建 model_template（M1–M5）")
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

    logger.info("[2/9] 创建 embedding-templates（B1–B3）")
    embedding_ids: list[str] = []
    for label, body in _embedding_templates():
        row = client.post("/embedding-templates", body)
        tid = _require_template_id(row, "/embedding-templates")
        embedding_ids.append(tid)
        key = f"b{len(embedding_ids)}"
        result["embedding_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)

    b1, b2, b3 = embedding_ids

    logger.info("[3/9] 创建 extension-config-templates（E1–E4）")
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

    logger.info("[4/9] 创建 skill-whitelist-templates（W1–W3）")
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

    logger.info("[5/9] 创建 service-config-templates（S1–S2）")
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

    logger.info("[6/9] 创建 service-policies")
    sales = client.post(
        "/config-effective/service-policies/",
        {
            "policy_name": "销售通道高优先级",
            "policy_desc": "命中 g_demo_sales 时的主服务策略",
            "service_id": "${group_id}::${bot_id}",
            "priority": 100,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {
                "default_model": [m2],
                "vision_model": [m2],
                "embedding_model": [b2],
                "skill_whitelist": [w1, w2],
                "extension_config": [e1, e2],
            },
            "enabled": True,
            "data": {"note": "服务策略匹配仅看 match_expr；service_id 仅为业务标识"},
        },
    )
    sales_id = _require_id(sales, "service-policies/sales")
    sales_policy_id = _require_policy_id(sales, "service-policies/sales")
    result["service_policy_sales_id"] = sales_id
    result["service_policy_sales_policy_id"] = sales_policy_id
    logger.info(
        "  [2.6.1] 销售通道 priority=100 -> id=%s policy_id=%s (default_model=%s, embedding=%s, skills=%s,%s, ext=%s,%s)",
        sales_id,
        sales_policy_id,
        m2,
        b2,
        w1,
        w2,
        e1,
        e2,
    )

    fallback = client.post(
        "/config-effective/service-policies/",
        {
            "policy_name": "销售组低优先级兜底",
            "service_id": "${group_id}::${bot_id}",
            "priority": 10,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
            "data": {},
        },
    )
    fallback_id = _require_id(fallback, "service-policies/fallback")
    fallback_policy_id = _require_policy_id(fallback, "service-policies/fallback")
    result["service_policy_fallback_id"] = fallback_id
    result["service_policy_fallback_policy_id"] = fallback_policy_id
    logger.info(
        "  [2.6.2] 低优先级兜底 -> id=%s policy_id=%s (default_model=%s)",
        fallback_id,
        fallback_policy_id,
        m1,
    )

    logger.info("[7/9] 创建 agent-policies")
    vip = client.post(
        "/config-effective/agent-policies/",
        {
            "policy_name": "VIP alice",
            "policy_desc": "alice 覆盖为 M3 与 B3",
            "agent_id": "${user_id}",
            "service_policy_id": sales_policy_id,
            "priority": 100,
            "match_expr": "user_id == 'alice'",
            "template_ref": {
                "default_model": [m3],
                "vision_model": [m3],
                "embedding_model": [b3],
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
    vip_policy_id = _require_policy_id(vip, "agent-policies/vip")
    result["agent_policy_vip_id"] = vip_id
    result["agent_policy_vip_policy_id"] = vip_policy_id
    logger.info(
        "  [2.7.1] VIP alice -> id=%s policy_id=%s (default_model=%s, embedding=%s, skill=%s, ext=%s)",
        vip_id,
        vip_policy_id,
        m3,
        b3,
        w1,
        e3,
    )

    mapping_rule = client.post(
        "/config-effective/agent-policies/",
        {
            "policy_name": "组映射 default_model",
            "agent_id": "default_agent_id_1",
            "service_policy_id": sales_policy_id,
            "priority": 0,
            "match_expr": "",
            "template_ref": {"default_model": [group_map_default_model]},
            "enabled": True,
            "data": {"remark": "固定 agent_id；匹配仅看 match_expr；group:: 查 2.9.2"},
        },
    )
    mapping_id = _require_id(mapping_rule, "agent-policies/mapping")
    mapping_policy_id = _require_policy_id(mapping_rule, "agent-policies/mapping")
    result["agent_policy_mapping_id"] = mapping_id
    result["agent_policy_mapping_policy_id"] = mapping_policy_id
    logger.info(
        "  [2.7.2] 组映射表达式 -> id=%s policy_id=%s (default_model=%s)",
        mapping_id,
        mapping_policy_id,
        group_map_default_model,
    )

    logger.info("[8/9] 创建 global-policies")
    global_row = client.post(
        "/config-effective/global-policies/",
        {
            "policy_name": "全局兜底",
            "policy_desc": "未命中服务/Agent 策略时使用",
            "priority": 0,
            "template_ref": {
                "default_model": [m1],
                "video_model": [m1],
                "audio_model": [m1],
                "vision_model": [m1],
                "embedding_model": [b1],
                "skill_whitelist": [w3],
                "extension_config": [e4],
            },
            "enabled": True,
            "data": {},
        },
    )
    global_id = _require_id(global_row, "global-policies")
    global_policy_uuid = _require_policy_id(global_row, "global-policies")
    result["global_policy_id"] = global_id
    result["global_policy_uuid"] = global_policy_uuid
    logger.info(
        "  [2.8] 全局兜底 -> id=%s policy_id=%s (四模型槽位=%s, embedding=%s, skill=%s, ext=%s)",
        global_id,
        global_policy_uuid,
        m1,
        b1,
        w3,
        e4,
    )

    logger.info("[9/9] 创建 config-default-template-mappings")
    carol_map = client.post(
        "/config-default-template-mappings/",
        {
            "policy_name": "carol 默认 default_model",
            "scope_type": "user",
            "scope_id": "carol",
            "priority": 0,
            "template_id": m4,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "未命中 Agent/服务策略时的用户级 default_model 映射"},
        },
    )
    carol_map_id = _require_id(carol_map, "mapping/carol")
    carol_map_policy_id = _require_policy_id(carol_map, "mapping/carol")
    result["mapping_carol_id"] = carol_map_id
    result["mapping_carol_policy_id"] = carol_map_policy_id
    logger.info(
        "  [2.9.1] user carol -> template_id=%s (id=%s policy_id=%s)",
        m4,
        carol_map_id,
        carol_map_policy_id,
    )

    group_map = client.post(
        "/config-default-template-mappings/",
        {
            "policy_name": "销售组 default_model 映射",
            "scope_type": "group",
            "scope_id": "g_demo_sales",
            "priority": 1,
            "template_id": m5,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "组级 default_model 映射，供 2.7.2 ${group::g_demo_sales} 解析"},
        },
    )
    group_map_id = _require_id(group_map, "mapping/group")
    group_map_policy_id = _require_policy_id(group_map, "mapping/group")
    result["mapping_group_id"] = group_map_id
    result["mapping_group_policy_id"] = group_map_policy_id
    logger.info(
        "  [2.9.2] group g_demo_sales -> template_id=%s (id=%s policy_id=%s)",
        m5,
        group_map_id,
        group_map_policy_id,
    )

    result["template_id_literals"] = {
        "m1": m1,
        "m2": m2,
        "m3": m3,
        "m4": m4,
        "m5": m5,
        "b1": b1,
        "b2": b2,
        "b3": b3,
    }
    return result


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="按企业级配置生效示例文档写入演示策略与模型模板",
    )
    p.add_argument(
        "jiuwenclaw_id",
        help="provision-local 返回的 jiuwenclaw_id（如 b26bc496-dfee-488b-a2ab-8bae8ce94985）",
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
        logger.error("缺少 httpx，请在 claw_manager 目录执行: uv sync\n或: pip install httpx")
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
    logger.info("    default/vision -> M3 VIP-加强对话 (2.7.1)")
    logger.info("    video/audio -> M1 全局兜底-经济型 (2.8 回填)")
    logger.info("    embedding -> B3 VIP 向量模型 (2.7.1)")
    logger.info("    skills=[W1 销售组-天气 Skill]; ext=E3 Agent Server 错误恢复（覆盖服务 E1+E2）")
    logger.info("  bob   + g_demo_sales::bot_main")
    logger.info("    default -> M5 销售组映射专用 (2.7.2 + 2.9.2)")
    logger.info("    vision -> M2 销售组-标准型 (继承 2.6.1)")
    logger.info("    video/audio -> M1 全局兜底-经济型 (2.8 回填)")
    logger.info("    embedding -> B2 销售组向量模型 (继承 2.6.1)")
    logger.info("    skills=[W1 销售组-天气 Skill, W2 销售组-CRM Skill] 继承 2.6.1")
    logger.info("    ext=E1 Gateway 请求前鉴权 + E2 Gateway 请求后日志（继承 2.6.1）")
    logger.info("  g_unknown::bot_main")
    logger.info("    default/vision/video/audio -> M1 全局兜底-经济型 (全局兜底)")
    logger.info("    embedding -> B1 全局兜底向量模型 (全局兜底)")
    logger.info("    skill=W3 全局兜底 Skill; ext=E4 Gateway 定时清理")
    logger.info("")
    logger.info("Gateway Runtime service_config 验证（§3.2）：")
    logger.info(
        "  uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_service_config.py "
        "--all-scenarios %s",
        summary.get("jiuwenclaw_id", "{JIUWENCLAW_ID}"),
    )
    logger.info("")
    logger.info("AgentServer 聊天联调（§3.1）：")
    logger.info(
        "  uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_config_chat.py "
        "--group-id g_demo_sales --bot-id bot_main --user-id alice "
        "--web-port {WEB_PORT}"
    )

    if args.json_out:
        logger.info("%s", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
