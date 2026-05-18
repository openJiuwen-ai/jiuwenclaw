"""解析组网内 agent_client REST 根 URL（与 ``routers/register.py`` 的 ``/api`` 前缀配合）。"""

from __future__ import annotations

from fastapi import HTTPException

from jiuwenclaw_manager.repositories.instance_repo import InstanceRepository


AGENT_CLIENT_INSTANCES_PREFIX = "/api/v1/instances"


async def resolve_management_api_base(repo: InstanceRepository, jiuwenclaw_id: str) -> str:
    row = await repo.get(jiuwenclaw_id)
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    data = row.data or {}
    raw = data.get("management_api_base")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().rstrip("/")
    services = await repo.list_services(jiuwenclaw_id)
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
