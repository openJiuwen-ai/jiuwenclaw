#!/usr/bin/env python3
"""一键给 Claw Manager 灌入贴近真实的 demo 数据。

包含：
  - 3 个组网实例（华北 / 华东 / 华南），不同 namespace / group / space
  - 每个实例上报多条心跳：Gateway + 多个 AgentServer + Worker（实例 3 故意只一个 Gateway 离线）
  - 8 个 Model Templates（OpenAI / DeepSeek / Anthropic / QianFan / 智谱）
  - 4 个 Extension Config Templates（pre/post/error/schedule）
  - 实例 1 完整四级策略 + 默认模板映射
  - 实例 2 一套精简策略

用法（两阶段，因为 templates / policies 走 manager 的 ws 双写到 gateway）：

  # phase 1: 创建实例 + 心跳（不需要任何 gateway 连接）
  python3 scripts/seed_demo_full.py --phase 1

  # 中间在另一个终端拉起 mock gateway：
  python3 scripts/mock_gateway_ws.py

  # phase 2: 创建 templates + policies + mappings
  python3 scripts/seed_demo_full.py --phase 2

也可以一把梭：默认 --phase all 会同时跑两步；前提是脚本运行前 mock_gateway_ws.py
已经监听好（或者运行后会自动失败，请按上面分两步）。

只依赖标准库；要求 Manager 已启动。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

BASE = os.environ.get("CLAWMANAGER_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
TIMEOUT = float(os.environ.get("CLAWMANAGER_TIMEOUT", "30"))


# --------------------------------------------------------------------------- #
# minimal HTTP helper (stdlib only)
# --------------------------------------------------------------------------- #


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else exc.reason
        raise SystemExit(f"[fail] {method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise SystemExit(f"[fail] cannot reach {url}: {exc.reason}") from exc

    payload = json.loads(raw) if raw else {}
    if isinstance(payload, dict) and "code" in payload and "data" in payload:
        if payload.get("code") not in (200, None):
            raise SystemExit(f"[fail] {method} {path}: code={payload.get('code')} message={payload.get('message')!r}")
        return payload.get("data") or {}
    return payload


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    return _call("POST", path, body)


def put(path: str, body: dict[str, Any]) -> dict[str, Any]:
    return _call("PUT", path, body)


def get(path: str) -> dict[str, Any]:
    return _call("GET", path)


# --------------------------------------------------------------------------- #
# fixture data
# --------------------------------------------------------------------------- #


def _instances() -> list[dict[str, Any]]:
    return [
        {
            "jiuwenclaw_name": "prod-cn-north",
            "description": "华北生产组网（北京可用区）",
            "k8s_master_host": "https://k8s-cn-north.demo.internal:6443",
            "k8s_auth_type": "token",
            "k8s_auth_config": {"token": "demo-token-cn-north", "ca": "<ca-cert>"},
            "k8s_namespace": "jiuwenclaw-prod-cn-north",
            "resource_quota": {"cpu": "32c", "memory": "64Gi", "gpu": 4},
            "creator_id": "ops_alice",
            "group_id": "g_prod",
            "space_id": "sp_global",
            "management_api_base": "http://10.10.0.11:18080",
        },
        {
            "jiuwenclaw_name": "prod-cn-east",
            "description": "华东生产组网（上海可用区）",
            "k8s_master_host": "https://k8s-cn-east.demo.internal:6443",
            "k8s_auth_type": "kubeconfig",
            "k8s_auth_config": {"kubeconfig": "<base64-kubeconfig>"},
            "k8s_namespace": "jiuwenclaw-prod-cn-east",
            "resource_quota": {"cpu": "24c", "memory": "48Gi", "gpu": 2},
            "creator_id": "ops_alice",
            "group_id": "g_prod",
            "space_id": "sp_global",
            "management_api_base": "http://10.20.0.11:18080",
        },
        {
            "jiuwenclaw_name": "staging-cn-south",
            "description": "华南灰度组网（深圳）",
            "k8s_master_host": "https://k8s-cn-south-staging.demo.internal:6443",
            "k8s_auth_type": "kubeconfig",
            "k8s_auth_config": {"kubeconfig": "<base64-kubeconfig>"},
            "k8s_namespace": "jiuwenclaw-staging-cn-south",
            "resource_quota": {"cpu": "8c", "memory": "16Gi"},
            "creator_id": "ops_bob",
            "group_id": "g_staging",
            "space_id": "sp_global",
            "management_api_base": "http://10.30.0.11:18080",
        },
    ]


def _model_templates() -> list[dict[str, Any]]:
    return [
        {
            "template_name": "全局兜底-经济型",
            "description": "未命中其他策略时使用，便宜稳定",
            "model_type": "default",
            "model_tags": ["chat", "fallback", "cn"],
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-demo-global-fallback",
            "model_id": "gpt-4o-mini",
            "model_provider": "openai",
            "parameters": {"temperature": 0.7, "max_tokens": 4096, "top_p": 1.0},
            "timeout": 60,
            "retry_count": 3,
            "enable_streaming": True,
            "enable_function_calling": True,
            "verify_ssl": True,
            "enabled": True,
            "data": {"owner": "platform", "cost_per_million_tokens": 0.6},
        },
        {
            "template_name": "销售组-标准型",
            "description": "销售部门常规对话",
            "model_type": "default",
            "model_tags": ["chat", "sales"],
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-demo-sales-standard",
            "model_id": "gpt-4o",
            "model_provider": "openai",
            "parameters": {"temperature": 0.4, "max_tokens": 8192},
            "timeout": 90,
            "retry_count": 2,
            "enable_streaming": True,
            "enable_function_calling": True,
            "verify_ssl": True,
            "enabled": True,
            "data": {"owner": "sales", "sla": "p95<800ms"},
        },
        {
            "template_name": "VIP-加强对话",
            "description": "VIP 用户专用，质量优先",
            "model_type": ["default", "vision"],
            "model_tags": ["chat", "vision", "vip"],
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-demo-vip-premium",
            "model_id": "gpt-5",
            "model_provider": "openai",
            "parameters": {"temperature": 0.2, "max_tokens": 16384},
            "timeout": 120,
            "retry_count": 1,
            "enable_streaming": True,
            "enable_function_calling": True,
            "verify_ssl": True,
            "enabled": True,
            "data": {"owner": "vip-ops"},
        },
        {
            "template_name": "DeepSeek 中文模型",
            "description": "中文长上下文场景",
            "model_type": "default",
            "model_tags": ["chat", "cn", "long-context"],
            "api_base": "https://api.deepseek.com/v1",
            "api_key": "sk-demo-deepseek",
            "model_id": "deepseek-v3",
            "model_provider": "deepseek",
            "parameters": {"temperature": 0.5, "max_tokens": 32768},
            "timeout": 90,
            "retry_count": 2,
            "enable_streaming": True,
            "enable_function_calling": True,
            "verify_ssl": True,
            "enabled": True,
            "data": {"owner": "platform"},
        },
        {
            "template_name": "Claude 推理",
            "description": "复杂推理 / 代码生成",
            "model_type": "default",
            "model_tags": ["chat", "reasoning", "code"],
            "api_base": "https://api.anthropic.com/v1",
            "api_key": "sk-ant-demo",
            "model_id": "claude-sonnet-4-5",
            "model_provider": "anthropic",
            "parameters": {"temperature": 0.2, "max_tokens": 8192},
            "timeout": 120,
            "retry_count": 1,
            "enable_streaming": True,
            "enable_function_calling": True,
            "verify_ssl": True,
            "enabled": True,
            "data": {"owner": "platform"},
        },
        {
            "template_name": "通义千问",
            "description": "国产合规中文场景",
            "model_type": "default",
            "model_tags": ["chat", "cn", "compliance"],
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-demo-qwen",
            "model_id": "qwen-plus",
            "model_provider": "qwen",
            "parameters": {"temperature": 0.5, "max_tokens": 8192},
            "timeout": 60,
            "retry_count": 2,
            "enable_streaming": True,
            "enable_function_calling": True,
            "verify_ssl": True,
            "enabled": True,
            "data": {"owner": "platform"},
        },
        {
            "template_name": "智谱 GLM",
            "description": "智谱清言中文场景，关闭流式以适配老客户端",
            "model_type": "default",
            "model_tags": ["chat", "cn"],
            "api_base": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": "sk-demo-glm",
            "model_id": "glm-4.5",
            "model_provider": "zhipu",
            "parameters": {"temperature": 0.6, "max_tokens": 8192},
            "timeout": 90,
            "retry_count": 2,
            "enable_streaming": False,
            "enable_function_calling": True,
            "verify_ssl": True,
            "enabled": True,
            "data": {"owner": "platform"},
        },
        {
            "template_name": "视觉-小型",
            "description": "前端低成本视觉理解",
            "model_type": "vision",
            "model_tags": ["vision"],
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-demo-vision",
            "model_id": "gpt-4o-vision",
            "model_provider": "openai",
            "parameters": {"temperature": 0.2},
            "timeout": 90,
            "retry_count": 2,
            "enable_streaming": False,
            "enable_function_calling": False,
            "verify_ssl": True,
            "enabled": False,
            "data": {"owner": "platform", "note": "禁用待评估"},
        },
    ]


def _extension_templates() -> list[dict[str, Any]]:
    return [
        {
            "template_name": "Gateway 请求前鉴权",
            "description": "请求前 token 校验与权限检查",
            "component": "gateway",
            "hook_type": "pre_request",
            "hook_config": {
                "handler": "hooks.auth.pre_request",
                "params": {
                    "require_token": True,
                    "allowed_roles": ["user", "vip", "admin"],
                    "deny_paths": ["/internal/*"],
                },
            },
            "custom_config": {"auth_header": "Authorization", "max_skew_seconds": 60},
            "enabled": True,
            "data": {"owner": "security"},
        },
        {
            "template_name": "Gateway 请求后访问日志",
            "description": "结构化访问日志写到 Loki",
            "component": "gateway",
            "hook_type": "post_request",
            "hook_config": {
                "handler": "hooks.logging.post_request",
                "params": {
                    "log_level": "info",
                    "include_body": False,
                    "loki_endpoint": "http://loki.observability:3100/loki/api/v1/push",
                },
            },
            "custom_config": {"redact_fields": ["password", "api_key"]},
            "enabled": True,
            "data": {"owner": "observability"},
        },
        {
            "template_name": "AgentServer 错误恢复",
            "description": "模型超时/限流时退避重试 + 告警",
            "component": "agent_server",
            "hook_type": "error",
            "hook_config": {
                "handler": "hooks.recovery.on_error",
                "params": {
                    "notify_channel": "alerts-feishu",
                    "max_retries": 2,
                    "backoff_ms": [500, 1500],
                    "fallback_model_template": "全局兜底-经济型",
                },
            },
            "custom_config": {"fallback_message": "服务繁忙，请稍后重试"},
            "enabled": True,
            "data": {"owner": "platform"},
        },
        {
            "template_name": "Gateway 定时清理",
            "description": "每 5 分钟清理过期 session 与临时上传",
            "component": "gateway",
            "hook_type": "schedule",
            "hook_config": {
                "handler": "hooks.maintenance.cleanup",
                "schedule": "*/5 * * * *",
                "params": {
                    "session_ttl_seconds": 3600,
                    "upload_ttl_seconds": 7200,
                    "dry_run": False,
                },
            },
            "custom_config": {"workspace": "prod"},
            "enabled": True,
            "data": {"owner": "platform"},
        },
    ]


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _list_instance_ids() -> list[str]:
    payload = get("/api/v1/instances?page=1&page_size=200")
    items = payload.get("items") or []
    return [str(it["jiuwenclaw_id"]) for it in items if it.get("jiuwenclaw_id")]


def phase_instances() -> list[str]:
    print(f"[seed] target = {BASE}")
    health = get("/api/health")
    if health.get("status") != "ok":
        raise SystemExit(f"[fail] manager not healthy: {health}")
    print("[seed] manager /api/health OK")

    existing = _list_instance_ids()
    if existing:
        print(f"[seed] existing instances detected ({len(existing)}); skip instance phase")
        return existing

    print("\n[1/1] creating 3 instances …")
    instance_ids: list[str] = []
    for spec in _instances():
        body = dict(spec)
        out = post("/api/v1/instances", body)
        jid = out["jiuwenclaw_id"]
        instance_ids.append(jid)
        print(f"  + {spec['jiuwenclaw_name']:>18}  -> {jid}  ns={spec['k8s_namespace']}")

    print("[seed] gateway online status: run mock_gateway_ws.py or connect real gateways via WS")

    return instance_ids


def phase_templates(instance_ids: list[str]) -> None:
    if len(instance_ids) < 2:
        raise SystemExit("[fail] need at least 2 instances to seed templates; run --phase 1 first")

    # 3. model templates
    print("\n[3/5] creating model templates …")
    model_ids: dict[str, str] = {}
    for spec in _model_templates():
        out = post("/api/v1/model-templates", spec)
        tid = out["template_id"]
        model_ids[spec["template_name"]] = tid
        print(f"  + {spec['template_name']:>18}  -> {tid}  provider={spec['model_provider']}")

    # 4. extension templates
    print("\n[4/5] creating extension config templates …")
    extension_ids: dict[str, str] = {}
    for spec in _extension_templates():
        out = post("/api/v1/extension-config-templates", spec)
        tid = out["template_id"]
        extension_ids[spec["template_name"]] = tid
        print(f"  + {spec['template_name']:>22}  -> {tid}  hook={spec['hook_type']}")

    # 5. policies on instance[0] (full) + instance[1] (simplified)
    print("\n[5/5] creating policies for instance[0] (full) + instance[1] (simplified) …")

    full = instance_ids[0]
    t_fallback = model_ids["全局兜底-经济型"]
    t_sales = model_ids["销售组-标准型"]
    t_vip = model_ids["VIP-加强对话"]
    t_ds = model_ids["DeepSeek 中文模型"]
    t_qwen = model_ids["通义千问"]

    sales_policy = post(
        f"/api/v1/instances/{full}/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 100,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": t_sales, "vision_model": t_sales},
            "enabled": True,
            "data": {"note": "销售组主策略"},
        },
    )
    sales_policy_id = int(sales_policy["id"])
    print(f"  + service-policy[sales] id={sales_policy_id}")

    fallback_policy = post(
        f"/api/v1/instances/{full}/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 10,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": t_fallback},
            "enabled": True,
            "data": {"note": "销售组兜底"},
        },
    )
    print(f"  + service-policy[sales-fallback] id={int(fallback_policy['id'])}")

    vip_agent_policy = post(
        f"/api/v1/instances/{full}/config-effective/agent-policies",
        {
            "agent_id": "${user_id}",
            "service_policy_id": sales_policy_id,
            "priority": 100,
            "match_expr": "user_id == 'alice'",
            "template_ref": {"default_model": t_vip, "vision_model": t_vip},
            "enabled": True,
            "data": {"demo_context": {"group_id": "g_demo_sales", "bot_id": "bot_main", "user_id": "alice"}},
        },
    )
    print(f"  + agent-policy[vip-alice] id={int(vip_agent_policy['id'])}")

    group_agent_policy = post(
        f"/api/v1/instances/{full}/config-effective/agent-policies",
        {
            "agent_id": "${user_id}",
            "service_policy_id": sales_policy_id,
            "priority": 0,
            "match_expr": "",
            "template_ref": {"default_model": f"${{group::g_demo_sales}} or {t_fallback}"},
            "enabled": True,
            "data": {"remark": "组映射 or 全局兜底"},
        },
    )
    print(f"  + agent-policy[group-map] id={int(group_agent_policy['id'])}")

    global_policy = post(
        f"/api/v1/instances/{full}/config-effective/global-policies",
        {
            "priority": 0,
            "template_ref": {
                "default_model": t_fallback,
                "video_model": t_fallback,
                "audio_model": t_fallback,
                "vision_model": t_fallback,
            },
            "enabled": True,
            "data": {},
        },
    )
    print(f"  + global-policy id={int(global_policy['id'])}")

    # default mappings
    carol_map = post(
        f"/api/v1/instances/{full}/config-default-template-mappings",
        {
            "user_id": "carol",
            "priority": 0,
            "template_id": t_ds,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "用户 Carol 偏好 DeepSeek"},
        },
    )
    print(f"  + default-mapping[user=carol] id={int(carol_map['id'])}")

    group_map = post(
        f"/api/v1/instances/{full}/config-default-template-mappings",
        {
            "group_id": "g_demo_sales",
            "priority": 0,
            "template_id": t_qwen,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "销售组使用通义千问"},
        },
    )
    print(f"  + default-mapping[group=g_demo_sales] id={int(group_map['id'])}")

    # instance[1] - simplified
    half = instance_ids[1]
    half_policy = post(
        f"/api/v1/instances/{half}/config-effective/global-policies",
        {
            "priority": 0,
            "template_ref": {"default_model": t_ds},
            "enabled": True,
            "data": {},
        },
    )
    print(f"  + (instance[1]) global-policy id={int(half_policy['id'])}")

    half_map = post(
        f"/api/v1/instances/{half}/config-default-template-mappings",
        {
            "group_id": "g_prod_qa",
            "priority": 0,
            "template_id": t_qwen,
            "template_type": "default_model",
            "enabled": True,
            "data": {},
        },
    )
    print(f"  + (instance[1]) default-mapping id={int(half_map['id'])}")

    # summary
    print("\n[done] templates / policies seeded.")
    print(f"  model_templates: {len(model_ids)}")
    print(f"  extension_templates: {len(extension_ids)}")
    print("  policies: instance[0] full, instance[1] simplified, instance[2] empty")
    print(f"  hint: refresh web ({BASE}) — every page should now show data.")
    _ = time.time()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=["1", "2", "all"],
        default="all",
        help="1=instances; 2=templates+policies (needs mock_gateway_ws.py for online status); "
        "all=do both back-to-back",
    )
    args = parser.parse_args()

    if args.phase in ("1", "all"):
        ids = phase_instances()
    else:
        ids = _list_instance_ids()

    if args.phase in ("2", "all"):
        phase_templates(ids)
    elif args.phase == "1":
        print(
            "\n[next] start mock gateway and then run phase 2:\n"
            "  python3 scripts/mock_gateway_ws.py        # in another terminal\n"
            "  python3 scripts/seed_demo_full.py --phase 2"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
