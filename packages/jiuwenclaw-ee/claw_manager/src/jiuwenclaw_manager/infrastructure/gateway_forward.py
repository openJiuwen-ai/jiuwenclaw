"""转发到组网内 Gateway ``agent_client_rest``（``/api/v1/...``）。"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

AGENT_CLIENT_INSTANCES_PREFIX = "/api/v1"


async def resolve_management_api_base(handler: DBHandler, jiuwenclaw_id: str) -> str:
    """解析组网内 agent_client REST 根 URL（与 ``routers/register.py`` 的 ``/api`` 前缀配合）。"""
    from jiuwenclaw_manager.core.instance.instance_service import (
        get_instance_row,
        list_instance_services,
    )

    row = await get_instance_row(handler, jiuwenclaw_id)
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    data = row.data or {}
    raw = data.get("management_api_base")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().rstrip("/")
    services = await list_instance_services(handler, jiuwenclaw_id)
    for s in services:
        if s.service_type == "gateway" and s.status == "online" and s.endpoint:
            u = s.endpoint.strip().rstrip("/")
            if u:
                return u
    raise HTTPException(
        status_code=400,
        detail=(
            "未配置 management_api_base：请在创建实例时传入 management_api_base，"
            "或 PATCH /api/v1/instances/{id} 合并 data.management_api_base，"
            "或由 gateway 心跳在 data 中携带 management_api_base（指向 agent_client_rest 根地址，如 http://127.0.0.1:18080）"
        ),
    )


class GatewayHttpClient:
    """网关转发用 ``httpx.AsyncClient`` 单例（由应用 ``lifespan`` 初始化与关闭）。"""

    _client: httpx.AsyncClient | None = None

    @classmethod
    def init(cls, *, timeout: httpx.Timeout | None = None) -> httpx.AsyncClient:
        if cls._client is not None:
            return cls._client
        from jiuwenclaw_manager.infrastructure.config import settings

        cls._client = httpx.AsyncClient(
            timeout=timeout
            or httpx.Timeout(settings.upstream_http_timeout_seconds)
        )
        return cls._client

    @classmethod
    def get(cls) -> httpx.AsyncClient:
        if cls._client is None:
            raise RuntimeError(
                "http_client is not initialized; call GatewayHttpClient.init first."
            )
        return cls._client

    @classmethod
    async def close(cls) -> None:
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None


def _forward_error_detail(out: dict[str, Any]) -> Any:
    upstream = out.get("upstream")
    if isinstance(upstream, dict):
        return upstream.get("detail") or upstream.get("message") or upstream
    return upstream or "gateway request failed"


def forward_upstream_data(out: dict[str, Any]) -> Any:
    """网关调用成功时返回 ``upstream`` 响应体中的 ``data`` 字段。"""
    if not out.get("ok"):
        raise ValueError(_forward_error_detail(out))

    upstream = out.get("upstream")
    if isinstance(upstream, dict):
        if upstream.get("code", 200) != 200:
            raise ValueError(_forward_error_detail(out))
        return upstream.get("data")
    return upstream


def forward_upstream_data_or_none(out: dict[str, Any]) -> Any | None:
    """成功返回 ``data``；HTTP 404 返回 ``None``；其它失败抛 ``ValueError``。"""
    if not out.get("ok"):
        if int(out.get("http_status", 0)) == 404:
            return None
        raise ValueError(_forward_error_detail(out))
    return forward_upstream_data(out)


def normalize_list_page(
    data: dict[str, Any] | None, *, page: int, page_size: int
) -> dict[str, Any]:
    """将 Gateway 列表响应中的 ``page_num`` 对齐为 Manager API 的 ``page``。"""
    if not data:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    if "page" not in data and "page_num" in data:
        data = dict(data)
        data["page"] = data.pop("page_num")
    return data


class GatewayForwardService:
    """HTTP 转发基类：``{management_api_base}{AGENT_CLIENT_INSTANCES_PREFIX}/...``。"""

    def __init__(
        self,
        handler: DBHandler,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._handler = handler
        self._client = GatewayHttpClient.get()
        self._headers = extra_headers or {}

    async def _request(
        self,
        method: str,
        jiuwenclaw_id: str,
        relative_path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = await resolve_management_api_base(self._handler, jiuwenclaw_id)
        url = f"{base}{AGENT_CLIENT_INSTANCES_PREFIX}{relative_path}"
        try:
            resp = await self._client.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=self._headers or None,
            )
        except httpx.RequestError as exc:
            raise ValueError(f"gateway unreachable: {exc}") from exc

        try:
            payload: Any = resp.json()
        except Exception:
            payload = {"_non_json_body": resp.text[:8000]}

        if resp.status_code >= 400:
            return {
                "ok": False,
                "http_status": resp.status_code,
                "upstream": payload,
            }
        if isinstance(payload, dict) and payload.get("code", 200) != 200:
            return {
                "ok": False,
                "http_status": resp.status_code,
                "upstream": payload,
            }
        return {"ok": True, "http_status": resp.status_code, "upstream": payload}


class EnterpriseGatewayForward(GatewayForwardService):
    """企业级配置（模型模板、配置生效策略等）转发。"""

    # --- model_template（由 Claw Manager 全局 API 管理，不经 Gateway HTTP 转发） ---

    # --- config_default_template_mapping ---

    async def create_template_mapping(
        self, jiuwenclaw_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            jiuwenclaw_id,
            "/config-default-template-mappings",
            json_body=body,
        )

    async def list_template_mappings(
        self,
        jiuwenclaw_id: str,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
        template_type: str | None = None,
        template_id: str | None = None,
        enabled: bool | None = None,
        page_size: int = 20,
        page_num: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size, "page_num": page_num}
        if user_id:
            params["user_id"] = user_id
        if group_id:
            params["group_id"] = group_id
        if template_type:
            params["template_type"] = template_type
        if template_id:
            params["template_id"] = template_id
        if enabled is not None:
            params["enabled"] = enabled
        return await self._request(
            "GET",
            jiuwenclaw_id,
            "/config-default-template-mappings",
            params=params,
        )

    async def get_template_mapping(
        self, jiuwenclaw_id: str, mapping_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            jiuwenclaw_id,
            f"/config-default-template-mappings/{mapping_id}",
        )

    async def update_template_mapping(
        self, jiuwenclaw_id: str, mapping_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            jiuwenclaw_id,
            f"/config-default-template-mappings/{mapping_id}",
            json_body=body,
        )

    async def delete_template_mapping(
        self, jiuwenclaw_id: str, mapping_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            jiuwenclaw_id,
            f"/config-default-template-mappings/{mapping_id}",
        )

    # --- config_effective_global_policy ---

    async def create_global_policy(
        self, jiuwenclaw_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            jiuwenclaw_id,
            "/config-effective/global-policies",
            json_body=body,
        )

    async def list_global_policies(
        self,
        jiuwenclaw_id: str,
        *,
        enabled: bool | None = None,
        page_size: int = 20,
        page_num: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size, "page_num": page_num}
        if enabled is not None:
            params["enabled"] = enabled
        return await self._request(
            "GET",
            jiuwenclaw_id,
            "/config-effective/global-policies",
            params=params,
        )

    async def get_global_policy(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            jiuwenclaw_id,
            f"/config-effective/global-policies/{policy_id}",
        )

    async def update_global_policy(
        self, jiuwenclaw_id: str, policy_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            jiuwenclaw_id,
            f"/config-effective/global-policies/{policy_id}",
            json_body=body,
        )

    async def delete_global_policy(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            jiuwenclaw_id,
            f"/config-effective/global-policies/{policy_id}",
        )

    # --- config_effective_service_policy ---

    async def create_service_policy(
        self, jiuwenclaw_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            jiuwenclaw_id,
            "/config-effective/service-policies",
            json_body=body,
        )

    async def list_service_policies(
        self,
        jiuwenclaw_id: str,
        *,
        enabled: bool | None = None,
        page_size: int = 20,
        page_num: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size, "page_num": page_num}
        if enabled is not None:
            params["enabled"] = enabled
        return await self._request(
            "GET",
            jiuwenclaw_id,
            "/config-effective/service-policies",
            params=params,
        )

    async def get_service_policy(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            jiuwenclaw_id,
            f"/config-effective/service-policies/{policy_id}",
        )

    async def update_service_policy(
        self, jiuwenclaw_id: str, policy_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            jiuwenclaw_id,
            f"/config-effective/service-policies/{policy_id}",
            json_body=body,
        )

    async def delete_service_policy(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            jiuwenclaw_id,
            f"/config-effective/service-policies/{policy_id}",
        )

    # --- config_effective_agent_policy ---

    async def create_agent_policy(
        self, jiuwenclaw_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            jiuwenclaw_id,
            "/config-effective/agent-policies",
            json_body=body,
        )

    async def list_agent_policies(
        self,
        jiuwenclaw_id: str,
        *,
        service_policy_id: int | None = None,
        enabled: bool | None = None,
        page_size: int = 20,
        page_num: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size, "page_num": page_num}
        if service_policy_id is not None:
            params["service_policy_id"] = service_policy_id
        if enabled is not None:
            params["enabled"] = enabled
        return await self._request(
            "GET",
            jiuwenclaw_id,
            "/config-effective/agent-policies",
            params=params,
        )

    async def get_agent_policy(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            jiuwenclaw_id,
            f"/config-effective/agent-policies/{policy_id}",
        )

    async def update_agent_policy(
        self, jiuwenclaw_id: str, policy_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            jiuwenclaw_id,
            f"/config-effective/agent-policies/{policy_id}",
            json_body=body,
        )

    async def delete_agent_policy(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            jiuwenclaw_id,
            f"/config-effective/agent-policies/{policy_id}",
        )
