#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""按《企业级claw数据模型设计.md》配置生效示例，向 Manager / Gateway 写入演示数据。

前置：已完成 ``provision-local``，Claw Manager 已启动（默认 ``http://127.0.0.1:8765``），
且目标实例 Gateway 可达。脚本经 Manager API 写入 **Gateway 库 + Manager 库**（双写，id 与 Gateway 一致）。

若升级 Manager 后首次双写失败，请删除 ``claw_manager.db`` 后重启 Manager 再执行。

执行顺序（与文档一致，跳过步骤 0 provision）：

1. 五条 ``model_template``（T1–T5）
2. 两条 ``config-effective/service-policies``
3. 两条 ``config-effective/agent-policies``（依赖上一步销售服务策略 id）
4. ``config-effective/global-policies``（每实例唯一；已存在则 PUT 更新）
5. 两条 ``config-default-template-mappings``

典型用法（PowerShell 一行）::

    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/seed_enterprise_demo_config.py sp-xxxxxxxxxxxx

可选环境变量 ``CLAWMANAGER_BASE_URL`` 覆盖 Manager 根地址。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 httpx，请在 claw_manager 目录执行: uv sync\n"
        "或: pip install httpx"
    ) from exc

_DEMO_TEMPLATE_NAMES = frozenset(
    {
        "全局兜底-经济型",
        "销售组-标准型",
        "VIP-加强对话",
        "Carol 默认映射模型",
        "销售组映射专用",
    }
)


class ManagerApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {path} -> HTTP {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class ManagerClient:
    def __init__(self, base_url: str, jiuwenclaw_id: str, *, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._jid = jiuwenclaw_id.strip()
        self._timeout = timeout
        if not self._jid:
            raise ValueError("jiuwenclaw_id 不能为空")

    def _url(self, path: str) -> str:
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


def _model_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "T1 全局兜底-经济型",
            {
                "display_name": "全局兜底-经济型",
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
            "T2 销售组-标准型",
            {
                "display_name": "销售组-标准型",
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
            "T3 VIP-加强对话",
            {
                "display_name": "VIP-加强对话",
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
            "T4 Carol 默认映射模型",
            {
                "display_name": "Carol 默认映射模型",
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
            "T5 销售组映射专用",
            {
                "display_name": "销售组映射专用",
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


def _ensure_fresh_instance(client: ManagerClient) -> None:
    existing = client.list_items("/model-templates")
    names = {str(row.get("display_name") or "") for row in existing}
    overlap = names & _DEMO_TEMPLATE_NAMES
    if overlap:
        raise SystemExit(
            f"实例 {client._jid!r} 已存在演示模型模板 {sorted(overlap)!r}。\n"
            "请在全新 provision 实例上重试，或先手动删除相关配置后再运行本脚本。"
        )


def _upsert_global_policy(client: ManagerClient, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return client.post("/config-effective/global-policies", body)
    except ManagerApiError as exc:
        if exc.status != 400 or "already exists" not in exc.detail.lower():
            raise
    rows = client.list_items("/config-effective/global-policies", page_size=5)
    if not rows:
        raise SystemExit("全局策略创建失败且列表为空，请检查 Manager / Gateway 日志。") from None
    policy_id = int(rows[0]["id"])
    print(f"  [global] 已存在，PUT 更新 id={policy_id}")
    return client.put(f"/config-effective/global-policies/{policy_id}", body)


def seed_demo_config(client: ManagerClient) -> dict[str, Any]:
    _ensure_fresh_instance(client)
    result: dict[str, Any] = {"jiuwenclaw_id": client._jid, "model_templates": {}}

    print("[1/5] 创建 model_template（T1–T5）")
    template_ids: list[int] = []
    for label, body in _model_templates():
        row = client.post("/model-templates", body)
        tid = _require_id(row, "/model-templates")
        template_ids.append(tid)
        key = f"t{len(template_ids)}"
        result["model_templates"][key] = tid
        print(f"  [{key}] {label} -> id={tid}")

    t1, t2, t3, t4, t5 = (str(i) for i in template_ids)
    group_map_default_model = f"${{group::g_demo_sales}} or {t1}"

    print("[2/5] 创建 service-policies")
    sales = client.post(
        "/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 100,
            "match_expr": "group_id == 'g_demo_sales'",
            "default_model": t2,
            "vision_model": t2,
            "enabled": True,
            "data": {
                "note": "服务策略匹配仅看 match_expr；service_id 仅为业务标识"
            },
        },
    )
    sales_id = _require_id(sales, "service-policies/sales")
    result["service_policy_sales_id"] = sales_id
    print(f"  [2.1] 销售通道 priority=100 -> id={sales_id} (default_model={t2})")

    fallback = client.post(
        "/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 10,
            "match_expr": "group_id == 'g_demo_sales'",
            "default_model": t1,
            "enabled": True,
            "data": {},
        },
    )
    fallback_id = _require_id(fallback, "service-policies/fallback")
    result["service_policy_fallback_id"] = fallback_id
    print(f"  [2.2] 低优先级兜底 -> id={fallback_id} (default_model={t1})")

    print("[3/5] 创建 agent-policies")
    vip = client.post(
        "/config-effective/agent-policies",
        {
            "agent_id": "${user_id}",
            "service_policy_id": sales_id,
            "priority": 100,
            "match_expr": "user_id == 'alice'",
            "default_model": t3,
            "vision_model": t3,
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
    print(f"  [3.1] VIP alice -> id={vip_id} (default_model={t3})")

    mapping_rule = client.post(
        "/config-effective/agent-policies",
        {
            "agent_id": "${user_id}",
            "service_policy_id": sales_id,
            "priority": 0,
            "match_expr": "",
            "default_model": group_map_default_model,
            "enabled": True,
            "data": {
                "remark": f"group:: 仅按 group_id 查步骤 5.2；or 右侧 {t1} 为 T1 的 model_template.id 回退"
            },
        },
    )
    mapping_id = _require_id(mapping_rule, "agent-policies/mapping")
    result["agent_policy_mapping_id"] = mapping_id
    print(f"  [3.2] 组映射表达式 -> id={mapping_id} (default_model={group_map_default_model})")

    print("[4/5] 创建 / 更新 global-policies")
    global_row = _upsert_global_policy(
        client,
        {
            "default_model": t1,
            "video_model": t1,
            "audio_model": t1,
            "vision_model": t1,
            "enabled": True,
            "data": {},
        },
    )
    global_id = _require_id(global_row, "global-policies")
    result["global_policy_id"] = global_id
    print(f"  [4] 全局兜底 -> id={global_id} (四槽位={t1})")

    print("[5/5] 创建 config-default-template-mappings")
    carol_map = client.post(
        "/config-default-template-mappings",
        {
            "user_id": "carol",
            "group_id": None,
            "template_id": t4,
            "template_type": "model",
            "enabled": True,
            "data": {"remark": "未命中 Agent/服务策略时的用户级默认"},
        },
    )
    carol_map_id = _require_id(carol_map, "mapping/carol")
    result["mapping_carol_id"] = carol_map_id
    print(f"  [5.1] user carol -> template_id={t4} (id={carol_map_id})")

    group_map = client.post(
        "/config-default-template-mappings",
        {
            "user_id": None,
            "group_id": "g_demo_sales",
            "template_id": t5,
            "template_type": "model",
            "enabled": True,
            "data": {"remark": "组级默认映射，与 T2 服务级默认区分"},
        },
    )
    group_map_id = _require_id(group_map, "mapping/group")
    result["mapping_group_id"] = group_map_id
    print(f"  [5.2] group g_demo_sales -> template_id={t5} (id={group_map_id})")

    result["template_id_literals"] = {"t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5}
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
    args = _parse_args()
    client = ManagerClient(args.manager_base, args.jiuwenclaw_id, timeout=args.timeout)
    print(f"[seed] jiuwenclaw_id={client._jid} manager={client._base}")

    try:
        summary = seed_demo_config(client)
    except ManagerApiError as exc:
        print(f"[failed] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except httpx.ConnectError as exc:
        print(f"[connect-failed] {exc}", file=sys.stderr)
        print(
            f"请确认 Claw Manager 已在 {args.manager_base} 启动，且实例 {args.jiuwenclaw_id} 已 provision。",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    print("\n[done] 演示配置已写入。预期解析：")
    print("  alice + g_demo_sales::bot_main -> T3 (VIP)")
    print("  bob   + g_demo_sales::bot_main -> T5 (Agent 3.2 + 映射 5.2)")
    print("  g_unknown::bot_main              -> T1 (全局兜底)")
    print("\n联调聊天（将 {{WEB_PORT}} 换为 provision 返回的 ports.web）：")
    print(
        "  uv run python packages/jiuwenclaw-ee/claw_manager/scripts/send_enterprise_chat.py "
        "--group-id g_demo_sales --bot-id bot_main --user-id alice "
        "--web-port {WEB_PORT}"
    )

    if args.json_out:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
