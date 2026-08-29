# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""FastAPI 依赖：同步信封解析（无验签/解密）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from ..http.sync_security import split_envelope
from ..infrastructure.utils import assert_jiuwenclaw_id_matches, get_jiuwenclaw_id
from ..schemas.sync_schemas import SyncEnvelopeOnlyBody

TBody = TypeVar("TBody", bound=BaseModel)


@dataclass(frozen=True)
class SyncContext:
    """同步上下文（不含业务分发）。"""

    revision: str
    business: dict[str, Any]
    jiuwenclaw_id: str
    method: str


def require_jiuwenclaw_id(business: dict[str, Any] | None = None) -> str:
    """统一从环境变量 ``JIUWENCLAW_ID`` 取实例 id；body 若带 id 则校验一致。"""
    jid = str(get_jiuwenclaw_id() or "").strip()
    if not jid:
        raise HTTPException(
            status_code=400,
            detail="JIUWENCLAW_ID is not set; register required",
        )
    body_jid = ""
    if business:
        body_jid = str(business.get("jiuwenclaw_id") or "").strip()
    if body_jid:
        try:
            assert_jiuwenclaw_id_matches(body_jid)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return jid


async def build_sync_context(body: BaseModel, method: str) -> SyncContext:
    raw = body.model_dump(mode="python")
    revision, business = split_envelope(raw)
    jid = require_jiuwenclaw_id(business)
    business.pop("jiuwenclaw_id", None)
    return SyncContext(
        revision=revision,
        business=business,
        jiuwenclaw_id=jid,
        method=method.upper(),
    )


def verify_sync(body_cls: type[TBody]) -> Callable[..., Any]:
    """生成带明确 request body 模型的依赖（历史名保留，已无验签）。"""

    async def _dep(
        request: Request,
        body: body_cls,  # type: ignore[valid-type]
    ) -> SyncContext:
        return await build_sync_context(body, request.method)

    _dep.__name__ = f"verify_sync_{getattr(body_cls, '__name__', 'body')}"
    _dep.__annotations__ = {
        "request": Request,
        "body": body_cls,
        "return": SyncContext,
    }
    return _dep


# DELETE / 无业务字段写接口常用
VerifySyncEnvelopeOnly = Annotated[
    SyncContext, Depends(verify_sync(SyncEnvelopeOnlyBody))
]


def sync_write_data(sync: SyncContext, result: Any) -> dict[str, Any]:
    """写接口统一 data：revision + 业务结果。"""
    return {"revision": sync.revision, "result": result}
