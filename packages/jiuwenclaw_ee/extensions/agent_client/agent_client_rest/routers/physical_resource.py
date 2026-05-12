from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..core.physical_resource.agent_server_res_mgr import (
    list_resource_config_records,
    upsert_resource_config_record,
)
from ..schemas import ResponseModel, ResourceConfigUpdateRequest

physical_resource_router = APIRouter()


@physical_resource_router.put("/resources")
async def update_instance_resources(
    request: ResourceConfigUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await upsert_resource_config_record(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(
        code=200,
        message="success",
        data=data,
    )


@physical_resource_router.get("/resources")
async def get_instance_resources(
    req: Request,
    component: str | None = Query(default=None),
    page_num: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=1000),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    items = await list_resource_config_records(
        handler,
        component,
        page_num=page_num,
        page_size=page_size,
    )
    return ResponseModel(code=200, message="success", data={"items": items})


# Backward compatibility alias
router = physical_resource_router
