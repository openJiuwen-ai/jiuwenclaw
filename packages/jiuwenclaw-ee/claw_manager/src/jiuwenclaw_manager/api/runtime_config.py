"""将 Manager 配置操作转发到组网内 agent_client REST（/api/v1/instances/...）。"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from jiuwenclaw_manager.api.deps import get_db, get_http_client
from jiuwenclaw_manager.models.schemas import ApiResponse
from jiuwenclaw_manager.models.upstream_payloads import (
    AgentServerConfigUpdateBody,
    ChannelConfigCreateBody,
    ModelConfigCreateBody,
    ModelConfigUpdateBody,
    ResourceConfigUpdateBody,
    SessionAffinityPolicyUpdateBody,
    TenantIsolationPolicyUpdateBody,
)
from jiuwenclaw_manager.services.runtime_config_forward import RuntimeConfigForwardService

router = APIRouter(prefix="/instances", tags=["runtime-config"])


def _svc(session: AsyncSession, client: httpx.AsyncClient) -> RuntimeConfigForwardService:
    from jiuwenclaw_manager.config import settings

    hdrs: dict[str, str] = {}
    if settings.upstream_api_key:
        hdrs["Authorization"] = f"Bearer {settings.upstream_api_key}"
    return RuntimeConfigForwardService(session, client, hdrs)


def _require_ok(out: dict) -> dict:
    if out.get("ok"):
        return out
    raise HTTPException(status_code=int(out.get("http_status", 502)), detail=out.get("upstream"))


@router.get("/{jiuwenclaw_id}/models", response_model=ApiResponse)
async def forward_list_models(
    jiuwenclaw_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    model_type: str | None = None,
    enabled: bool | None = None,
    page_size: int = Query(20, ge=1, le=200),
    page_num: int = Query(1, ge=1),
):
    try:
        out = _require_ok(
            await _svc(session, client).list_models(
                jiuwenclaw_id,
                model_type=model_type,
                enabled=enabled,
                page_size=page_size,
                page_num=page_num,
            )
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.post("/{jiuwenclaw_id}/models", response_model=ApiResponse)
async def forward_create_model(
    jiuwenclaw_id: str,
    body: ModelConfigCreateBody,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(await _svc(session, client).create_model(jiuwenclaw_id, body.model_dump(exclude_none=True)))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.put("/{jiuwenclaw_id}/models/{model_id}", response_model=ApiResponse)
async def forward_update_model(
    jiuwenclaw_id: str,
    model_id: int,
    body: ModelConfigUpdateBody,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(
            await _svc(session, client).update_model(
                jiuwenclaw_id, model_id, body.model_dump(exclude_none=True)
            )
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.delete("/{jiuwenclaw_id}/models/{model_id}", response_model=ApiResponse)
async def forward_delete_model(
    jiuwenclaw_id: str,
    model_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(await _svc(session, client).delete_model(jiuwenclaw_id, model_id))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.post("/{jiuwenclaw_id}/channels", response_model=ApiResponse)
async def forward_create_channel(
    jiuwenclaw_id: str,
    body: ChannelConfigCreateBody,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(
            await _svc(session, client).create_channel(jiuwenclaw_id, body.model_dump(exclude_none=True))
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.post("/{jiuwenclaw_id}/channels/{channel_id}/activate", response_model=ApiResponse)
async def forward_activate_channel(
    jiuwenclaw_id: str,
    channel_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(await _svc(session, client).activate_channel(jiuwenclaw_id, channel_id))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.post("/{jiuwenclaw_id}/channels/{channel_id}/deactivate", response_model=ApiResponse)
async def forward_deactivate_channel(
    jiuwenclaw_id: str,
    channel_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(await _svc(session, client).deactivate_channel(jiuwenclaw_id, channel_id))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.delete("/{jiuwenclaw_id}/channels/{channel_id}", response_model=ApiResponse)
async def forward_delete_channel(
    jiuwenclaw_id: str,
    channel_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(await _svc(session, client).delete_channel(jiuwenclaw_id, channel_id))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.put("/{jiuwenclaw_id}/session-affinity", response_model=ApiResponse)
async def forward_put_session_affinity(
    jiuwenclaw_id: str,
    body: SessionAffinityPolicyUpdateBody,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(
            await _svc(session, client).put_session_affinity(
                jiuwenclaw_id, body.model_dump(exclude_none=True)
            )
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.put("/{jiuwenclaw_id}/isolation-policies/{policy_id}", response_model=ApiResponse)
async def forward_put_isolation_policy(
    jiuwenclaw_id: str,
    policy_id: int,
    body: TenantIsolationPolicyUpdateBody,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(
            await _svc(session, client).put_isolation_policy(
                jiuwenclaw_id, policy_id, body.model_dump(exclude_none=True)
            )
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.put("/{jiuwenclaw_id}/agent-server/config", response_model=ApiResponse)
async def forward_put_agent_server_config(
    jiuwenclaw_id: str,
    body: AgentServerConfigUpdateBody,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(
            await _svc(session, client).put_agent_server_config(
                jiuwenclaw_id, body.model_dump(exclude_none=True)
            )
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)


@router.put("/{jiuwenclaw_id}/resources", response_model=ApiResponse)
async def forward_put_resources(
    jiuwenclaw_id: str,
    body: ResourceConfigUpdateBody,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    try:
        out = _require_ok(
            await _svc(session, client).put_resources(jiuwenclaw_id, body.model_dump(exclude_none=True))
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc}") from exc
    return ApiResponse(data=out)
