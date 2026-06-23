"""实例纳管与 Gateway WebSocket 心跳。"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.schemas.instance_schemas import (
    CreateInstanceBody,
    InstanceDetail,
    InstanceSummary,
    InstanceUpdateBody,
)
from jiuwenclaw_manager.models.instance_models import INSTANCE_INFO_TABLE_DEF

logger = logging.getLogger(__name__)

_INSTANCE_TABLE = INSTANCE_INFO_TABLE_DEF.table_name
_LOG_MASKING_SEEDED_KEY = "log_masking_seeded"


def _instance_data_dict(row: Any | None) -> dict[str, Any]:
    data = getattr(row, "data", None) if row is not None else None
    return dict(data) if isinstance(data, dict) else {}


async def is_log_masking_seeded(handler: DBHandler, jiuwenclaw_id: str) -> bool:
    """``instance_info.data.log_masking_seeded`` 为真时表示 builtin 种子已执行过。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return False
    row = await get_instance_row(handler, jid)
    return bool(_instance_data_dict(row).get(_LOG_MASKING_SEEDED_KEY))


def dumps_auth_config(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))


async def create_instance_row(handler: DBHandler, row_data: dict[str, Any]) -> Any:
    now = utc_now()
    payload = dict(row_data)
    payload.setdefault("created_at", now)
    payload.setdefault("updated_at", now)
    payload.setdefault("last_heartbeat", None)
    return await handler.create(_INSTANCE_TABLE, payload)


async def get_instance_row(handler: DBHandler, jiuwenclaw_id: str) -> Any | None:
    return await handler.get(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})


_MAX_JIUWENCLAW_ID_ATTEMPTS = 10
_JIUWENCLAW_NAME_MAX_LEN = 128
_K8S_NAMESPACE_MAX_LEN = 64


def _strip_payload_field(payload: dict[str, Any], key: str, *, max_len: int) -> str | None:
    val = str(payload.get(key) or "").strip()
    if not val:
        return None
    return val[:max_len]


async def generate_unique_jiuwenclaw_id(handler: DBHandler) -> str:
    """生成 ``instance_info`` 中尚未占用的 ``jiuwenclaw_id``。"""
    for _ in range(_MAX_JIUWENCLAW_ID_ATTEMPTS):
        jiuwenclaw_id = new_uuid4()
        if await get_instance_row(handler, jiuwenclaw_id) is None:
            return jiuwenclaw_id
    raise RuntimeError("failed to generate unique jiuwenclaw_id after retries")


async def bootstrap_gateway_log_masking(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> None:
    """Gateway WS 注册：首次 MDB builtin 种子 + bulk push 到 GDB（``op=sync``）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return
    try:
        from jiuwenclaw_manager.core.application_config.log_masking_rule import (
            push_log_masking_rules_sync_to_gateway,
            seed_builtin_log_masking_rules,
        )

        if not await is_log_masking_seeded(handler, jid):
            seeded = await seed_builtin_log_masking_rules(handler, jid)
            await merge_instance_data(handler, jid, {_LOG_MASKING_SEEDED_KEY: True})
            if seeded:
                logger.info(
                    "[Instance] seeded %d builtin log_masking_rule row(s) for %s",
                    seeded,
                    jid,
                )
            else:
                logger.info(
                    "[Instance] log_masking builtin seed completed for %s (no new rows)",
                    jid,
                )
        sync_ack = await push_log_masking_rules_sync_to_gateway(handler, jid)
        logger.info(
            "[Instance] log_masking_rule sync on gateway register jiuwenclaw_id=%s "
            "revision=%s",
            jid,
            sync_ack.get("revision"),
        )
    except Exception:
        logger.warning(
            "[Instance] log_masking_rule bootstrap failed for %s",
            jid,
            exc_info=True,
        )


async def bootstrap_gateway_templates(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> None:
    """Gateway WS 注册：将配置生效策略引用的模板 bulk push 到 GDB（``op=sync``）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return
    try:
        from jiuwenclaw_manager.core.template.push_template_to_gateway import (
            rebuild_jid_template_ref_for_gateway,
            sync_referenced_templates_to_gateway,
        )

        acks = await sync_referenced_templates_to_gateway(handler, jid)
        await rebuild_jid_template_ref_for_gateway(handler, jid)
        for name, ack in acks.items():
            logger.info(
                "[Instance] %s sync on gateway register jiuwenclaw_id=%s revision=%s",
                name,
                jid,
                ack.get("revision"),
            )
    except Exception:
        logger.warning(
            "[Instance] template bootstrap failed for %s",
            jid,
            exc_info=True,
        )


async def register_gateway_via_ws(
    handler: DBHandler,
    payload: dict[str, Any],
    *,
    manager_id: str = "default",
) -> str:
    """Gateway WS 注册：复用已有 ``jiuwenclaw_id`` 或分配新 id 并写入 ``instance_info``。"""
    _ = manager_id
    payload_jiuwenclaw_id = str(payload.get("jiuwenclaw_id") or "").strip()
    now = utc_now()

    if payload_jiuwenclaw_id:
        existing = await get_instance_row(handler, payload_jiuwenclaw_id)
        if existing is not None:
            updates: dict[str, Any] = {"status": "online", "updated_at": now}
            name = _strip_payload_field(
                payload, "jiuwenclaw_name", max_len=_JIUWENCLAW_NAME_MAX_LEN
            )
            namespace = _strip_payload_field(
                payload, "k8s_namespace", max_len=_K8S_NAMESPACE_MAX_LEN
            )
            if name:
                updates["jiuwenclaw_name"] = name
            if namespace:
                updates["k8s_namespace"] = namespace
            await handler.update(
                _INSTANCE_TABLE,
                {"jiuwenclaw_id": payload_jiuwenclaw_id},
                updates,
            )
            return payload_jiuwenclaw_id
        jiuwenclaw_id = payload_jiuwenclaw_id
    else:
        jiuwenclaw_id = await generate_unique_jiuwenclaw_id(handler)

    jiuwenclaw_name = _strip_payload_field(
        payload, "jiuwenclaw_name", max_len=_JIUWENCLAW_NAME_MAX_LEN
    ) or f"gateway-{jiuwenclaw_id[-8:]}"
    k8s_namespace = _strip_payload_field(
        payload, "k8s_namespace", max_len=_K8S_NAMESPACE_MAX_LEN
    ) or "default"
    await create_instance_row(
        handler,
        {
            "jiuwenclaw_id": jiuwenclaw_id,
            "jiuwenclaw_name": jiuwenclaw_name,
            "creator_id": "manager-ws",
            "description": None,
            "k8s_master_host": "manager-ws",
            "k8s_auth_type": "none",
            "k8s_auth_config": dumps_auth_config({}),
            "k8s_namespace": k8s_namespace,
            "status": "online",
            "resource_quota": None,
            "data": None,
            "group_id": "default",
            "space_id": "default",
        },
    )
    return jiuwenclaw_id


async def apply_gateway_ws_heartbeat(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    manager_id: str = "default",
    endpoint: str | None = None,
    version: str | None = None,
) -> bool:
    """Gateway 经 Manager WebSocket 上报心跳，刷新 ``instance_info.status`` 与 ``last_heartbeat``。"""
    _ = manager_id
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return False
    if await get_instance_row(handler, jid) is None:
        return False
    now = utc_now()
    updates: dict[str, Any] = {
        "status": "online",
        "last_heartbeat": now,
        "updated_at": now,
    }
    if endpoint or version:
        row = await get_instance_row(handler, jid)
        merged = dict(getattr(row, "data", None) or {}) if row is not None else {}
        if endpoint:
            merged["gateway_endpoint"] = endpoint
        if version:
            merged["gateway_version"] = version
        updates["data"] = merged
    await handler.update(_INSTANCE_TABLE, {"jiuwenclaw_id": jid}, updates)
    return True


async def mark_instance_offline(handler: DBHandler, jiuwenclaw_id: str) -> None:
    """Gateway WS 断开或心跳超时后，将实例标记为 offline。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return
    row = await get_instance_row(handler, jid)
    if row is None:
        return
    now = utc_now()
    await handler.update(
        _INSTANCE_TABLE,
        {"jiuwenclaw_id": jid},
        {"status": "offline", "updated_at": now},
    )


async def list_instance_rows(
    handler: DBHandler,
    *,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[Sequence[Any], int]:
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    total = await handler.count_records(_INSTANCE_TABLE, filters)
    rows = await handler.list_records(_INSTANCE_TABLE, filters, limit=limit, offset=offset)
    return rows, int(total)


async def delete_instance_row(handler: DBHandler, jiuwenclaw_id: str) -> None:
    await handler.delete(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    # 解绑销毁：一并删除该实例的 Gateway 加密公钥（心跳超时下线不走此路径）。
    from jiuwenclaw_manager.security.keys import delete_instance_enc_pubkey

    await delete_instance_enc_pubkey(handler, jiuwenclaw_id)


async def merge_instance_data(
    handler: DBHandler, jiuwenclaw_id: str, patch: dict
) -> Any | None:
    """仅合并写入 ``instance_info.data`` JSON 列（供 provision 等内部调用）。"""
    row = await get_instance_row(handler, jiuwenclaw_id)
    if row is None:
        return None
    merged = dict(getattr(row, "data", None) or {})
    merged.update(patch)
    now = utc_now()
    return await handler.update(
        _INSTANCE_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id},
        {"data": merged, "updated_at": now},
    )


class InstanceService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, body: CreateInstanceBody) -> dict:
        jiuwenclaw_id = await generate_unique_jiuwenclaw_id(self._handler)
        data_dict: dict | None = None
        if body.management_api_base and str(body.management_api_base).strip():
            data_dict = {"management_api_base": str(body.management_api_base).strip().rstrip("/")}
        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "jiuwenclaw_name": body.jiuwenclaw_name,
            "creator_id": body.creator_id,
            "description": body.description,
            "k8s_master_host": body.k8s_master_host,
            "k8s_auth_type": body.k8s_auth_type,
            "k8s_auth_config": dumps_auth_config(body.k8s_auth_config),
            "k8s_namespace": body.k8s_namespace,
            "status": "online",
            "resource_quota": body.resource_quota,
            "data": data_dict,
            "group_id": body.group_id,
            "space_id": body.space_id,
        }
        row = await create_instance_row(self._handler, row_data)
        return {"jiuwenclaw_id": jiuwenclaw_id, "status": getattr(row, "status", "online")}

    async def list_instances(
        self, *, page: int, page_size: int, status: str | None
    ) -> dict:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        rows, total = await list_instance_rows(
            self._handler, status=status, offset=offset, limit=page_size
        )
        items = [
            InstanceSummary(
                jiuwenclaw_id=r.jiuwenclaw_id,
                jiuwenclaw_name=r.jiuwenclaw_name,
                status=r.status,
                k8s_namespace=r.k8s_namespace,
                group_id=r.group_id,
                space_id=r.space_id,
                created_at=iso_datetime(r.created_at),
                last_heartbeat=iso_datetime(getattr(r, "last_heartbeat", None)),
            )
            for r in rows
        ]
        return {
            "items": [i.model_dump() for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get(self, jiuwenclaw_id: str) -> InstanceDetail | None:
        row = await get_instance_row(self._handler, jiuwenclaw_id)
        if row is None:
            return None
        return InstanceDetail(
            jiuwenclaw_id=row.jiuwenclaw_id,
            jiuwenclaw_name=row.jiuwenclaw_name,
            status=row.status,
            k8s_namespace=row.k8s_namespace,
            group_id=row.group_id,
            space_id=row.space_id,
            created_at=iso_datetime(row.created_at),
            last_heartbeat=iso_datetime(getattr(row, "last_heartbeat", None)),
            description=row.description,
            k8s_master_host=row.k8s_master_host,
            k8s_auth_type=row.k8s_auth_type,
            resource_quota=row.resource_quota,
            data=row.data,
        )

    async def delete(self, jiuwenclaw_id: str) -> bool:
        from jiuwenclaw_manager.core.instance.instance_provisioner import (
            terminate_local_if_present,
        )

        row = await get_instance_row(self._handler, jiuwenclaw_id)
        if row is None:
            return False
        await terminate_local_if_present(self._handler, jiuwenclaw_id)
        await delete_instance_row(self._handler, jiuwenclaw_id)
        return True

    async def update(
        self, jiuwenclaw_id: str, body: InstanceUpdateBody
    ) -> InstanceDetail | None:
        jid = str(jiuwenclaw_id or "").strip()
        if not jid:
            raise ValueError("jiuwenclaw_id is required")
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            return await self.get(jid)
        if await get_instance_row(self._handler, jid) is None:
            return None

        strip_fields = (
            "jiuwenclaw_name",
            "description",
            "k8s_master_host",
            "k8s_auth_type",
            "k8s_namespace",
            "group_id",
            "space_id",
        )
        for field in strip_fields:
            if field in updates and updates[field] is not None:
                updates[field] = str(updates[field]).strip()
        if "k8s_auth_config" in updates and updates["k8s_auth_config"] is not None:
            if not isinstance(updates["k8s_auth_config"], dict):
                raise ValueError("k8s_auth_config must be an object")
            updates["k8s_auth_config"] = dumps_auth_config(updates["k8s_auth_config"])

        updates["updated_at"] = utc_now()
        if await self._handler.update(
            _INSTANCE_TABLE, {"jiuwenclaw_id": jid}, updates
        ) is None:
            return None
        return await self.get(jid)
