# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""演示数据 HTTP 请求体（与 enterprise_config_demo_data_config 对齐）。"""

from __future__ import annotations

from typing import Any

_DEMO_AGENT_IMAGE = "jiuwenclaw/agent-server:latest"


def demo_agent_server_base() -> dict[str, Any]:
    return {
        "agent_image": _DEMO_AGENT_IMAGE,
        "namespace": "jiuwenclaw",
        "container_name": "agent-server",
        "container_port": 8080,
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


def model_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "M1",
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
            "M2",
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
            "M3",
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
            "M4",
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
            "M5",
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


def embedding_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "EM1",
            {
                "template_name": "记忆向量-OpenAI",
                "description": "用于记忆语义检索",
                "embed_tags": ["memory", "task_memory"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-embedding",
                "model_id": "text-embedding-3-large",
                "model_provider": "openai",
                "parameters": {"encoding_format": "float", "dimensions": 1024},
                "client_config": {
                    "timeout": 60,
                    "retry_count": 3,
                    "verify_ssl": True,
                },
                "enabled": True,
                "data": {},
            },
        ),
    ]


def extension_config_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "E1",
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
            "E2",
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
            "E3",
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
            "E4",
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


def skill_whitelist_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "W1",
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
            "W2",
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
            "W3",
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


def service_config_templates() -> list[tuple[str, dict[str, Any]]]:
    base = demo_agent_server_base()
    return [
        (
            "S1",
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
            "S2",
            {
                **base,
                "template_name": "全局兜底 AgentServer 池",
                "description": "未命中服务策略时的最小 AgentServer 池",
                "min_idle_services": 1,
                "max_services": 5,
                "enabled": True,
                "data": {"demo": "s2"},
            },
        ),
    ]


def instance_create_body(*, jiuwenclaw_name: str = "ut-demo-instance") -> dict[str, Any]:
    return {
        "jiuwenclaw_name": jiuwenclaw_name,
        "creator_id": "ut-tester",
        "description": "manager API unit test instance",
        "k8s_master_host": "127.0.0.1",
        "k8s_auth_type": "none",
        "k8s_auth_config": {},
        "k8s_namespace": "default",
    }
