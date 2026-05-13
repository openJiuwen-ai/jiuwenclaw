"""将配置请求转发到组网内 ``jiuwenclaw.extensions.agent_client.routers`` 暴露的 REST API。"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from jiuwenclaw_manager.repositories.instance_repo import InstanceRepository
from jiuwenclaw_manager.services.management_target import AGENT_CLIENT_INSTANCES_PREFIX, resolve_management_api_base


class RuntimeConfigForwardService:
    """HTTP 转发到 ``{management_api_base}{AGENT_CLIENT_INSTANCES_PREFIX}/...``。"""

    def __init__(
        self,
        session: AsyncSession,
        http_client: httpx.AsyncClient,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self._client = http_client
        self._headers = extra_headers or {}
        self._repo = InstanceRepository(session)

    async def _request(
        self,
        method: str,
        jiuwenclaw_id: str,
        relative_path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = await resolve_management_api_base(self._repo, jiuwenclaw_id)
        url = f"{base}{AGENT_CLIENT_INSTANCES_PREFIX}{relative_path}"
        try:
            resp = await self._client.request(
                method,
                url,
                json=json_body,
                headers=self._headers or None,
            )
        except httpx.RequestError as exc:
            raise exc

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

    async def create_model(self, jiuwenclaw_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", jiuwenclaw_id, "/models", json_body=body)

    async def update_model(self, jiuwenclaw_id: str, model_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", jiuwenclaw_id, f"/models/{model_id}", json_body=body)

    async def delete_model(self, jiuwenclaw_id: str, model_id: int) -> dict[str, Any]:
        return await self._request("DELETE", jiuwenclaw_id, f"/models/{model_id}")

    async def create_channel(self, jiuwenclaw_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", jiuwenclaw_id, "/channels", json_body=body)

    async def activate_channel(self, jiuwenclaw_id: str, channel_id: str) -> dict[str, Any]:
        return await self._request("POST", jiuwenclaw_id, f"/channels/{channel_id}/activate")

    async def deactivate_channel(self, jiuwenclaw_id: str, channel_id: str) -> dict[str, Any]:
        return await self._request("POST", jiuwenclaw_id, f"/channels/{channel_id}/deactivate")

    async def delete_channel(self, jiuwenclaw_id: str, channel_id: str) -> dict[str, Any]:
        return await self._request("DELETE", jiuwenclaw_id, f"/channels/{channel_id}")

    async def put_session_affinity(self, jiuwenclaw_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", jiuwenclaw_id, "/session-affinity", json_body=body)

    async def put_isolation_policy(
        self, jiuwenclaw_id: str, policy_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PUT", jiuwenclaw_id, f"/isolation-policies/{policy_id}", json_body=body
        )

    async def put_agent_server_config(self, jiuwenclaw_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", jiuwenclaw_id, "/agent-server/config", json_body=body)

    async def put_resources(self, jiuwenclaw_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", jiuwenclaw_id, "/resources", json_body=body)
