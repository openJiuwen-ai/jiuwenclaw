# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServer ↔ Gateway WebSocket：E2AResponse 线编码 / 解码与 legacy 兜底。"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum
from typing import Any

from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_KIND_E2A_ERROR,
    E2A_RESPONSE_STATUS_FAILED,
    E2A_SOURCE_PROTOCOL_A2A,
    E2A_SOURCE_PROTOCOL_ACP,
    E2A_SOURCE_PROTOCOL_E2A,
    E2A_WIRE_INTERNAL_METADATA_KEYS,
    E2A_WIRE_LEGACY_AGENT_CHUNK_KEY,
    E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY,
)
from jiuwenswarm.common.e2a.gateway_normalize import (
    e2a_response_from_agent_chunk,
    e2a_response_from_agent_response,
    e2a_response_to_agent_chunk,
    e2a_response_to_agent_response,
)
from jiuwenswarm.common.e2a.models import (
    E2A_PROTOCOL_VERSION,
    E2AEnvelope,
    E2AProvenance,
    E2AResponse,
    IdentityOrigin,
    utc_now_iso,
)
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk

logger = logging.getLogger(__name__)

_PCS_E2A_REQUEST_FIELDS = frozenset(
    {
        "protocol_version",
        "request_id",
        "channel",
        "session_id",
        "method",
        "params",
        "is_stream",
        "user_id",
        "agent_ref",
        "chat_id",
        "identity_origin",
        "channel_context",
        "auth",
        "timestamp",
        "provenance",
    }
)
_PCS_E2A_OPTIONAL_STRING_FIELDS = ("session_id", "user_id", "chat_id")
_PCS_E2A_OPTIONAL_OBJECT_FIELDS = ("agent_ref", "channel_context", "auth", "provenance")
_PCS_E2A_SOURCE_PROTOCOLS = {
    E2A_SOURCE_PROTOCOL_E2A,
    E2A_SOURCE_PROTOCOL_ACP,
    E2A_SOURCE_PROTOCOL_A2A,
}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except Exception:
            return _json_safe(value.model_dump())
    if hasattr(value, "dict"):
        return _json_safe(value.dict())
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def _invalid_pcs_envelope(message: str) -> ValueError:
    return ValueError(f"invalid PCS E2A envelope: {message}")


def validate_pcs_e2a_request_dict(data: dict[str, Any]) -> None:
    """Validate the canonical PCS-only Gateway-to-AgentServer envelope."""

    if not isinstance(data, dict):
        raise _invalid_pcs_envelope("request must be an object")
    unknown = set(data) - _PCS_E2A_REQUEST_FIELDS
    if unknown:
        raise _invalid_pcs_envelope("unknown top-level field")
    if data.get("protocol_version", E2A_PROTOCOL_VERSION) != E2A_PROTOCOL_VERSION:
        raise _invalid_pcs_envelope("unsupported protocol_version")
    for name in ("request_id", "channel", "method"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise _invalid_pcs_envelope(f"{name} must be non-empty")
    if not str(data["method"]).startswith("pcs."):
        raise _invalid_pcs_envelope("method must start with pcs.")
    if not isinstance(data.get("params"), dict):
        raise _invalid_pcs_envelope("params must be an object")
    if "is_stream" in data and type(data["is_stream"]) is not bool:
        raise _invalid_pcs_envelope("is_stream must be boolean")
    for name in _PCS_E2A_OPTIONAL_STRING_FIELDS:
        value = data.get(name)
        if value is not None and not isinstance(value, str):
            raise _invalid_pcs_envelope(f"{name} must be a string or null")
    for name in _PCS_E2A_OPTIONAL_OBJECT_FIELDS:
        value = data.get(name)
        if value is not None and not isinstance(value, dict):
            raise _invalid_pcs_envelope(f"{name} must be an object or null")
    origin = data.get("identity_origin")
    if origin is not None and origin not in {item.value for item in IdentityOrigin}:
        raise _invalid_pcs_envelope("identity_origin is invalid")
    provenance = data.get("provenance")
    if isinstance(provenance, dict):
        source_protocol = provenance.get("source_protocol", E2A_SOURCE_PROTOCOL_E2A)
        if source_protocol not in _PCS_E2A_SOURCE_PROTOCOLS:
            raise _invalid_pcs_envelope("provenance.source_protocol is invalid")
    if "timestamp" in data:
        timestamp = data["timestamp"]
        if not isinstance(timestamp, str):
            raise _invalid_pcs_envelope("timestamp must be RFC 3339")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _invalid_pcs_envelope("timestamp must be RFC 3339") from exc
        if parsed.tzinfo is None:
            raise _invalid_pcs_envelope("timestamp must include timezone")


def encode_pcs_request_for_wire(envelope: E2AEnvelope) -> dict[str, Any]:
    """Serialize one PCS request without the generic E2A extension fields."""

    if not isinstance(envelope, E2AEnvelope):
        raise TypeError("envelope must be E2AEnvelope")
    wire = {
        name: _json_safe(getattr(envelope, name))
        for name in _PCS_E2A_REQUEST_FIELDS
        if getattr(envelope, name) is not None
    }
    validate_pcs_e2a_request_dict(wire)
    return wire


def _raw_dict_to_agent_response(data: dict[str, Any]) -> AgentResponse:
    return AgentResponse(
        request_id=str(data["request_id"]),
        channel_id=str(data.get("channel_id", "")),
        ok=bool(data.get("ok", True)),
        payload=data.get("payload"),
        metadata=data.get("metadata"),
    )


def _raw_dict_to_agent_chunk(data: dict[str, Any]) -> AgentResponseChunk:
    return AgentResponseChunk(
        request_id=str(data["request_id"]),
        channel_id=str(data.get("channel_id", "")),
        payload=data.get("payload"),
        is_complete=bool(data.get("is_complete", False)),
    )


def is_e2a_response_wire_dict(data: dict[str, Any]) -> bool:
    """判别 JSON 对象是否为 E2A 响应线格式（与 ``E2AEnvelope`` 区分：须含非空 ``response_kind``）。"""
    if not isinstance(data, dict) or data.get("type") == "event":
        return False
    if data.get("protocol_version") != E2A_PROTOCOL_VERSION:
        return False
    rk = data.get("response_kind")
    return isinstance(rk, str) and bool(rk.strip())


def _deprecated_unary_shape(data: dict[str, Any]) -> bool:
    return (
        isinstance(data, dict)
        and "request_id" in data
        and "channel_id" in data
        and "ok" in data
        and not is_e2a_response_wire_dict(data)
    )


def _deprecated_chunk_shape(data: dict[str, Any]) -> bool:
    return (
        isinstance(data, dict)
        and "request_id" in data
        and "channel_id" in data
        and "is_complete" in data
        and "payload" in data
        and "ok" not in data
        and not is_e2a_response_wire_dict(data)
    )


def parse_agent_server_wire_unary(data: dict[str, Any]) -> AgentResponse:
    """将一条非流式 WebSocket JSON 解析为 ``AgentResponse``。"""
    rid = str(data.get("request_id", ""))
    if is_e2a_response_wire_dict(data):
        try:
            e2a = E2AResponse.from_dict(dict(data))
        except Exception as e:
            logger.exception(
                "[E2A][wire][in][FAIL] stage=from_dict unary request_id=%s err=%s",
                rid,
                e,
            )
            raise
        meta = dict(e2a.metadata or {})
        legacy = meta.get(E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY)
        if legacy is not None and isinstance(legacy, dict):
            logger.warning(
                "[E2A][wire][in][fallback] unary request_id=%s response_id=%s legacy_key=%s json_bytes≈%s",
                rid,
                e2a.response_id,
                E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY,
                len(str(legacy).encode("utf-8", errors="replace")),
            )
            return _raw_dict_to_agent_response(legacy)
        try:
            out = e2a_response_to_agent_response(e2a)
            logger.debug(
                "[E2A][wire][in] unary request_id=%s response_kind=%s",
                rid,
                e2a.response_kind,
            )
            return out
        except Exception as e:
            logger.exception(
                "[E2A][wire][in][FAIL] stage=inverse unary request_id=%s response_kind=%s err=%s",
                rid,
                e2a.response_kind,
                e,
            )
            legacy_inv = meta.get(E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY)
            if isinstance(legacy_inv, dict):
                logger.warning(
                    "[E2A][wire][in][fallback] unary inverse failed, using legacy blob request_id=%s",
                    rid,
                )
                return _raw_dict_to_agent_response(legacy_inv)
            raise

    if _deprecated_unary_shape(data):
        logger.warning(
            "[E2A][wire][in][deprecated_legacy_shape] unary request_id=%s keys=%s",
            rid,
            list(data.keys())[:24],
        )
        return _raw_dict_to_agent_response(data)

    raise ValueError(f"parse_agent_server_wire_unary: unrecognized wire shape keys={list(data.keys())[:32]}")


def parse_agent_server_wire_chunk(data: dict[str, Any]) -> AgentResponseChunk:
    """将一条流式 WebSocket JSON 解析为 ``AgentResponseChunk``。"""
    rid = str(data.get("request_id", ""))
    if is_e2a_response_wire_dict(data):
        try:
            e2a = E2AResponse.from_dict(dict(data))
        except Exception as e:
            logger.exception(
                "[E2A][wire][in][FAIL] stage=from_dict chunk request_id=%s err=%s",
                rid,
                e,
            )
            raise
        meta = dict(e2a.metadata or {})
        legacy = meta.get(E2A_WIRE_LEGACY_AGENT_CHUNK_KEY)
        if legacy is not None and isinstance(legacy, dict):
            logger.warning(
                "[E2A][wire][in][fallback] chunk request_id=%s response_id=%s legacy_key=%s json_bytes≈%s",
                rid,
                e2a.response_id,
                E2A_WIRE_LEGACY_AGENT_CHUNK_KEY,
                len(str(legacy).encode("utf-8", errors="replace")),
            )
            return _raw_dict_to_agent_chunk(legacy)
        try:
            out = e2a_response_to_agent_chunk(e2a)
            logger.debug(
                "[E2A][wire][in] chunk request_id=%s response_kind=%s is_final=%s",
                rid,
                e2a.response_kind,
                e2a.is_final,
            )
            return out
        except Exception as e:
            logger.exception(
                "[E2A][wire][in][FAIL] stage=inverse chunk request_id=%s response_kind=%s is_final=%s err=%s",
                rid,
                e2a.response_kind,
                e2a.is_final,
                e,
            )
            legacy_inv = meta.get(E2A_WIRE_LEGACY_AGENT_CHUNK_KEY)
            if isinstance(legacy_inv, dict):
                logger.warning(
                    "[E2A][wire][in][fallback] chunk inverse failed, using legacy blob request_id=%s",
                    rid,
                )
                return _raw_dict_to_agent_chunk(legacy_inv)
            raise

    if _deprecated_chunk_shape(data):
        logger.warning(
            "[E2A][wire][in][deprecated_legacy_shape] chunk request_id=%s keys=%s",
            rid,
            list(data.keys())[:24],
        )
        return _raw_dict_to_agent_chunk(data)

    raise ValueError(f"parse_agent_server_wire_chunk: unrecognized wire shape keys={list(data.keys())[:32]}")


def _parse_pcs_e2a_response(data: dict[str, Any]) -> E2AResponse:
    if not is_e2a_response_wire_dict(data):
        raise ValueError("PCS response must be canonical E2AResponse")
    e2a = E2AResponse.from_dict(dict(data))
    if set(e2a.metadata or {}) & E2A_WIRE_INTERNAL_METADATA_KEYS:
        raise ValueError("PCS response contains legacy metadata")
    return e2a


def parse_pcs_server_wire_unary(data: dict[str, Any]) -> AgentResponse:
    """Parse a PCS unary response without legacy shape or metadata fallback."""

    e2a = _parse_pcs_e2a_response(data)
    response = e2a_response_to_agent_response(e2a)
    response.agent_ref = e2a.agent_ref
    return response


def parse_pcs_server_wire_chunk(data: dict[str, Any]) -> AgentResponseChunk:
    """Parse a PCS response chunk without legacy shape or metadata fallback."""

    return e2a_response_to_agent_chunk(_parse_pcs_e2a_response(data))


def encode_pcs_response_for_wire(
    resp: AgentResponse,
    *,
    response_id: str,
    sequence: int = 0,
) -> dict[str, Any]:
    """Encode a PCS unary response without the generic legacy fallback."""

    e2a = e2a_response_from_agent_response(
        resp,
        response_id=response_id,
        sequence=sequence,
    )
    return _json_safe(e2a.to_dict())


def encode_pcs_chunk_for_wire(
    chunk: AgentResponseChunk,
    *,
    response_id: str,
    sequence: int,
    is_stream: bool = True,
) -> dict[str, Any]:
    """Encode a PCS response chunk without the generic legacy fallback."""

    e2a = e2a_response_from_agent_chunk(
        chunk,
        response_id=response_id,
        sequence=sequence,
        is_stream=is_stream,
    )
    return _json_safe(e2a.to_dict())


def encode_agent_response_for_wire(
    resp: AgentResponse,
    *,
    response_id: str,
    sequence: int = 0,
) -> dict[str, Any]:
    """``AgentResponse`` → E2A 线 dict；失败时 ``metadata`` 塞入整包 legacy 并记日志。"""
    rid = resp.request_id
    try:
        e2a = e2a_response_from_agent_response(
            resp, response_id=response_id, sequence=sequence
        )
        try:
            wire = e2a.to_dict()
        except Exception as te:
            logger.exception(
                "[E2A][wire][out][FAIL] stage=to_dict unary request_id=%s response_id=%s err=%s legacy_stashed=true",
                rid,
                response_id,
                te,
            )
            return _fallback_wire_unary_from_legacy(
                _json_safe(asdict(resp)),
                response_id=response_id,
                sequence=sequence,
                exc=te,
            )
        logger.info(
            "[E2A][wire][out] unary request_id=%s response_id=%s response_kind=%s legacy_stashed=false",
            rid,
            response_id,
            e2a.response_kind,
        )
        return _json_safe(wire)
    except Exception as e:
        logger.exception(
            "[E2A][wire][out][FAIL] stage=encode unary request_id=%s response_id=%s err=%s legacy_stashed=true",
            rid,
            response_id,
            e,
        )
        return _fallback_wire_unary_from_legacy(
            _json_safe(asdict(resp)),
            response_id=response_id,
            sequence=sequence,
            exc=e,
        )


def encode_agent_chunk_for_wire(
    chunk: AgentResponseChunk,
    *,
    response_id: str,
    sequence: int,
    is_stream: bool = True,
) -> dict[str, Any]:
    """``AgentResponseChunk`` → E2A 线 dict；失败时 ``metadata`` 塞入整包 legacy。"""
    rid = chunk.request_id
    try:
        e2a = e2a_response_from_agent_chunk(
            chunk,
            response_id=response_id,
            sequence=sequence,
            is_stream=is_stream,
        )
        try:
            wire = e2a.to_dict()
        except Exception as te:
            logger.exception(
                (
                    "[E2A][wire][out][FAIL] stage=to_dict chunk request_id=%s response_id=%s "
                    "seq=%s err=%s legacy_stashed=true"
                ),
                rid,
                response_id,
                sequence,
                te,
            )
            return _fallback_wire_chunk_from_legacy(
                _json_safe(asdict(chunk)),
                response_id=response_id,
                sequence=sequence,
                exc=te,
                is_stream=is_stream,
            )
        return _json_safe(wire)
    except Exception as e:
        logger.exception(
            "[E2A][wire][out][FAIL] stage=encode chunk request_id=%s response_id=%s seq=%s err=%s legacy_stashed=true",
            rid,
            response_id,
            sequence,
            e,
        )
        return _fallback_wire_chunk_from_legacy(
            _json_safe(asdict(chunk)),
            response_id=response_id,
            sequence=sequence,
            exc=e,
            is_stream=is_stream,
        )


def _fallback_wire_unary_from_legacy(
    legacy: dict[str, Any],
    *,
    response_id: str,
    sequence: int,
    exc: BaseException,
) -> dict[str, Any]:
    ts = utc_now_iso()
    prov = E2AProvenance(
        source_protocol=E2A_SOURCE_PROTOCOL_E2A,
        converter="jiuwenswarm.common.e2a.wire_codec:_fallback_wire_unary_from_legacy",
        converted_at=ts,
        details={"error": str(exc), "kind": "wire_encode_fallback"},
    )
    e2a = E2AResponse(
        protocol_version=E2A_PROTOCOL_VERSION,
        response_id=response_id,
        request_id=str(legacy.get("request_id", "")),
        sequence=sequence,
        is_final=True,
        status=E2A_RESPONSE_STATUS_FAILED,
        response_kind=E2A_RESPONSE_KIND_E2A_ERROR,
        timestamp=ts,
        provenance=prov,
        body={
            "code": "E2A.WIRE_ENCODE_ERROR",
            "message": "Failed to encode AgentResponse as E2A; see metadata legacy blob",
            "details": {"error": str(exc)},
        },
        channel=str(legacy.get("channel_id") or "") or None,
        metadata={E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY: legacy},
        identity_origin=IdentityOrigin.AGENT,
        is_stream=False,
    )
    return e2a.to_dict()


def _fallback_wire_chunk_from_legacy(
    legacy: dict[str, Any],
    *,
    response_id: str,
    sequence: int,
    exc: BaseException,
    is_stream: bool,
) -> dict[str, Any]:
    ts = utc_now_iso()
    prov = E2AProvenance(
        source_protocol=E2A_SOURCE_PROTOCOL_E2A,
        converter="jiuwenswarm.common.e2a.wire_codec:_fallback_wire_chunk_from_legacy",
        converted_at=ts,
        details={"error": str(exc), "kind": "wire_encode_chunk_fallback"},
    )
    e2a = E2AResponse(
        protocol_version=E2A_PROTOCOL_VERSION,
        response_id=response_id,
        request_id=str(legacy.get("request_id", "")),
        sequence=sequence,
        is_final=bool(legacy.get("is_complete", False)),
        status=E2A_RESPONSE_STATUS_FAILED,
        response_kind=E2A_RESPONSE_KIND_E2A_ERROR,
        timestamp=ts,
        provenance=prov,
        body={
            "code": "E2A.WIRE_ENCODE_ERROR",
            "message": "Failed to encode AgentResponseChunk as E2A; see metadata legacy blob",
            "details": {"error": str(exc)},
        },
        channel=str(legacy.get("channel_id") or "") or None,
        metadata={E2A_WIRE_LEGACY_AGENT_CHUNK_KEY: legacy},
        identity_origin=IdentityOrigin.AGENT,
        is_stream=is_stream,
    )
    return e2a.to_dict()


def encode_json_parse_error_wire(
    *,
    request_id: str,
    channel_id: str,
    message: str,
    response_id: str = "",
) -> dict[str, Any]:
    """入站 JSON 无法解析时发送的单帧 E2A 形错误（无 legacy blob）。"""
    ts = utc_now_iso()
    rid_out = response_id or (request_id or "invalid-json")
    e2a = E2AResponse(
        protocol_version=E2A_PROTOCOL_VERSION,
        response_id=rid_out,
        request_id=request_id or None,
        sequence=0,
        is_final=True,
        status=E2A_RESPONSE_STATUS_FAILED,
        response_kind=E2A_RESPONSE_KIND_E2A_ERROR,
        timestamp=ts,
        provenance=E2AProvenance(
            source_protocol=E2A_SOURCE_PROTOCOL_E2A,
            converter="jiuwenswarm.common.e2a.wire_codec:encode_json_parse_error_wire",
            converted_at=ts,
            details={"kind": "json_parse_error"},
        ),
        body={
            "code": "E2A.INVALID_JSON",
            "message": message,
            "details": {},
        },
        channel=channel_id or None,
        identity_origin=IdentityOrigin.AGENT,
        is_stream=False,
    )
    return e2a.to_dict()
