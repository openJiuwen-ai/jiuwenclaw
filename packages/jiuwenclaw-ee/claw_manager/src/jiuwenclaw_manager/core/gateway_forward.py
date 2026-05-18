"""转发到组网内 Gateway ``agent_client_rest``（``/api/v1/instances/...``）。"""

from __future__ import annotations

from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.repositories.instance_repo import InstanceRepository
from jiuwenclaw_manager.core.management_target import (
    AGENT_CLIENT_INSTANCES_PREFIX,
    resolve_management_api_base,
)


def forward_headers() -> dict[str, str]:
    from jiuwenclaw_manager.config import settings

    hdrs: dict[str, str] = {}
    if settings.upstream_api_key:
        hdrs["Authorization"] = f"Bearer {settings.upstream_api_key}"
    return hdrs


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
        http_client: httpx.AsyncClient,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._handler = handler
        self._client = http_client
        self._headers = extra_headers or {}
        self._repo = InstanceRepository(handler)

    async def _request(
        self,
        method: str,
        jiuwenclaw_id: str,
        relative_path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = await resolve_management_api_base(self._repo, jiuwenclaw_id)
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

    # --- model_template ---

    async def create_model_template(
        self, jiuwenclaw_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", jiuwenclaw_id, "/model-templates", json_body=body
        )

    async def list_model_templates(
        self,
        jiuwenclaw_id: str,
        *,
        enabled: bool | None = None,
        model_type: str | None = None,
        page_size: int = 20,
        page_num: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size, "page_num": page_num}
        if model_type is not None:
            params["model_type"] = model_type
        if enabled is not None:
            params["enabled"] = enabled
        return await self._request(
            "GET", jiuwenclaw_id, "/model-templates", params=params
        )

    async def get_model_template(
        self, jiuwenclaw_id: str, template_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET", jiuwenclaw_id, f"/model-templates/{template_id}"
        )

    async def update_model_template(
        self, jiuwenclaw_id: str, template_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            jiuwenclaw_id,
            f"/model-templates/{template_id}",
            json_body=body,
        )

    async def delete_model_template(
        self, jiuwenclaw_id: str, template_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE", jiuwenclaw_id, f"/model-templates/{template_id}"
        )

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
