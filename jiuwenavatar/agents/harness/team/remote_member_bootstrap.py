# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wrap team spawn_member so remote blank claws receive a bootstrap envelope.

LLM still calls ``spawn_member`` only. For member names listed under
``team.metadata.jiuwen_remote_member_names``, after a successful DB insert we
publish a JSON payload via the normal team ``send_message`` channel so a
teammate process that is already running and subscribed can apply runtime
hints (transport topology, leader id, etc.).

Security: payload intentionally avoids DB credentials; it only mirrors
messager-facing fields already shared for pyzmq coordination.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import socket
import types
import uuid
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# team.metadata.jiuwen_remote_member_names: str | list[str]
_METADATA_REMOTE_NAMES_KEY = "jiuwen_remote_member_names"
_WRAPPED_ATTR = "_jiuwen_spawn_member_remote_bootstrap_wrapped"
_WRAPPED_TEAM_AGENT_ATTR = "_jiuwen_spawn_member_remote_bootstrap_team_agent"
_WRAPPED_SESSION_ID_ATTR = "_jiuwen_spawn_member_remote_bootstrap_session_id"
_WRAPPED_CHANNEL_ID_ATTR = "_jiuwen_spawn_member_remote_bootstrap_channel_id"
_WRAPPED_REMOTE_NAMES_ATTR = "_jiuwen_spawn_member_remote_bootstrap_remote_names"
_WRAPPED_REMOTE_ALL_ATTR = "_jiuwen_spawn_member_remote_bootstrap_remote_all"
_LOCAL_SPAWN_GUARD_ATTR = "_jiuwen_distributed_local_spawn_guard_attached"
_SEND_MESSAGE_GUARDED_ATTR = "_jiuwen_distributed_send_message_guarded"
_ACK_LISTENER_ATTR = "_jiuwen_remote_bootstrap_ack_listener_attached"
_TEAMMATE_BOOTSTRAP_LISTENER_ATTR = "_jiuwen_remote_teammate_bootstrap_listener_attached"
_SHUTDOWN_CLEANUP_WRAPPED_ATTR = "_jiuwen_shutdown_member_remote_cleanup_wrapped"
_SHUTDOWN_CLEANUP_SESSION_ID_ATTR = "_jiuwen_shutdown_member_remote_cleanup_session_id"
_SHUTDOWN_CLEANUP_CHANNEL_ID_ATTR = "_jiuwen_shutdown_member_remote_cleanup_channel_id"
_METADATA_REMOTE_ALL_KEY = "jiuwen_remote_all_spawn_members"
_A2X_RESERVATIONS_BY_SESSION: dict[str, list[tuple[str, Any, dict[str, Any]]]] = {}
_SHUTDOWN_CLEANUP_TASKS: dict[str, asyncio.Task] = {}

# Remote claw → leader: JSON body on a normal team P2P message (DB + MESSAGE topic).
REMOTE_BOOTSTRAP_ACK_TYPE = "jiuwen.remote_bootstrap_ack"
REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE = "jiuwen.remote_teammate_bootstrap.direct"
REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE = "jiuwen.remote_team_destroy.direct"
_TRANSPORT_BOOTSTRAP_DIRECT_ADDR_KEY = "bootstrap_direct_addr"
_TRANSPORT_BOOTSTRAP_KNOWN_PEERS_KEY = "bootstrap_known_peers"

_DYNAMIC_MEMBER_AGENTS: dict[tuple[str, str], Any] = {}


class RemoteSpawnPrecheck(NamedTuple):
    """Result for remote spawn fail-fast validation."""

    error: str | None = None
    registry_reservation: Any | None = None


def remote_member_names(config_base: dict[str, Any] | None = None) -> set[str]:
    """Member slugs treated as externally-hosted teammates (post-spawn bootstrap)."""
    if config_base is None:
        from jiuwenavatar.common.config import get_config as _get_config

        config_base = _get_config()
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    meta = team.get("metadata") if isinstance(team.get("metadata"), dict) else {}
    raw = meta.get(_METADATA_REMOTE_NAMES_KEY)
    if raw is None:
        return set()
    if isinstance(raw, str) and raw.strip():
        return {raw.strip()}
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def remote_all_spawn_members(config_base: dict[str, Any] | None = None) -> bool:
    """Whether distributed leader treats every ``spawn_member`` as remote."""
    if config_base is None:
        from jiuwenavatar.common.config import get_config as _get_config

        config_base = _get_config()
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    runtime_mode = str(runtime.get("mode", "")).strip().lower()
    if runtime_mode == "distributed":
        # In distributed mode, prefer remote takeover by default.
        raw = team.get("metadata") if isinstance(team.get("metadata"), dict) else {}
        if isinstance(raw, dict) and _METADATA_REMOTE_ALL_KEY in raw:
            return bool(raw.get(_METADATA_REMOTE_ALL_KEY))
        return True
    return False


def _is_distributed_leader_runtime(config_base: dict[str, Any]) -> bool:
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    mode = str(runtime.get("mode", "")).strip().lower()
    role = str(runtime.get("role", "")).strip().lower()
    return mode == "distributed" and role == "leader"


def _team_agent_deep_agent(team_agent: Any) -> Any | None:
    """Return the underlying DeepAgent across openjiuwen TeamAgent variants."""
    deep_agent = getattr(team_agent, "deep_agent", None)
    if deep_agent is not None:
        return deep_agent

    harness = getattr(team_agent, "harness", None)
    if harness is None:
        return None

    deep_agent = getattr(harness, "inner_agent", None)
    if deep_agent is not None:
        return deep_agent

    return getattr(harness, "_deep_agent", None)


def _spawn_member_tool_id(leader_deep_agent: Any) -> str:
    """Resolve qualified tool id (inprocess mode rewrites card ids)."""
    try:
        for card in leader_deep_agent.ability_manager.list() or []:
            cid = getattr(card, "id", "") or ""
            if cid == "team.spawn_member" or cid.startswith("team.spawn_member."):
                return cid
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] spawn_member tool id resolve failed: %s", exc)
    return "team.spawn_member"


def _team_tool_id(leader_deep_agent: Any, tool_name: str) -> str:
    """Resolve a registered team tool id by public tool name."""
    expected = f"team.{tool_name}"
    try:
        for card in leader_deep_agent.ability_manager.list() or []:
            cid = getattr(card, "id", "") or ""
            name = getattr(card, "name", "") or ""
            if cid == expected or cid.startswith(f"{expected}.") or name == tool_name:
                return cid or expected
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] %s tool id resolve failed: %s", tool_name, exc)
    return expected


def _is_distributed_leader_runtime(config_base: dict[str, Any]) -> bool:
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    mode = str(runtime.get("mode", "")).strip().lower()
    role = str(runtime.get("role", "")).strip().lower()
    return mode == "distributed" and role == "leader"


def _messager_bootstrap_dict(team_agent: Any) -> dict[str, Any]:
    ctx = team_agent.runtime_context
    if ctx is None or ctx.messager_config is None:
        return {}
    try:
        return ctx.messager_config.model_dump(mode="json")
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] messager_config dump failed: %s", exc)
        return {}


def _transport_params_from_config(config_base: dict[str, Any] | None = None) -> dict[str, Any]:
    if config_base is None:
        from jiuwenavatar.common.config import get_config as _get_config

        config_base = _get_config()
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    transport = team.get("transport") if isinstance(team.get("transport"), dict) else {}
    params = transport.get("params") if isinstance(transport.get("params"), dict) else {}
    return params if isinstance(params, dict) else {}


def _resolve_bootstrap_peer_for_member(member_name: str, config_base: dict[str, Any] | None = None) -> tuple[str, str]:
    """Resolve (agent_id, addr) for bootstrap control-plane message."""
    params = _transport_params_from_config(config_base)
    requested = str(member_name or "").strip()

    def _iter_peers(key: str) -> list[dict[str, Any]]:
        raw = params.get(key)
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        return []

    def _pick(peers: list[dict[str, Any]]) -> tuple[str, str]:
        if not peers:
            return "", ""
        for peer in peers:
            alias = str(peer.get("member_name", "")).strip()
            if requested and alias and alias == requested:
                agent_id = str(peer.get("agent_id", "")).strip()
                addrs = peer.get("addrs") if isinstance(peer.get("addrs"), list) else []
                addr = _normalize_leader_direct_addr(addrs[0]) if addrs else ""
                if agent_id and addr:
                    return agent_id, addr
        for peer in peers:
            agent_id = str(peer.get("agent_id", "")).strip()
            addrs = peer.get("addrs") if isinstance(peer.get("addrs"), list) else []
            addr = _normalize_leader_direct_addr(addrs[0]) if addrs else ""
            if requested and requested == agent_id and addr:
                return agent_id, addr
        first = peers[0]
        agent_id = str(first.get("agent_id", "")).strip()
        addrs = first.get("addrs") if isinstance(first.get("addrs"), list) else []
        addr = _normalize_leader_direct_addr(addrs[0]) if addrs else ""
        return (agent_id, addr) if agent_id and addr else ("", "")

    agent_id, addr = _pick(_iter_peers(_TRANSPORT_BOOTSTRAP_KNOWN_PEERS_KEY))
    if agent_id and addr:
        return agent_id, addr
    return _pick(_iter_peers("known_peers"))


def _team_name_for_agent(team_agent: Any) -> str:
    team_name_fn = getattr(team_agent, "_team_name", None)
    if callable(team_name_fn):
        try:
            name = team_name_fn()
            if isinstance(name, str) and name.strip():
                return name.strip()
        except Exception as exc:
            logger.debug("[RemoteMemberBootstrap] _team_name lookup failed: %s", exc)
    tb = getattr(team_agent, "team_backend", None)
    name = getattr(tb, "team_name", "") if tb is not None else ""
    return str(name or "").strip()


async def _existing_team_member(team_agent: Any | None, member_name: str) -> Any | None:
    if team_agent is None:
        return None
    tb = getattr(team_agent, "team_backend", None)
    if tb is None:
        return None
    get_member = getattr(tb, "get_member", None)
    if not callable(get_member):
        return None
    try:
        return await get_member(member_name)
    except TypeError:
        team_name = _team_name_for_agent(team_agent)
        if not team_name:
            return None
        return await get_member(member_name, team_name)


def _validate_remote_spawn_inputs(inputs: dict[str, Any] | None) -> str | None:
    data = inputs or {}
    member_name = data.get("member_name")
    if not isinstance(member_name, str) or not member_name.strip():
        return "member_name is required for remote spawn"
    display_name = data.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        return "display_name is required for remote spawn"
    desc = data.get("desc")
    if desc is None or not str(desc).strip():
        return "desc is required for remote spawn"
    return None


async def _release_registry_reservation(
    reservation: Any | None,
    *,
    member_name: str,
    reason: str,
) -> None:
    if reservation is None:
        return
    logger.warning(
        "[RemoteMemberBootstrap] releasing A2X reservation member=%s service_id=%s endpoint=%s reason=%s",
        member_name,
        getattr(reservation, "service_id", ""),
        getattr(reservation, "endpoint", ""),
        reason,
    )
    for method_name in ("release", "close"):
        method = getattr(reservation, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            logger.warning(
                "[RemoteMemberBootstrap] A2X reservation %s failed member=%s: %s",
                method_name,
                member_name,
                exc,
            )


async def precheck_and_reserve_remote_spawn(
    member_name: str,
    config_base: dict[str, Any] | None = None,
    *,
    team_agent: Any | None = None,
) -> RemoteSpawnPrecheck:
    """Reserve a blank remote teammate before spawn_member mutates the team DB."""
    key = str(member_name or "").strip()
    if not key:
        return RemoteSpawnPrecheck(error="Remote member name is required.")

    existing = await _existing_team_member(team_agent, key)
    if existing is not None:
        team_name = _team_name_for_agent(team_agent) if team_agent is not None else ""
        suffix = f" in team {team_name}" if team_name else ""
        return RemoteSpawnPrecheck(error=f"Remote member {key} already exists{suffix}.")

    if config_base is None:
        from jiuwenavatar.common.config import get_config as _get_config

        config_base = _get_config()

    try:
        from jiuwenavatar.agents.harness.team.a2x.a2x_registry_runtime import reserve_blank_teammate_agent

        reservation = await reserve_blank_teammate_agent(
            config_base,
            source="leader-spawn-precheck",
        )
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] A2X blank teammate precheck failed member=%s: %s",
            key,
            exc,
        )
        return RemoteSpawnPrecheck(
            error=f"Failed to reserve blank teammate for remote member {key}: {exc}"
        )

    if reservation is None:
        return RemoteSpawnPrecheck(
            error=f"No blank teammate available to host remote member {key}."
        )
    return RemoteSpawnPrecheck(registry_reservation=reservation)


def _normalize_leader_agent_id(raw: Any, *, team_name: str, leader_member_name: str) -> str:
    """Return a non-empty, stable leader peer id for teammate route registration."""
    value = str(raw or "").strip()
    if value and value.lower() not in {"none", "null"}:
        return value
    tname = str(team_name or "").strip() or "jiuwen_team"
    lname = str(leader_member_name or "").strip() or "team_leader"
    return f"{tname}_{lname}"


def _normalize_leader_direct_addr(raw: Any) -> str:
    """Normalize leader direct addr to a connectable host for remote teammate."""
    value = str(raw or "").strip()
    if not value:
        return ""
    # direct_addr is often configured as bind addr 0.0.0.0; peers must dial a real host.
    return re.sub(r"^tcp://0\.0\.0\.0(?=[:/]|$)", "tcp://127.0.0.1", value)


def build_bootstrap_ack_envelope(
    *,
    member_name: str,
    team_name: str | None = None,
    leader_agent_id: str | None = None,
    leader_direct_addr: str | None = None,
    handshake_applied: bool | None = None,
    version: int = 1,
) -> dict[str, Any]:
    """Payload for a teammate→leader message after remote bootstrap is applied (optional team_name)."""
    body: dict[str, Any] = {
        "type": REMOTE_BOOTSTRAP_ACK_TYPE,
        "version": version,
        "member_name": member_name.strip(),
    }
    if team_name:
        body["team_name"] = team_name
    if leader_agent_id:
        body["leader_agent_id"] = leader_agent_id
    if leader_direct_addr:
        body["leader_direct_addr"] = leader_direct_addr
    if isinstance(handshake_applied, bool):
        body["handshake_applied"] = handshake_applied
    return body


def parse_remote_bootstrap_ack_json(content: str) -> dict[str, Any] | None:
    """If ``content`` is a valid ACK JSON envelope, return the dict; else None."""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("type") != REMOTE_BOOTSTRAP_ACK_TYPE:
        return None
    if int(data.get("version", 1)) != 1:
        return None
    mn = data.get("member_name")
    if not isinstance(mn, str) or not mn.strip():
        return None
    return data


def parse_remote_teammate_bootstrap_json(content: str) -> dict[str, Any] | None:
    """If ``content`` is a valid teammate-bootstrap envelope, return dict; else None."""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("type") != "jiuwen.remote_teammate_bootstrap":
        return None
    if int(data.get("version", 1)) != 1:
        return None
    member_name = data.get("member_name")
    if not isinstance(member_name, str) or not member_name.strip():
        return None
    return data


def build_bootstrap_envelope(
    team_agent: Any,
    *,
    session_id: str,
    member_name: str,
    prompt: str | None,
) -> dict[str, Any]:
    spec = team_agent.spec
    ctx = team_agent.runtime_context
    team_spec = ctx.team_spec if ctx else None
    team_name = (team_spec.team_name if team_spec else None) or (spec.team_name if spec else "")
    leader_member_name = (team_spec.leader_member_name if team_spec else None) or (
        spec.leader.member_name if spec and spec.leader else None
    )
    messager = _messager_bootstrap_dict(team_agent)
    leader_agent_id = _normalize_leader_agent_id(
        messager.get("node_id"),
        team_name=team_name,
        leader_member_name=str(leader_member_name or ""),
    )
    leader_direct_addr = _normalize_leader_direct_addr(messager.get("direct_addr"))
    return {
        "type": "jiuwen.remote_teammate_bootstrap",
        "version": 1,
        "bootstrap_id": str(uuid.uuid4()),
        "team_name": team_name,
        "session_id": str(session_id or "").strip(),
        "member_name": member_name,
        "leader_member_name": leader_member_name,
        "leader_agent_id": leader_agent_id,
        "leader_direct_addr": leader_direct_addr,
        "messager": messager,
        "prompt": prompt or "",
    }


def build_team_destroy_envelope(
    team_agent: Any,
    *,
    session_id: str,
    member_name: str,
    reservation: Any | None = None,
) -> dict[str, Any]:
    """Payload sent from leader to a remote teammate before team teardown."""
    spec = getattr(team_agent, "spec", None)
    ctx = getattr(team_agent, "runtime_context", None)
    team_spec = getattr(ctx, "team_spec", None) if ctx else None
    team_name = (getattr(team_spec, "team_name", None) if team_spec else None) or (
        getattr(spec, "team_name", "") if spec else ""
    )
    leader_member_name = (getattr(team_spec, "leader_member_name", None) if team_spec else None) or (
        spec.leader.member_name if spec and getattr(spec, "leader", None) else None
    )
    messager = _messager_bootstrap_dict(team_agent)
    leader_agent_id = _normalize_leader_agent_id(
        messager.get("node_id"),
        team_name=team_name,
        leader_member_name=str(leader_member_name or ""),
    )
    body = {
        "type": "jiuwen.remote_team_destroy",
        "version": 1,
        "destroy_id": str(uuid.uuid4()),
        "team_name": team_name,
        "session_id": str(session_id or "").strip(),
        "member_name": str(member_name or "").strip(),
        "leader_member_name": leader_member_name,
        "leader_agent_id": leader_agent_id,
    }
    if reservation is not None:
        body["registry"] = {
            "dataset": str(getattr(reservation, "dataset", "") or "").strip(),
            "service_id": str(getattr(reservation, "service_id", "") or "").strip(),
            "endpoint": _normalize_leader_direct_addr(getattr(reservation, "endpoint", "")),
        }
    return body


def _apply_leader_route_from_envelope(team_agent: Any, envelope: dict[str, Any]) -> bool:
    """Best-effort dynamic route registration so blank teammate can reply to leader."""
    leader_agent_id = str(envelope.get("leader_agent_id", "")).strip()
    leader_direct_addr = _normalize_leader_direct_addr(envelope.get("leader_direct_addr"))
    if (
        not leader_agent_id
        or leader_agent_id.lower() in {"none", "null"}
        or not leader_direct_addr
    ):
        return False
    messager = getattr(team_agent, "_messager", None) or getattr(team_agent, "mailbox_transport", None)
    register = getattr(messager, "register_peer", None)
    if not callable(register):
        return False
    try:
        from openjiuwen.agent_teams.messager.base import MessagerPeerConfig

        register(MessagerPeerConfig(agent_id=leader_agent_id, addrs=[leader_direct_addr]))
        logger.info(
            "[RemoteMemberBootstrap] teammate applied leader route agent_id=%s addr=%s",
            leader_agent_id,
            leader_direct_addr,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] teammate failed to apply leader route agent_id=%s addr=%s: %s",
            leader_agent_id,
            leader_direct_addr,
            exc,
        )
        return False


async def _send_bootstrap_via_raw_zmq(
    *,
    peer_addr: str,
    envelope: dict[str, Any],
    member_name: str,
    peer_agent_id: str,
    timeout_s: float = 20.0,
    event_type: str = REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE,
    log_label: str = "bootstrap",
    id_field: str = "bootstrap_id",
) -> bool:
    """Send a control-plane event directly to a teammate bootstrap ROUTER endpoint."""
    try:
        import zmq
        import zmq.asyncio
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] raw ZMQ %s unavailable: %s", log_label, exc)
        return False

    addr = _normalize_leader_direct_addr(peer_addr)
    if not addr:
        return False

    ctx = zmq.asyncio.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        sock.connect(addr)
        payload = {
            "event_type": event_type,
            "payload": {"envelope": envelope},
            "sender_id": str(envelope.get("leader_member_name") or ""),
        }
        await sock.send_multipart([json.dumps(payload).encode("utf-8")])
        frames = await asyncio.wait_for(sock.recv_multipart(), timeout=max(0.2, timeout_s))
        if not any(frame == b"ok" for frame in frames):
            logger.warning(
                "[RemoteMemberBootstrap] raw ZMQ %s unexpected ACK member=%s "
                "peer_agent_id=%s peer_addr=%s frames=%s",
                log_label,
                member_name,
                peer_agent_id,
                addr,
                frames,
            )
            return False
        logger.info(
            "[RemoteMemberBootstrap] pushed %s via raw ZMQ member=%s "
            "peer_agent_id=%s peer_addr=%s %s=%s",
            log_label,
            member_name,
            peer_agent_id,
            addr,
            id_field,
            envelope.get(id_field),
        )
        return True
    except asyncio.TimeoutError:
        logger.warning(
            "[RemoteMemberBootstrap] raw ZMQ %s timed out member=%s "
            "peer_agent_id=%s peer_addr=%s timeout=%.1fs",
            log_label,
            member_name,
            peer_agent_id,
            addr,
            timeout_s,
        )
        return False
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] raw ZMQ %s failed member=%s peer_agent_id=%s "
            "peer_addr=%s: %s",
            log_label,
            member_name,
            peer_agent_id,
            addr,
            exc,
        )
        return False
    finally:
        sock.close(linger=0)


async def _send_bootstrap_message(
    team_agent: Any,
    session_id: str,
    member_name: str,
    prompt: str | None,
    *,
    registry_reservation: Any | None = None,
) -> bool:
    mm = team_agent.message_manager
    messager = getattr(team_agent, "_messager", None) or getattr(team_agent, "mailbox_transport", None)
    if mm is None:
        logger.warning(
            "[RemoteMemberBootstrap] no message_manager; cannot notify member=%s",
            member_name,
        )
        return False
    envelope = build_bootstrap_envelope(
        team_agent,
        session_id=session_id,
        member_name=member_name,
        prompt=prompt,
    )
    peer_agent_id = ""
    peer_addr = ""
    if registry_reservation is not None:
        peer_agent_id = registry_reservation.service_id
        peer_addr = _normalize_leader_direct_addr(registry_reservation.endpoint)
        envelope["a2x_dataset"] = getattr(registry_reservation, "dataset", "")
        envelope["a2x_service_id"] = getattr(registry_reservation, "service_id", "")
    else:
        try:
            from jiuwenavatar.common.config import get_config as _get_config
            from jiuwenavatar.agents.harness.team.a2x.a2x_registry_runtime import reserve_blank_teammate_agent

            registry_reservation = await reserve_blank_teammate_agent(
                _get_config(),
                source="leader-spawn-member",
            )
            if registry_reservation is not None:
                peer_agent_id = registry_reservation.service_id
                peer_addr = _normalize_leader_direct_addr(registry_reservation.endpoint)
                envelope["a2x_dataset"] = getattr(registry_reservation, "dataset", "")
                envelope["a2x_service_id"] = getattr(registry_reservation, "service_id", "")
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] A2X blank teammate reservation failed: %s", exc)

    if not peer_agent_id or not peer_addr:
        peer_agent_id, peer_addr = _resolve_bootstrap_peer_for_member(member_name)

    direct_sent = False
    direct_send_error = None
    if messager is not None and peer_agent_id and peer_addr:
        try:
            from openjiuwen.agent_teams.messager.base import MessagerPeerConfig
            from openjiuwen.agent_teams.schema.events import EventMessage

            register = getattr(messager, "register_peer", None)
            send = getattr(messager, "send", None)
            if callable(register) and callable(send):
                register(MessagerPeerConfig(agent_id=peer_agent_id, addrs=[peer_addr]))
                control_event = EventMessage(
                    event_type=REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE,
                    payload={"envelope": envelope},
                    sender_id=str(envelope.get("leader_member_name") or ""),
                )
                await send(peer_agent_id, control_event)
                direct_sent = True
                logger.info(
                    "[RemoteMemberBootstrap] pushed bootstrap via direct control-plane member=%s "
                    "peer_agent_id=%s peer_addr=%s bootstrap_id=%s",
                    member_name,
                    peer_agent_id,
                    peer_addr,
                    envelope.get("bootstrap_id"),
                )
        except Exception as exc:
            direct_send_error = exc
            logger.warning(
                "[RemoteMemberBootstrap] direct bootstrap send failed member=%s peer_agent_id=%s peer_addr=%s: %s",
                member_name,
                peer_agent_id,
                peer_addr,
                exc,
            )
    if (not direct_sent) and peer_agent_id and peer_addr:
        try:
            direct_sent = await _send_bootstrap_via_raw_zmq(
                peer_addr=peer_addr,
                envelope=envelope,
                member_name=member_name,
                peer_agent_id=peer_agent_id,
            )
        except Exception as exc:
            logger.warning(
                "[RemoteMemberBootstrap] raw ZMQ bootstrap send failed member=%s "
                "peer_agent_id=%s peer_addr=%s: %s",
                member_name,
                peer_agent_id,
                peer_addr,
                exc,
            )
    if direct_sent:
        if registry_reservation is not None:
            logger.info(
                "[RemoteMemberBootstrap] keeping A2X reservation after bootstrap "
                "member=%s service_id=%s endpoint=%s",
                member_name,
                registry_reservation.service_id,
                registry_reservation.endpoint,
            )
            _remember_a2x_reservation(
                team_agent,
                session_id,
                member_name,
                registry_reservation,
            )
        return True

    if registry_reservation is not None:
        logger.warning(
            "[RemoteMemberBootstrap] releasing A2X reservation after bootstrap delivery failure "
            "member=%s service_id=%s endpoint=%s",
            member_name,
            registry_reservation.service_id,
            registry_reservation.endpoint,
        )
        await registry_reservation.release()
        await registry_reservation.close()

    logger.warning(
        "[RemoteMemberBootstrap] direct bootstrap not delivered; DB fallback disabled "
        "member=%s peer_agent_id=%s peer_addr=%s has_messager=%s send_error=%s",
        member_name,
        peer_agent_id,
        peer_addr,
        bool(messager is not None),
        direct_send_error,
    )
    return False


async def send_bootstrap_message(
    team_agent: Any,
    session_id: str,
    member_name: str,
    prompt: str | None,
    **kwargs: Any,
) -> bool:
    return await _send_bootstrap_message(
        team_agent,
        session_id,
        member_name,
        prompt,
        registry_reservation=kwargs.get("registry_reservation"),
    )


def _remember_a2x_reservation(
    team_agent: Any,
    session_id: str,
    member_name: str,
    reservation: Any,
) -> None:
    key = str(session_id or "").strip()
    if not key:
        logger.warning(
            "[RemoteMemberBootstrap] skip A2X reservation tracking: missing session_id member=%s",
            member_name,
        )
        return
    destroy_envelope = build_team_destroy_envelope(
        team_agent,
        session_id=key,
        member_name=member_name,
        reservation=reservation,
    )
    reservations = _A2X_RESERVATIONS_BY_SESSION.setdefault(key, [])
    reservations.append((member_name, reservation, destroy_envelope))
    logger.info(
        "[RemoteMemberBootstrap] tracked A2X reservation for session teardown "
        "session_id=%s member=%s service_id=%s endpoint=%s",
        key,
        member_name,
        getattr(reservation, "service_id", ""),
        getattr(reservation, "endpoint", ""),
    )


async def _notify_reserved_teammate_team_destroy(
    team_agent: Any | None,
    member_name: str,
    reservation: Any,
    envelope: dict[str, Any],
) -> None:
    messager = None
    if team_agent is not None:
        messager = getattr(team_agent, "_messager", None) or getattr(team_agent, "mailbox_transport", None)
    peer_agent_id = str(getattr(reservation, "service_id", "") or "").strip()
    peer_addr = _normalize_leader_direct_addr(getattr(reservation, "endpoint", ""))
    if not peer_agent_id or not peer_addr:
        logger.debug(
            "[RemoteMemberBootstrap] skip team destroy notify member=%s has_messager=%s "
            "peer_agent_id=%s peer_addr=%s",
            member_name,
            bool(messager is not None),
            peer_agent_id,
            peer_addr,
        )
        return
    delivered = False
    try:
        from openjiuwen.agent_teams.messager.base import MessagerPeerConfig
        from openjiuwen.agent_teams.schema.events import EventMessage

        register = getattr(messager, "register_peer", None)
        send = getattr(messager, "send", None)
        if messager is not None and callable(register) and callable(send):
            register(MessagerPeerConfig(agent_id=peer_agent_id, addrs=[peer_addr]))
            control_event = EventMessage(
                event_type=REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE,
                payload={"envelope": envelope},
                sender_id=str(envelope.get("leader_member_name") or ""),
            )
            await send(peer_agent_id, control_event)
            delivered = True
            logger.info(
                "[RemoteMemberBootstrap] notified remote teammate to restore blank state before team destroy "
                "member=%s service_id=%s endpoint=%s destroy_id=%s",
                member_name,
                peer_agent_id,
                peer_addr,
                envelope.get("destroy_id"),
            )
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] remote teammate team destroy notify failed "
            "member=%s service_id=%s endpoint=%s: %s",
            member_name,
            peer_agent_id,
            peer_addr,
            exc,
        )
    if not delivered:
        await _send_bootstrap_via_raw_zmq(
            peer_addr=peer_addr,
            envelope=envelope,
            member_name=member_name,
            peer_agent_id=peer_agent_id,
            event_type=REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE,
            log_label="team destroy",
            id_field="destroy_id",
        )


async def release_a2x_reservations_for_session(
    session_id: str,
    *,
    team_agent: Any | None = None,
) -> None:
    """Notify reserved teammates for a distributed session teardown and close registry clients."""
    from jiuwenavatar.common.config import get_config as _get_config

    if not _is_distributed_leader_runtime(_get_config()):
        logger.debug(
            "[RemoteMemberBootstrap] non-distributed leader runtime; skip A2X reservation release "
            "session_id=%s",
            session_id,
        )
        return

    key = str(session_id or "").strip()
    reservations = _A2X_RESERVATIONS_BY_SESSION.pop(key, [])
    if not reservations:
        logger.debug("[RemoteMemberBootstrap] no A2X reservations to release session_id=%s", key)
        return

    for member_name, reservation, destroy_envelope in reservations:
        await _notify_reserved_teammate_team_destroy(
            team_agent,
            member_name,
            reservation,
            destroy_envelope,
        )
        close = getattr(reservation, "close", None)
        if callable(close):
            await close()
    logger.info(
        "[RemoteMemberBootstrap] released A2X reservations for distributed session teardown "
        "session_id=%s count=%d",
        key,
        len(reservations),
    )


async def _ensure_remote_member_record(
    team_agent: Any,
    member_name: str,
    inputs: dict[str, Any] | None,
) -> None:
    """Ensure the active team DB has the remotely spawned member row."""
    tb = getattr(team_agent, "team_backend", None)
    if tb is None:
        return
    spawn_member = getattr(tb, "spawn_member", None)
    if not callable(spawn_member):
        return
    get_member = getattr(tb, "get_member", None)
    if callable(get_member):
        existing = await get_member(member_name)
        if existing is not None:
            return

    from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberMode, MemberStatus
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    data = inputs or {}
    display_name = str(data.get("display_name") or member_name).strip() or member_name
    desc = str(data.get("desc") or data.get("description") or "").strip() or None
    prompt = data.get("prompt")
    if prompt is not None:
        prompt = str(prompt)

    _tn = getattr(team_agent, "_team_name", None)
    team_name = _tn() if callable(_tn) else getattr(tb, "team_name", "")
    card = AgentCard(
        id=f"{team_name}_{member_name}" if team_name else member_name,
        name=display_name,
        description=desc or display_name,
    )
    teammate_mode = getattr(tb, "teammate_mode", MemberMode.BUILD_MODE)
    if not isinstance(teammate_mode, MemberMode):
        teammate_mode = MemberMode(str(teammate_mode or MemberMode.BUILD_MODE.value))

    result = await spawn_member(
        member_name=member_name,
        display_name=display_name,
        agent_card=card,
        desc=desc,
        prompt=prompt,
        status=MemberStatus.UNSTARTED,
        execution_status=ExecutionStatus.IDLE,
        mode=teammate_mode,
    )
    if not bool(getattr(result, "success", result)):
        logger.warning(
            "[RemoteMemberBootstrap] failed to ensure remote member row member=%s team=%s result=%s",
            member_name,
            team_name,
            result,
        )
    else:
        logger.info(
            "[RemoteMemberBootstrap] ensured remote member row member=%s team=%s before bootstrap",
            member_name,
            team_name,
        )


def attach_spawn_member_remote_bootstrap_wrapper(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None,
) -> None:
    """Monkey-patch SpawnMemberTool.invoke on the leader's registered tool instance."""
    from jiuwenavatar.common.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_leader_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed leader runtime; skip spawn_member wrapper")
        return

    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    if getattr(team_agent, "role", None) != TeamRole.LEADER:
        return
    leader = _team_agent_deep_agent(team_agent)
    if leader is None:
        logger.debug("[RemoteMemberBootstrap] skip spawn_member wrapper: missing leader DeepAgent")
        return

    tool_id = _spawn_member_tool_id(leader)
    tag = getattr(getattr(leader, "card", None), "id", None)
    tool = Runner.resource_mgr.get_tool(tool_id, tag=tag) if tag else None
    if tool is None:
        tool = Runner.resource_mgr.get_tool(tool_id)
    if tool is None:
        logger.debug(
            "[RemoteMemberBootstrap] tool %s not in Runner.resource_mgr (session_id=%s channel=%s)",
            tool_id,
            session_id,
            channel_id,
        )
        return
    remote_names = remote_member_names(config_base)
    remote_all = remote_all_spawn_members(config_base)
    if not remote_names and not remote_all:
        logger.debug("[RemoteMemberBootstrap] no jiuwen_remote_member_names; skip wrapper")
        return

    setattr(tool, _WRAPPED_TEAM_AGENT_ATTR, team_agent)
    setattr(tool, _WRAPPED_SESSION_ID_ATTR, session_id)
    setattr(tool, _WRAPPED_CHANNEL_ID_ATTR, channel_id)
    setattr(tool, _WRAPPED_REMOTE_NAMES_ATTR, set(remote_names))
    setattr(tool, _WRAPPED_REMOTE_ALL_ATTR, remote_all)
    if getattr(tool, _WRAPPED_ATTR, False):
        logger.info(
            "[RemoteMemberBootstrap] rebound spawn_member wrapper tool_id=%s session_id=%s channel=%s "
            "remote=%s remote_all=%s",
            tool_id,
            session_id,
            channel_id,
            sorted(remote_names),
            remote_all,
        )
        return

    orig_invoke = tool.invoke

    async def wrapped_invoke(self: Any, inputs: dict[str, Any], **kwargs: Any) -> Any:
        from openjiuwen.harness.tools.base_tool import ToolOutput

        registry_reservation = None
        key = ""
        active_team_agent = getattr(self, _WRAPPED_TEAM_AGENT_ATTR, team_agent)
        active_session_id = str(
            getattr(self, _WRAPPED_SESSION_ID_ATTR, session_id) or session_id
        ).strip() or session_id
        try:
            mname = (inputs or {}).get("member_name")
            if not isinstance(mname, str):
                logger.info(
                    "[RemoteMemberBootstrap] spawn_member missing member_name(str); skip remote bootstrap: inputs=%s",
                    inputs,
                )
                return await orig_invoke(inputs, **kwargs)

            key = mname.strip()
            active_remote_names = getattr(self, _WRAPPED_REMOTE_NAMES_ATTR, remote_names)
            if not isinstance(active_remote_names, set):
                active_remote_names = set(active_remote_names or [])
            active_remote_all = bool(getattr(self, _WRAPPED_REMOTE_ALL_ATTR, remote_all))
            if (not active_remote_all) and key not in active_remote_names:
                logger.info(
                    "[RemoteMemberBootstrap] spawn_member member=%s not remote target; no remote claw bootstrap "
                    "(remote_all=%s remote_names=%s)",
                    key,
                    active_remote_all,
                    sorted(active_remote_names),
                )
                return await orig_invoke(inputs, **kwargs)

            logger.info(
                "[RemoteMemberBootstrap] spawn_member member=%s entering remote claw bootstrap path "
                "(remote_all=%s remote_names=%s)",
                key,
                active_remote_all,
                sorted(active_remote_names),
            )
            precheck = await precheck_and_reserve_remote_spawn(
                key,
                config_base,
                team_agent=active_team_agent,
            )
            registry_reservation = precheck.registry_reservation
            if precheck.error:
                return ToolOutput(success=False, error=precheck.error)
            validation_error = _validate_remote_spawn_inputs(inputs)
            if validation_error:
                await _release_registry_reservation(
                    registry_reservation,
                    member_name=key,
                    reason="invalid remote spawn inputs",
                )
                registry_reservation = None
                return ToolOutput(success=False, error=validation_error)

            result = await orig_invoke(inputs, **kwargs)
            ok = bool(getattr(result, "success", False))
            if not ok:
                logger.info(
                    "[RemoteMemberBootstrap] spawn_member result not success; skip remote bootstrap: inputs=%s",
                    inputs,
                )
                await _release_registry_reservation(
                    registry_reservation,
                    member_name=key,
                    reason="spawn_member failed",
                )
                registry_reservation = None
                return result

            await _ensure_remote_member_record(active_team_agent, key, inputs)
            # openjiuwen native spawn path may mark member as READY immediately.
            # For remote teammates, force it back to UNSTARTED and wait for ACK to set READY.
            try:
                from openjiuwen.agent_teams.schema.status import MemberStatus

                tb = getattr(active_team_agent, "team_backend", None)
                db = getattr(tb, "db", None) if tb is not None else None
                team_name = _team_name_for_agent(active_team_agent)
                if db is not None and isinstance(team_name, str) and team_name.strip():
                    await db.update_member_status(key, team_name, MemberStatus.UNSTARTED.value)
                    logger.info(
                        "[RemoteMemberBootstrap] spawn_member member=%s status forced to UNSTARTED "
                        "until remote ACK team=%s",
                        key,
                        team_name,
                    )
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] failed to force UNSTARTED before bootstrap member=%s: %s",
                    key,
                    exc,
                )

            try:
                delivered = await send_bootstrap_message(
                    active_team_agent,
                    active_session_id,
                    key,
                    (inputs or {}).get("prompt"),
                    registry_reservation=registry_reservation,
                )
                delivery_error = None
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] bootstrap delivery raised member=%s: %s",
                    key,
                    exc,
                )
                delivered = False
                delivery_error = exc

            if delivered:
                registry_reservation = None
                logger.info(
                    "[RemoteMemberBootstrap] bootstrap delivered member=%s team=%s; "
                    "status remains UNSTARTED until MESSAGE ACK sets ready",
                    key,
                    _team_name_for_agent(active_team_agent),
                )
                return result

            try:
                from openjiuwen.agent_teams.schema.status import MemberStatus

                tb = getattr(active_team_agent, "team_backend", None)
                db = getattr(tb, "db", None) if tb is not None else None
                team_name = _team_name_for_agent(active_team_agent)
                if db is not None and isinstance(team_name, str) and team_name.strip():
                    await db.update_member_status(key, team_name, MemberStatus.ERROR.value)
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] failed to mark remote member ERROR after bootstrap failure "
                    "member=%s: %s",
                    key,
                    exc,
                )
            await _release_registry_reservation(
                registry_reservation,
                member_name=key,
                reason="bootstrap not delivered",
            )
            registry_reservation = None
            detail = f": {delivery_error}" if delivery_error is not None else ""
            return ToolOutput(
                success=False,
                error=f"Remote bootstrap not delivered for member {key}; member marked error{detail}.",
            )
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] post-spawn hook failed: %s", exc)
            if key:
                try:
                    from openjiuwen.agent_teams.schema.status import MemberStatus

                    tb = getattr(active_team_agent, "team_backend", None)
                    db = getattr(tb, "db", None) if tb is not None else None
                    team_name = _team_name_for_agent(active_team_agent)
                    if db is not None and isinstance(team_name, str) and team_name.strip():
                        await db.update_member_status(key, team_name, MemberStatus.ERROR.value)
                except Exception as mark_exc:
                    logger.warning(
                        "[RemoteMemberBootstrap] failed to mark remote member ERROR after hook exception "
                        "member=%s: %s",
                        key,
                        mark_exc,
                    )
            await _release_registry_reservation(
                registry_reservation,
                member_name=key,
                reason="post-spawn hook exception",
            )
            return ToolOutput(
                success=False,
                error=f"Remote bootstrap failed for member {key or '<unknown>'}: {exc}",
            )

    tool.invoke = types.MethodType(wrapped_invoke, tool)
    setattr(tool, _WRAPPED_ATTR, True)
    logger.info(
        "[RemoteMemberBootstrap] attached spawn_member wrapper tool_id=%s remote=%s remote_all=%s",
        tool_id,
        sorted(remote_names),
        remote_all,
    )


async def _all_teammates_shutdown_requested_or_done(team_agent: Any) -> bool:
    tb = getattr(team_agent, "team_backend", None)
    list_members = getattr(tb, "list_members", None) if tb is not None else None
    if not callable(list_members):
        logger.debug("[RemoteMemberBootstrap] shutdown cleanup skipped: missing public list_members")
        return False

    from openjiuwen.agent_teams.schema.status import MemberStatus

    closed_statuses = {
        MemberStatus.SHUTDOWN_REQUESTED.value,
        MemberStatus.SHUTDOWN.value,
    }

    members = await list_members()
    if not members:
        return False

    for member in members:
        status = str(getattr(member, "status", "") or "").strip().lower()
        if status not in closed_statuses:
            logger.debug(
                "[RemoteMemberBootstrap] shutdown cleanup waiting for member=%s status=%s",
                getattr(member, "member_name", None),
                status,
            )
            return False
    return True


def _schedule_shutdown_cleanup(session_id: str, channel_id: str | None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    existing_task = _SHUTDOWN_CLEANUP_TASKS.get(sid)
    if existing_task is None or existing_task.done():
        _SHUTDOWN_CLEANUP_TASKS[sid] = asyncio.create_task(
            _delayed_shutdown_cleanup(sid, channel_id)
        )
    logger.info(
        "[RemoteMemberBootstrap] marked session for post-stream shutdown cleanup "
        "session_id=%s channel=%s",
        sid,
        channel_id,
    )


async def _delayed_shutdown_cleanup(session_id: str, channel_id: str | None) -> bool:
    try:
        # Give the leader response a short chance to flush before stopping the team stream.
        await asyncio.sleep(2.0)
        return await run_pending_shutdown_cleanup_for_session(
            session_id,
            channel_id=channel_id,
        )
    finally:
        current_task = asyncio.current_task()
        if _SHUTDOWN_CLEANUP_TASKS.get(session_id) is current_task:
            _SHUTDOWN_CLEANUP_TASKS.pop(session_id, None)


async def wait_for_pending_shutdown_cleanup_for_session(
    session_id: str,
    *,
    timeout: float = 15.0,
) -> bool:
    sid = str(session_id or "").strip()
    task = _SHUTDOWN_CLEANUP_TASKS.get(sid)
    if task is None:
        return False
    try:
        return bool(await asyncio.wait_for(asyncio.shield(task), timeout=timeout))
    except asyncio.TimeoutError:
        logger.warning(
            "[RemoteMemberBootstrap] timed out waiting for shutdown cleanup session_id=%s",
            sid,
        )
        return False


async def _push_shutdown_cleanup_notice(
    *,
    session_id: str,
    channel_id: str | None,
    deleted: bool,
) -> None:
    try:
        from jiuwenavatar.server.gateway_push import WebSocketGatewayPushTransport
        from jiuwenavatar.server.runtime.session.session_metadata import build_server_push_message

        request_id = f"team_shutdown_cleanup_{session_id}"
        content = (
            "团队已清空并解散，远端成员已恢复为空闲状态。"
            if deleted
            else "团队解散清理未完成，请查看后端日志确认原因。"
        )
        await WebSocketGatewayPushTransport().send_push(
            build_server_push_message(
                session_id=session_id,
                request_id=request_id,
                fallback_channel_id=channel_id,
                payload={
                    "event_type": "chat.final",
                    "request_id": request_id,
                    "session_id": session_id,
                    "content": content,
                },
            )
        )
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] shutdown cleanup notice push failed "
            "session_id=%s error=%s",
            session_id,
            exc,
        )


async def run_pending_shutdown_cleanup_for_session(
    session_id: str,
    channel_id: str | None = None,
) -> bool:
    """Delete a distributed team session after all teammates have been asked to shut down."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    active_channel_id = channel_id
    try:
        from jiuwenavatar.agents.harness.team import get_team_manager

        manager = get_team_manager(active_channel_id)
        deleted = await manager.delete_session_runtime(sid, reason="team.shutdown_all_members: ")
        logger.info(
            "[RemoteMemberBootstrap] post-stream shutdown cleanup finished "
            "session_id=%s deleted=%s",
            sid,
            deleted,
        )
        await _push_shutdown_cleanup_notice(
            session_id=sid,
            channel_id=active_channel_id,
            deleted=bool(deleted),
        )
        return bool(deleted)
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] post-stream shutdown cleanup failed session_id=%s error=%s",
            sid,
            exc,
        )
        await _push_shutdown_cleanup_notice(
            session_id=sid,
            channel_id=active_channel_id,
            deleted=False,
        )
        return False


def attach_shutdown_member_remote_cleanup_wrapper(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None,
) -> None:
    """Delete a distributed team session after every teammate has been shut down."""
    from jiuwenavatar.common.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_leader_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed leader runtime; skip shutdown cleanup wrapper")
        return

    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    if getattr(team_agent, "role", None) != TeamRole.LEADER:
        return
    leader = _team_agent_deep_agent(team_agent)
    if leader is None:
        logger.debug("[RemoteMemberBootstrap] skip shutdown cleanup wrapper: missing leader DeepAgent")
        return

    tool_id = _team_tool_id(leader, "shutdown_member")
    tag = getattr(getattr(leader, "card", None), "id", None)
    tool = Runner.resource_mgr.get_tool(tool_id, tag=tag) if tag else None
    if tool is None:
        tool = Runner.resource_mgr.get_tool(tool_id)
    if tool is None:
        logger.debug(
            "[RemoteMemberBootstrap] tool %s not in Runner.resource_mgr; skip shutdown cleanup wrapper "
            "session_id=%s channel=%s",
            tool_id,
            session_id,
            channel_id,
        )
        return

    setattr(tool, _SHUTDOWN_CLEANUP_SESSION_ID_ATTR, session_id)
    setattr(tool, _SHUTDOWN_CLEANUP_CHANNEL_ID_ATTR, channel_id)
    setattr(tool, _WRAPPED_TEAM_AGENT_ATTR, team_agent)
    if getattr(tool, _SHUTDOWN_CLEANUP_WRAPPED_ATTR, False):
        logger.info(
            "[RemoteMemberBootstrap] rebound shutdown cleanup wrapper tool_id=%s session_id=%s channel=%s",
            tool_id,
            session_id,
            channel_id,
        )
        return

    orig_invoke = tool.invoke

    async def wrapped_invoke(self: Any, inputs: dict[str, Any], **kwargs: Any) -> Any:
        result = await orig_invoke(inputs, **kwargs)
        try:
            if not bool(getattr(result, "success", False)):
                return result
            active_team_agent = getattr(self, _WRAPPED_TEAM_AGENT_ATTR, team_agent)
            if not await _all_teammates_shutdown_requested_or_done(active_team_agent):
                return result
            active_session_id = str(
                getattr(self, _SHUTDOWN_CLEANUP_SESSION_ID_ATTR, session_id) or session_id
            ).strip() or session_id
            active_channel_id = getattr(self, _SHUTDOWN_CLEANUP_CHANNEL_ID_ATTR, channel_id)
            logger.info(
                "[RemoteMemberBootstrap] all teammates are shutdown-requested; scheduling team cleanup "
                "session_id=%s channel=%s",
                active_session_id,
                active_channel_id,
            )
            _schedule_shutdown_cleanup(active_session_id, active_channel_id)
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] shutdown cleanup hook failed: %s", exc)
        return result

    tool.invoke = types.MethodType(wrapped_invoke, tool)
    setattr(tool, _SHUTDOWN_CLEANUP_WRAPPED_ATTR, True)
    logger.info(
        "[RemoteMemberBootstrap] attached shutdown cleanup wrapper tool_id=%s session_id=%s channel=%s",
        tool_id,
        session_id,
        channel_id,
    )


def attach_distributed_local_spawn_guard(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None,
) -> None:
    """Disable leader-side teammate startup when teammates are remote-managed.

    Some agent-core versions accept ``spawn_mode=distributed`` in config but still
    wire ``send_message`` auto-start to local ``spawn_teammate``. In distributed
    leader mode, jiuwenavatar owns remote bootstrap, so local teammate creation must
    be suppressed at the adapter layer.
    """
    from jiuwenavatar.common.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_leader_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed leader runtime; skip local spawn guard")
        return

    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    if getattr(team_agent, "role", None) != TeamRole.LEADER:
        return
    if getattr(team_agent, _LOCAL_SPAWN_GUARD_ATTR, False):
        return

    leader = _team_agent_deep_agent(team_agent)
    if leader is None:
        logger.debug("[RemoteMemberBootstrap] skip local spawn guard: missing leader DeepAgent")
        return

    tool_id = _team_tool_id(leader, "send_message")
    tag = getattr(getattr(leader, "card", None), "id", None)
    tool = Runner.resource_mgr.get_tool(tool_id, tag=tag) if tag else None
    if tool is None:
        tool = Runner.resource_mgr.get_tool(tool_id)
    if tool is None:
        logger.warning(
            "[RemoteMemberBootstrap] distributed local spawn guard could not find send_message tool "
            "tool_id=%s session_id=%s channel=%s",
            tool_id,
            session_id,
            channel_id,
        )
    elif not getattr(tool, _SEND_MESSAGE_GUARDED_ATTR, False):
        if hasattr(tool, "_on_teammate_created"):
            setattr(tool, "_on_teammate_created", None)
            setattr(tool, _SEND_MESSAGE_GUARDED_ATTR, True)
            logger.info(
                "[RemoteMemberBootstrap] distributed local spawn guard disabled send_message auto-start "
                "tool_id=%s session_id=%s channel=%s",
                tool_id,
                session_id,
                channel_id,
            )
        else:
            logger.warning(
                "[RemoteMemberBootstrap] send_message tool has no _on_teammate_created field "
                "tool_id=%s type=%s",
                tool_id,
                type(tool).__name__,
            )

    original_spawn_teammate = getattr(team_agent, "spawn_teammate", None)
    if callable(original_spawn_teammate):

        async def _skip_local_spawn_teammate(self: Any, ctx: Any, *args: Any, **kwargs: Any) -> None:
            member_name = getattr(ctx, "member_name", None)
            logger.info(
                "[RemoteMemberBootstrap] distributed local spawn guard skipped local spawn_teammate "
                "member=%s session_id=%s channel=%s",
                member_name,
                session_id,
                channel_id,
            )
            return None

        setattr(team_agent, "_jiuwen_original_spawn_teammate", original_spawn_teammate)
        team_agent.spawn_teammate = types.MethodType(_skip_local_spawn_teammate, team_agent)
    else:
        logger.warning("[RemoteMemberBootstrap] team_agent has no callable spawn_teammate to guard")

    setattr(team_agent, _LOCAL_SPAWN_GUARD_ATTR, True)
    logger.info(
        "[RemoteMemberBootstrap] distributed local spawn guard attached session_id=%s channel=%s",
        session_id,
        channel_id,
    )


def attach_remote_bootstrap_ack_listener(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None = None,
) -> None:
    """Leader: on MESSAGE transport events, detect ACK JSON and set member UNSTARTED→READY in DB.

    The published :class:`MessageEvent` has no body; we load content via ``db.get_message``,
    then ``mark_message_read`` so the leader LLM is not fed the control payload.
    """
    from jiuwenavatar.common.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_leader_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed leader runtime; skip ACK listener")
        return

    from openjiuwen.agent_teams.schema.events import TeamEvent
    from openjiuwen.agent_teams.schema.status import MemberStatus
    from openjiuwen.agent_teams.schema.team import TeamRole

    _ = session_id
    _ = channel_id

    if getattr(team_agent, "role", None) != TeamRole.LEADER:
        return
    if getattr(team_agent, _ACK_LISTENER_ATTR, False):
        return
    tb = getattr(team_agent, "team_backend", None)
    mm = getattr(team_agent, "message_manager", None)
    if tb is None or mm is None or getattr(tb, "db", None) is None:
        logger.debug(
            "[RemoteMemberBootstrap] skip ACK listener: missing team_backend.db or message_manager",
        )
        return
    remote_names = remote_member_names(config_base)
    remote_all = remote_all_spawn_members(config_base)
    if not remote_names and not remote_all:
        logger.debug("[RemoteMemberBootstrap] no jiuwen_remote_member_names; skip ACK listener")
        return

    processed_message_ids: set[str] = set()

    async def on_event(event: Any) -> None:
        if getattr(event, "event_type", None) != TeamEvent.MESSAGE:
            return
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            return
        to_name = payload.get("to_member_name")
        from_name = payload.get("from_member_name")
        message_id = payload.get("message_id")
        _mn = getattr(team_agent, "_member_name", None)
        leader_name = _mn() if callable(_mn) else None
        if not leader_name or to_name != leader_name:
            return
        if not isinstance(from_name, str):
            return
        sender = from_name.strip()
        if not sender:
            return
        if (not remote_all) and sender not in remote_names:
            return
        if not isinstance(message_id, str) or not message_id:
            return

        row = await tb.db.get_message(message_id)
        if row is None:
            logger.debug("[RemoteMemberBootstrap] ACK: no row for message_id=%s", message_id)
            return
        if getattr(row, "from_member_name", None) != from_name or getattr(row, "to_member_name", None) != leader_name:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: DB row sender/recipient mismatch id=%s",
                message_id,
            )
            return

        ack = parse_remote_bootstrap_ack_json(getattr(row, "content", "") or "")
        if ack is None:
            return
        ack_member = str(ack.get("member_name", "")).strip()
        if ack_member != sender:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: member_name != sender for id=%s",
                message_id,
            )
            return
        _tn = getattr(team_agent, "_team_name", None)
        team_name = _tn() if callable(_tn) else None
        if not team_name:
            logger.warning("[RemoteMemberBootstrap] ACK: leader has no team_name")
            return
        ack_team = ack.get("team_name")
        if ack_team and str(ack_team) != str(team_name):
            logger.warning(
                "[RemoteMemberBootstrap] ACK: team_name mismatch db=%s ack=%s",
                team_name,
                ack_team,
            )
            return
        ack_applied = ack.get("handshake_applied")
        if isinstance(ack_applied, bool) and not ack_applied:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: teammate reports bootstrap not fully applied member=%s id=%s",
                ack_member,
                message_id,
            )
            return
        existing_member = await _existing_team_member(team_agent, ack_member)
        if existing_member is None:
            try:
                await mm.mark_message_read(message_id, leader_name)
            except Exception as exc:
                logger.warning("[RemoteMemberBootstrap] ACK: mark_message_read failed: %s", exc)
            logger.warning(
                "[RemoteMemberBootstrap] ACK: no roster row for member=%s team=%s; skip READY update",
                ack_member,
                team_name,
            )
            return

        ok = await tb.db.update_member_status(ack_member, team_name, MemberStatus.READY.value)
        if not ok:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: update_member_status failed member=%s team=%s",
                ack_member,
                team_name,
            )
            return
        try:
            await mm.mark_message_read(message_id, leader_name)
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] ACK: mark_message_read failed: %s", exc)
        logger.info(
            "[RemoteMemberBootstrap] ACK applied member=%s team=%s message_id=%s -> status=ready "
            "(ready is set by ACK listener, not directly by spawn_member)",
            ack_member,
            team_name,
            message_id,
        )

    team_agent.add_event_listener(on_event)
    setattr(team_agent, _ACK_LISTENER_ATTR, True)
    logger.info(
        "[RemoteMemberBootstrap] attached remote bootstrap ACK listener remote=%s remote_all=%s",
        sorted(remote_names),
        remote_all,
    )


def _set_obj_member_name(obj: Any, member_name: str) -> None:
    """Best-effort mutation helper for member-name fields on runtime objects."""
    for attr in ("member_name", "_member_name"):
        if hasattr(obj, attr):
            try:
                setattr(obj, attr, member_name)
            except Exception as exc:
                logger.debug(
                    "[RemoteMemberBootstrap] failed to set %s on %s: %s",
                    attr,
                    type(obj).__name__,
                    exc,
                )


def _adopt_teammate_member_name(team_agent: Any, member_name: str) -> None:
    """Switch teammate runtime identity to the spawned remote member name."""
    target = member_name.strip()
    if not target:
        return
    ctx = getattr(team_agent, "_ctx", None)
    if ctx is not None and hasattr(ctx, "member_name"):
        try:
            ctx.member_name = target
        except Exception as exc:
            logger.debug("[RemoteMemberBootstrap] failed to update runtime member_name: %s", exc)
        mc = getattr(ctx, "messager_config", None)
        if mc is not None and hasattr(mc, "node_id"):
            try:
                mc.node_id = target
            except Exception as exc:
                logger.debug("[RemoteMemberBootstrap] failed to update messager node_id: %s", exc)
    for obj_name in ("_team_backend", "_team_member"):
        obj = getattr(team_agent, obj_name, None)
        if obj is not None:
            _set_obj_member_name(obj, target)


def _is_distributed_teammate_runtime(config_base: dict[str, Any]) -> bool:
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    mode = str(runtime.get("mode", "")).strip().lower()
    role = str(runtime.get("role", "")).strip().lower()
    return mode == "distributed" and role == "teammate"


def _messager_direct_addr(messager: Any) -> str:
    config = getattr(messager, "_config", None)
    return str(getattr(config, "direct_addr", "") or "")


async def _stop_team_agent_runtime(
    agent: Any,
    *,
    session_id: str,
    member_name: str,
    source: str,
) -> bool:
    """Stop a TeamAgent-like runtime without deleting team database state."""
    stopped = False
    messager = getattr(agent, "_messager", None) or getattr(agent, "mailbox_transport", None)
    direct_addr = _messager_direct_addr(messager)
    stop_coordination = getattr(agent, "_stop_coordination", None)
    if callable(stop_coordination):
        with contextlib.suppress(Exception):
            await stop_coordination()
            stopped = True
    stop_messager = getattr(messager, "stop", None)
    if callable(stop_messager):
        with contextlib.suppress(Exception):
            await stop_messager()
            stopped = True
    logger.info(
        "[RemoteMemberBootstrap] team agent runtime stopped source=%s session_id=%s member=%s "
        "agent_id=%s messager_id=%s direct_addr=%s stopped=%s",
        source,
        session_id,
        member_name,
        id(agent),
        id(messager) if messager is not None else None,
        direct_addr,
        stopped,
    )
    return stopped


async def _discard_auxiliary_team_agent(
    team_manager: Any,
    session_id: str,
    team_agent: Any,
) -> None:
    """Remove the bootstrap helper TeamAgent from TeamManager without cleaning DB rows."""
    agents = getattr(team_manager, "_team_agents", None)
    removed = False
    if isinstance(agents, dict) and agents.get(session_id) is team_agent:
        agents.pop(session_id, None)
        removed = True
    await _stop_team_agent_runtime(
        team_agent,
        session_id=session_id,
        member_name=str(getattr(team_agent, "member_name", None) or "bootstrap-helper"),
        source="bootstrap-helper",
    )
    logger.info(
        "[RemoteMemberBootstrap] discarded auxiliary team agent session_id=%s removed_from_manager=%s",
        session_id,
        removed,
    )


def _cleanup_auxiliary_leader_workspace(team_name: str, leader_member_name: str) -> None:
    """Remove the helper leader member workspace created in the teammate process."""
    team = str(team_name or "").strip()
    leader = str(leader_member_name or "").strip()
    if not team or not leader:
        return
    try:
        from openjiuwen.agent_teams.paths import team_home

        helper_workspace = team_home(team) / "workspaces" / f"{leader}_workspace"
        if not helper_workspace.exists():
            return
        shutil.rmtree(helper_workspace)
        logger.info(
            "[RemoteMemberBootstrap] removed auxiliary leader workspace: %s",
            helper_workspace,
        )
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] auxiliary leader workspace cleanup failed "
            "team=%s leader=%s error=%s",
            team,
            leader,
            exc,
        )


def _allocate_loopback_direct_addr() -> str:
    """Reserve a currently free loopback TCP port for a dynamic member ROUTER."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
    return f"tcp://127.0.0.1:{port}"


def _retarget_teammate_direct_addr(ctx: Any, *, session_id: str, member_name: str) -> Any:
    """Avoid agent-core's default inprocess member direct_addr (usually 16000)."""
    cfg = getattr(ctx, "messager_config", None)
    if cfg is None:
        return ctx
    old_addr = str(getattr(cfg, "direct_addr", "") or "")
    new_addr = _allocate_loopback_direct_addr()
    new_cfg = cfg.model_copy(update={"direct_addr": new_addr})
    new_ctx = ctx.model_copy(update={"messager_config": new_cfg})
    logger.info(
        "[RemoteMemberBootstrap] retargeted teammate direct_addr session_id=%s member=%s old=%s new=%s",
        session_id,
        member_name,
        old_addr,
        new_addr,
    )
    return new_ctx


async def _stop_dynamic_member_agent(session_id: str, member_name: str) -> bool:
    """Stop and forget a dynamically created remote teammate member runtime."""
    sid = str(session_id or "").strip()
    member = str(member_name or "").strip()
    if not sid or not member:
        return False
    agent = _DYNAMIC_MEMBER_AGENTS.pop((sid, member), None)
    if agent is None:
        return False
    stopped = await _stop_team_agent_runtime(
        agent,
        session_id=sid,
        member_name=member,
        source="dynamic-member",
    )
    logger.info(
        "[RemoteMemberBootstrap] dynamic member runtime stopped session_id=%s member=%s stopped=%s",
        sid,
        member,
        stopped,
    )
    return True


async def _stop_dynamic_member_agents_for_session(session_id: str, member_name: str | None = None) -> int:
    """Stop dynamic member runtimes for a session, optionally narrowed to one member."""
    sid = str(session_id or "").strip()
    member = str(member_name or "").strip() if member_name else ""
    if not sid:
        return 0
    keys = [
        key
        for key in list(_DYNAMIC_MEMBER_AGENTS)
        if key[0] == sid and (not member or key[1] == member)
    ]
    stopped_count = 0
    for key_sid, key_member in keys:
        if await _stop_dynamic_member_agent(key_sid, key_member):
            stopped_count += 1
    return stopped_count


async def _ensure_dynamic_member_execution_loop(
    *,
    session_id: str,
    target_member: str,
    channel_id: str = "default",
    leader_agent_id: str = "",
    leader_direct_addr: str = "",
) -> tuple[bool, bool]:
    """Best-effort bootstrap for teammate runtime loop after dynamic member takeover."""
    sid = str(session_id or "").strip()
    member = str(target_member or "").strip()
    if not sid or not member:
        return False, False
    try:
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.context import reset_session_id, set_session_id

        from jiuwenavatar.server.agent_ws_server import AgentWebSocketServer
        from jiuwenavatar.agents.harness.team.team_manager import get_team_manager

        server = AgentWebSocketServer.get_instance()
        agent_manager = server.get_agent_manager()
        agent = agent_manager.get_agent_nowait(channel_id) or await agent_manager.get_agent(channel_id, "agent")
        if agent is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: agent unavailable channel=%s session_id=%s",
                channel_id,
                sid,
            )
            return False, False
        deep_agent = agent.get_instance()
        if deep_agent is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: deep_agent unavailable session_id=%s",
                sid,
            )
            return False, False

        team_manager = get_team_manager(channel_id)
        leader_team_agent = await team_manager.get_or_create_team(
            sid,
            deep_agent,
            channel_id=channel_id,
        )
        helper_token = set_session_id(sid)
        try:
            helper_backend = getattr(leader_team_agent, "team_backend", None)
            helper_db = getattr(helper_backend, "db", None)
            initialize_db = getattr(helper_db, "initialize", None)
            if callable(initialize_db):
                await initialize_db()
            # Build a real TEAMMATE runtime context for the adopted member, instead
            # of using TeamManager.interact() (which drives the leader context).
            spawn_manager = getattr(leader_team_agent, "spawn_manager", None)
            build_ctx = getattr(spawn_manager, "build_context_from_db", None)
            if not callable(build_ctx):
                build_ctx = getattr(leader_team_agent, "_build_context_from_db", None)
            if not callable(build_ctx):
                logger.warning(
                    "[RemoteMemberBootstrap] teammate loop start skipped: build_context_from_db unavailable "
                    "session_id=%s member=%s",
                    sid,
                    member,
                )
                return False, False
            teammate_ctx = await build_ctx(member)
        finally:
            reset_session_id(helper_token)
        if teammate_ctx is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: teammate context missing "
                "session_id=%s member=%s",
                sid,
                member,
            )
            return False, False
        if getattr(leader_team_agent, "spec", None) is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: team spec unavailable session_id=%s",
                sid,
            )
            return False, False
        teammate_ctx = _retarget_teammate_direct_addr(teammate_ctx, session_id=sid, member_name=member)
        spec_obj = leader_team_agent.spec
        leader_member_name = str(getattr(spec_obj, "leader_member_name", "") or "").strip()
        if not leader_member_name:
            leader_spec = getattr(spec_obj, "leader", None)
            leader_member_name = str(getattr(leader_spec, "member_name", "") or "").strip()
        payload = {
            "spec": spec_obj.model_dump(mode="json"),
            "context": teammate_ctx.model_dump(mode="json"),
        }
        await _discard_auxiliary_team_agent(team_manager, sid, leader_team_agent)
        _cleanup_auxiliary_leader_workspace(
            str(getattr(spec_obj, "team_name", "") or ""),
            leader_member_name,
        )
        await _stop_dynamic_member_agent(sid, member)
        teammate_agent = await TeamAgent.from_spawn_payload(payload)
        _DYNAMIC_MEMBER_AGENTS[(sid, member)] = teammate_agent
        route_applied = False
        if leader_agent_id and leader_direct_addr:
            route_applied = _apply_leader_route_from_envelope(
                teammate_agent,
                {
                    "leader_agent_id": leader_agent_id,
                    "leader_direct_addr": leader_direct_addr,
                },
            )
        kickoff = (
            f"[remote bootstrap] teammate adopted member={member}. "
            "Start/continue execution loop for assigned team tasks."
        )
        token = set_session_id(sid)
        try:
            await teammate_agent.invoke({"query": kickoff}, session=None)
        finally:
            reset_session_id(token)
            if (sid, member) in _DYNAMIC_MEMBER_AGENTS:
                await _stop_dynamic_member_agent(sid, member)
        logger.info(
            "[RemoteMemberBootstrap] teammate execution loop kicked: session_id=%s member=%s channel_id=%s",
            sid,
            member,
            channel_id,
        )
        return True, route_applied
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] teammate execution loop kickoff failed: session_id=%s member=%s error=%s",
            sid,
            member,
            exc,
            exc_info=True,
        )
        return False, False


async def _replace_teammate_card_after_direct_bootstrap(
    *,
    channel_id: str,
    member_name: str,
) -> bool:
    """Replace this teammate's A2X card after direct control-plane bootstrap."""
    member = str(member_name or "").strip()
    if not member:
        return False
    try:
        from jiuwenavatar.server.agent_ws_server import AgentWebSocketServer
        from jiuwenavatar.agents.harness.team.a2x.a2x_registry_runtime import replace_teammate_agent_card_after_bootstrap

        server = AgentWebSocketServer.get_instance()
        agent_manager = server.get_agent_manager()
        agent = agent_manager.get_agent_nowait(channel_id) or await agent_manager.get_agent(channel_id, "agent")
        deep_agent = agent.get_instance() if agent is not None else None
        if deep_agent is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate registry card replace skipped: deep_agent unavailable "
                "channel=%s member=%s",
                channel_id,
                member,
            )
            return False
        client = getattr(deep_agent, "_jiuwen_a2x_client", None)
        dataset = str(getattr(deep_agent, "_jiuwen_a2x_blank_dataset", "") or "").strip()
        service_id = str(getattr(deep_agent, "_jiuwen_a2x_blank_service_id", "") or "").strip()
        if client is None or not dataset or not service_id:
            logger.warning(
                "[RemoteMemberBootstrap] teammate registry card replace skipped: missing local A2X state "
                "channel=%s member=%s has_client=%s dataset=%s service_id=%s",
                channel_id,
                member,
                client is not None,
                dataset,
                service_id,
            )
            return False
        replaced = await replace_teammate_agent_card_after_bootstrap(
            client,
            dataset=dataset,
            service_id=service_id,
            member_name=member,
            source="teammate-direct-bootstrap",
        )
        logger.info(
            "[RemoteMemberBootstrap] teammate registry card replace after direct bootstrap "
            "channel=%s member=%s dataset=%s service_id=%s replaced=%s",
            channel_id,
            member,
            dataset,
            service_id,
            replaced,
        )
        return bool(replaced)
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] teammate registry card replace after direct bootstrap failed "
            "channel=%s member=%s: %s",
            channel_id,
            member,
            exc,
            exc_info=True,
        )
        return False


async def _apply_bootstrap_envelope_from_control_plane(
    *,
    processed_ids: set[str],
    loop_kicked_members: set[tuple[str, str]],
    kickoff_tasks: set[asyncio.Task[Any]],
    adopted_member: str,
    envelope: dict[str, Any],
    source_id: str,
) -> str:
    """Process one bootstrap envelope (from DB poll or direct control-plane)."""
    if not isinstance(envelope, dict):
        return adopted_member
    bootstrap_id = str(envelope.get("bootstrap_id", "")).strip() or source_id
    if bootstrap_id in processed_ids:
        return adopted_member
    envelope_team_name = str(envelope.get("team_name", "")).strip()
    envelope_session_id = str(envelope.get("session_id", "")).strip()
    if not envelope_team_name or not envelope_session_id:
        return adopted_member
    target_member = str(envelope.get("member_name", "")).strip()
    if not target_member:
        return adopted_member
    leader_agent_id = str(envelope.get("leader_agent_id", "")).strip()
    leader_direct_addr = str(envelope.get("leader_direct_addr", "")).strip()

    effective_sid = envelope_session_id
    loop_key = (effective_sid, target_member)

    logger.info(
        "[RemoteMemberBootstrap] teammate applied bootstrap from control-plane "
        "(ready to send direct ACK to leader transport) team=%s session_id=%s "
        "old_member=%s adopted_member=%s source_id=%s",
        envelope_team_name,
        envelope_session_id,
        adopted_member,
        target_member,
        source_id,
    )
    card_replaced = await _replace_teammate_card_after_direct_bootstrap(
        channel_id="default",
        member_name=target_member,
    )
    if effective_sid and loop_key not in loop_kicked_members:
        loop_kicked_members.add(loop_key)

        async def _kickoff_loop() -> None:
            kicked, route_applied = await _ensure_dynamic_member_execution_loop(
                session_id=effective_sid,
                target_member=target_member,
                channel_id="default",
                leader_agent_id=leader_agent_id,
                leader_direct_addr=leader_direct_addr,
            )
            if kicked:
                logger.info(
                    "[RemoteMemberBootstrap] teammate execution kickoff scheduled from bootstrap "
                    "team=%s session_id=%s member=%s handshake_applied=%s card_replaced=%s",
                    envelope_team_name,
                    effective_sid,
                    target_member,
                    route_applied,
                    card_replaced,
                )
            else:
                logger.warning(
                    "[RemoteMemberBootstrap] teammate execution kickoff failed after direct ACK "
                    "team=%s session_id=%s member=%s card_replaced=%s",
                    envelope_team_name,
                    effective_sid,
                    target_member,
                    card_replaced,
                )

        kickoff_task = asyncio.create_task(
            _kickoff_loop(),
            name=f"remote-bootstrap-kickoff:{effective_sid}:{target_member}",
        )
        kickoff_tasks.add(kickoff_task)

        def _on_kickoff_done(task: asyncio.Task[Any]) -> None:
            kickoff_tasks.discard(task)
            loop_kicked_members.discard(loop_key)
            if task.cancelled():
                return
            try:
                task.result()
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] teammate execution kickoff task crashed "
                    "team=%s session_id=%s member=%s error=%s",
                    envelope_team_name,
                    effective_sid,
                    target_member,
                    exc,
                )

        kickoff_task.add_done_callback(_on_kickoff_done)
    processed_ids.add(bootstrap_id)
    return target_member


async def _apply_team_destroy_envelope_from_control_plane(
    *,
    loop_kicked_members: set[tuple[str, str]],
    kickoff_tasks: set[asyncio.Task[Any]],
    adopted_member: str,
    local_member: str,
    envelope: dict[str, Any],
    source_id: str,
) -> str:
    """Handle leader teardown notification on a remote teammate process."""
    if not isinstance(envelope, dict):
        return adopted_member
    envelope_team_name = str(envelope.get("team_name", "")).strip()
    envelope_session_id = str(envelope.get("session_id", "")).strip()
    target_member = str(envelope.get("member_name", "")).strip()
    if not envelope_team_name or not envelope_session_id or not target_member:
        return adopted_member

    for loop_key in list(loop_kicked_members):
        sid, member = loop_key
        if sid == envelope_session_id and member == target_member:
            loop_kicked_members.discard(loop_key)

    task_prefix = f"remote-bootstrap-kickoff:{envelope_session_id}:"
    for task in list(kickoff_tasks):
        task_name = task.get_name()
        if not task_name.startswith(task_prefix):
            continue
        if not task_name.endswith(f":{target_member}"):
            continue
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    stopped_dynamic = await _stop_dynamic_member_agents_for_session(envelope_session_id, target_member)

    try:
        from jiuwenavatar.agents.harness.team.team_manager import get_team_manager

        cleaned = await get_team_manager("default").destroy_team(envelope_session_id)
        logger.info(
            "[RemoteMemberBootstrap] teammate applied team destroy notification "
            "team=%s session_id=%s member=%s source_id=%s cleaned=%s dynamic_runtimes_stopped=%s",
            envelope_team_name,
            envelope_session_id,
            target_member,
            source_id,
            cleaned,
            stopped_dynamic,
        )
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] teammate team destroy cleanup failed "
            "team=%s session_id=%s member=%s source_id=%s error=%s",
            envelope_team_name,
            envelope_session_id,
            target_member,
            source_id,
            exc,
        )

    registry = envelope.get("registry") if isinstance(envelope.get("registry"), dict) else {}
    try:
        from jiuwenavatar.common.config import get_config as _get_config
        from jiuwenavatar.agents.harness.team.a2x.a2x_registry_runtime import (
            restore_teammate_blank_agent_on_destroy,
        )

        restored = await restore_teammate_blank_agent_on_destroy(
            _get_config(),
            dataset=str(registry.get("dataset", "")).strip() or None,
            service_id=str(registry.get("service_id", "")).strip() or None,
            endpoint=str(registry.get("endpoint", "")).strip() or None,
            source="teammate-team-destroy",
        )
        logger.info(
            "[RemoteMemberBootstrap] teammate registry blank restore after team destroy "
            "team=%s session_id=%s member=%s restored=%s",
            envelope_team_name,
            envelope_session_id,
            target_member,
            restored,
        )
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] teammate registry blank restore failed "
            "team=%s session_id=%s member=%s: %s",
            envelope_team_name,
            envelope_session_id,
            target_member,
            exc,
        )

    if adopted_member == target_member:
        return local_member
    return adopted_member


async def run_teammate_bootstrap_daemon(*, stop_event: asyncio.Event, poll_interval: float = 1.0) -> None:
    """Startup daemon for distributed teammate: consume bootstrap even before team runtime exists."""
    from jiuwenavatar.common.config import get_config as _get_config

    config = _get_config()
    if not _is_distributed_teammate_runtime(config):
        return
    team_cfg = config.get("team") if isinstance(config.get("team"), dict) else {}
    transport_cfg = team_cfg.get("transport") if isinstance(team_cfg.get("transport"), dict) else {}
    transport_params = transport_cfg.get("params") if isinstance(transport_cfg.get("params"), dict) else {}

    runtime = team_cfg.get("runtime") if isinstance(team_cfg.get("runtime"), dict) else {}
    local_member = str(runtime.get("member_name", "teammate_1")).strip() or "teammate_1"
    adopted_member = local_member
    processed: set[str] = set()
    loop_kicked_members: set[tuple[str, str]] = set()
    kickoff_tasks: set[asyncio.Task[Any]] = set()
    bootstrap_router = None
    zmq_mod = None
    direct_bootstrap_addr = _normalize_leader_direct_addr(
        transport_params.get(_TRANSPORT_BOOTSTRAP_DIRECT_ADDR_KEY)
    )

    if direct_bootstrap_addr:
        try:
            import zmq
            import zmq.asyncio

            zmq_mod = zmq
            ctx = zmq.asyncio.Context.instance()
            bootstrap_router = ctx.socket(zmq.ROUTER)
            bootstrap_router.bind(direct_bootstrap_addr)
            logger.info(
                "[RemoteMemberBootstrap] teammate direct bootstrap listener started addr=%s local_member=%s",
                direct_bootstrap_addr,
                local_member,
            )
            try:
                from jiuwenavatar.agents.harness.team.a2x.a2x_registry_runtime import (
                    register_teammate_blank_agent_at_startup,
                )

                await register_teammate_blank_agent_at_startup(
                    config,
                    source="teammate-bootstrap-daemon",
                )
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] teammate startup A2X registration failed: %s",
                    exc,
                )
        except Exception as exc:
            bootstrap_router = None
            logger.warning(
                "[RemoteMemberBootstrap] teammate direct bootstrap listener disabled addr=%s error=%s",
                direct_bootstrap_addr,
                exc,
            )

    logger.info(
        "[RemoteMemberBootstrap] teammate bootstrap daemon started local_member=%s",
        local_member,
    )
    while not stop_event.is_set():
        try:
            if bootstrap_router is not None and zmq_mod is not None:
                for _ in range(64):
                    try:
                        frames = await bootstrap_router.recv_multipart(flags=zmq_mod.NOBLOCK)
                    except zmq_mod.Again:
                        break
                    except Exception as exc:
                        logger.warning("[RemoteMemberBootstrap] direct bootstrap recv failed: %s", exc)
                        break
                    if len(frames) < 2:
                        continue
                    identity, payload = frames[0], frames[-1]
                    try:
                        raw = json.loads(payload.decode("utf-8"))
                    except Exception:
                        await bootstrap_router.send_multipart([identity, b"ok"])
                        continue
                    event_type = str(raw.get("event_type", "")).strip()
                    env = None
                    if event_type == REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE:
                        payload_obj = raw.get("payload")
                        if isinstance(payload_obj, dict):
                            env = payload_obj.get("envelope")
                    elif event_type == REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE:
                        payload_obj = raw.get("payload")
                        if isinstance(payload_obj, dict):
                            env = payload_obj.get("envelope")
                    if isinstance(env, dict) and event_type == REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE:
                        source_id = str(env.get("bootstrap_id", "")).strip() or str(uuid.uuid4())
                        adopted_member = await _apply_bootstrap_envelope_from_control_plane(
                            processed_ids=processed,
                            loop_kicked_members=loop_kicked_members,
                            kickoff_tasks=kickoff_tasks,
                            adopted_member=adopted_member,
                            envelope=env,
                            source_id=source_id,
                        )
                    elif isinstance(env, dict) and event_type == REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE:
                        source_id = str(env.get("destroy_id", "")).strip() or str(uuid.uuid4())
                        adopted_member = await _apply_team_destroy_envelope_from_control_plane(
                            loop_kicked_members=loop_kicked_members,
                            kickoff_tasks=kickoff_tasks,
                            adopted_member=adopted_member,
                            local_member=local_member,
                            envelope=env,
                            source_id=source_id,
                        )
                    await bootstrap_router.send_multipart([identity, b"ok"])

        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] teammate bootstrap daemon loop error: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(0.2, poll_interval))
        except asyncio.TimeoutError:
            pass

    if bootstrap_router is not None:
        try:
            bootstrap_router.close(linger=0)
        except Exception as exc:
            logger.debug("[RemoteMemberBootstrap] bootstrap router close failed: %s", exc)
    for task in list(kickoff_tasks):
        task.cancel()
    for task in list(kickoff_tasks):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    logger.info("[RemoteMemberBootstrap] teammate bootstrap daemon stopped")


def attach_remote_teammate_bootstrap_listener(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None = None,
) -> None:
    """Teammate: consume remote bootstrap message, adopt member identity, send ACK."""
    from jiuwenavatar.common.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_teammate_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed teammate runtime; skip teammate listener")
        return

    from openjiuwen.agent_teams.schema.events import TeamEvent
    from openjiuwen.agent_teams.schema.team import TeamRole

    _ = session_id
    _ = channel_id

    if getattr(team_agent, "role", None) != TeamRole.TEAMMATE:
        return
    if getattr(team_agent, _TEAMMATE_BOOTSTRAP_LISTENER_ATTR, False):
        return
    tb = getattr(team_agent, "team_backend", None)
    mm = getattr(team_agent, "message_manager", None)
    if tb is None or mm is None or getattr(tb, "db", None) is None:
        logger.debug(
            "[RemoteMemberBootstrap] skip teammate bootstrap listener: missing team_backend.db or message_manager",
        )
        return

    processed_message_ids: set[str] = set()

    async def on_event(event: Any) -> None:
        if getattr(event, "event_type", None) != TeamEvent.MESSAGE:
            return
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            return
        message_id = payload.get("message_id")
        from_name = payload.get("from_member_name")
        to_name = payload.get("to_member_name")
        if not isinstance(message_id, str) or not message_id:
            return
        if not isinstance(from_name, str) or not from_name.strip():
            return
        if not isinstance(to_name, str) or not to_name.strip():
            return

        if message_id in processed_message_ids:
            return

        row = await tb.db.get_message(message_id)
        if row is None:
            return
        envelope = parse_remote_teammate_bootstrap_json(getattr(row, "content", "") or "")
        if envelope is None:
            return

        target_member = str(envelope.get("member_name", "")).strip()
        leader_member = str(envelope.get("leader_member_name", "")).strip() or from_name.strip()
        if not target_member:
            return
        if to_name.strip() != target_member:
            return

        old_member = getattr(team_agent, "_member_name", None)
        old_member_name = old_member() if callable(old_member) else None
        _adopt_teammate_member_name(team_agent, target_member)
        route_applied = _apply_leader_route_from_envelope(team_agent, envelope)

        deep_agent = _team_agent_deep_agent(team_agent)
        client = getattr(deep_agent, "_jiuwen_a2x_client", None) if deep_agent is not None else None
        dataset = str(getattr(deep_agent, "_jiuwen_a2x_blank_dataset", "") or "").strip()
        service_id = str(getattr(deep_agent, "_jiuwen_a2x_blank_service_id", "") or "").strip()
        card_replaced = False
        if client is None:
            raise RuntimeError(
                "[RemoteMemberBootstrap] teammate bootstrap missing A2X client "
                f"member={target_member}"
            )
        from jiuwenavatar.agents.harness.team.a2x.a2x_registry_runtime import replace_teammate_agent_card_after_bootstrap

        card_replaced = await replace_teammate_agent_card_after_bootstrap(
            client,
            dataset=dataset,
            service_id=service_id,
            member_name=target_member,
            source="teammate-bootstrap",
        )

        try:
            if old_member_name:
                await mm.mark_message_read(message_id, old_member_name)
            await mm.mark_message_read(message_id, target_member)
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] teammate mark_message_read failed: %s", exc)

        processed_message_ids.add(message_id)
        _tn = getattr(team_agent, "_team_name", None)
        team_name = _tn() if callable(_tn) else None
        ack = build_bootstrap_ack_envelope(
            member_name=target_member,
            team_name=team_name,
            leader_agent_id=str(envelope.get("leader_agent_id", "")).strip(),
            leader_direct_addr=str(envelope.get("leader_direct_addr", "")).strip(),
            handshake_applied=bool(route_applied and card_replaced),
        )
        ack_id = await mm.send_message(
            content=json.dumps(ack, ensure_ascii=False),
            to_member_name=leader_member,
        )
        logger.info(
            "[RemoteMemberBootstrap] teammate bootstrap consumed message_id=%s old_member=%s adopted_member=%s "
            "leader=%s ack_message_id=%s card_replaced=%s route_applied=%s dataset=%s service_id=%s",
            message_id,
            old_member_name,
            target_member,
            leader_member,
            ack_id,
            card_replaced,
            route_applied,
            dataset,
            service_id,
        )

    team_agent.add_event_listener(on_event)
    setattr(team_agent, _TEAMMATE_BOOTSTRAP_LISTENER_ATTR, True)
    logger.info("[RemoteMemberBootstrap] attached teammate bootstrap listener")
