# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例注册与探活 API。"""

from __future__ import annotations

from base64 import b64encode
from typing import Any

from fastapi import APIRouter, HTTPException
from openjiuwen_runtime.foundation.security.link_auth import InMemoryPinStore, verify_and_pin
from pydantic import BaseModel, Field

from ..core.enterprise_config.gateway_db import GatewayDb
from ..core.instance import resolve_public_endpoint
from ..infrastructure.utils import (
    get_gateway_register_identity,
    get_jiuwenclaw_id,
    set_jiuwenclaw_id,
)
from ..schemas.common_schemas import ResponseModel
from ..security.keys import get_or_create_gateway_enc_keypair, store_manager_sign_pubkey

register_router = APIRouter()
_manager_pin_store = InMemoryPinStore()


class RegisterInstanceBody(BaseModel):
    link_token: str = Field(..., min_length=1)
    manager_id: str = Field(default="default")
    jiuwenclaw_id: str = Field(..., min_length=1)
    sign_pubkey: str = Field(..., min_length=1)
    sign_alg: str = Field(default="Ed25519")
    sign_pubkey_fp: str | None = None
    key_version: str = Field(default="v1")


@register_router.get("/health", response_model=ResponseModel)
async def instance_health() -> ResponseModel:
    jid = get_jiuwenclaw_id()
    return ResponseModel(
        code=200,
        message="success",
        data={
            "status": "ok",
            "service_type": "gateway",
            "jiuwenclaw_id": jid,
            "registered": bool(jid),
        },
    )


@register_router.get("/register-payload", response_model=ResponseModel)
async def register_payload() -> ResponseModel:
    enc_pubkey = enc_pubkey_fp = None
    try:
        keypair = await get_or_create_gateway_enc_keypair()
        enc_pubkey = b64encode(keypair.public_raw).decode("ascii")
        enc_pubkey_fp = keypair.fingerprint
    except Exception:  # noqa: BLE001
        pass
    data: dict[str, Any] = {
        "service_type": "gateway",
        "jiuwenclaw_id": get_jiuwenclaw_id(),
        "enc_pubkey": enc_pubkey,
        "enc_alg": "X25519",
        "enc_pubkey_fp": enc_pubkey_fp,
        "endpoint": resolve_public_endpoint(),
    }
    data.update(get_gateway_register_identity())
    return ResponseModel(code=200, message="success", data=data)


@register_router.post("/register", response_model=ResponseModel)
async def register_instance(body: RegisterInstanceBody) -> ResponseModel:
    res = verify_and_pin(
        _manager_pin_store,
        body.link_token,
        expect_type="manager",
    )
    if not res.allowed:
        raise HTTPException(status_code=401, detail=f"link-auth failed: {res.reason}")

    jid = body.jiuwenclaw_id.strip()
    set_jiuwenclaw_id(jid)
    GatewayDb.bind(jid)
    await store_manager_sign_pubkey(
        jid,
        body.sign_pubkey,
        key_version=body.key_version,
        manager_id=body.manager_id,
        sign_alg=body.sign_alg,
        fingerprint=body.sign_pubkey_fp,
    )
    enc_fp = None
    try:
        enc_fp = (await get_or_create_gateway_enc_keypair()).fingerprint
    except Exception:  # noqa: BLE001
        pass
    return ResponseModel(
        code=200,
        message="success",
        data={
            "jiuwenclaw_id": jid,
            "service_type": "gateway",
            "enc_pubkey_fp": enc_fp,
        },
    )
