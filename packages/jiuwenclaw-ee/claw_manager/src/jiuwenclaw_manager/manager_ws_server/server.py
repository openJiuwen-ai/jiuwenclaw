"""Claw Manager manager_ws_server：配置下发 WebSocket 服务端。"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from openjiuwen_runtime.foundation.security.link_auth import NonceCache, InMemoryPinStore, build_token
from sqlalchemy.exc import SQLAlchemyError

from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.infrastructure.logger import get_logger

# 密钥落库的预期失败类型：DB 未就绪/连接/语句错误、表未初始化、格式问题。
# 收敛后真正的代码 bug（如 TypeError）不再被吞掉，会照常抛出。
_KEY_STORE_ERRORS = (RuntimeError, OSError, ValueError, SQLAlchemyError)
from jiuwenclaw_manager.security.field_crypto import (
    DEK_ALG,
    ENC_SCHEME,
    ENC_VERSION,
    encrypt_sensitive_fields,
    has_sensitive,
    new_dek,
    wrap_dek_for_gateway,
)
from jiuwenclaw_manager.security.crypto_primitives import b64e as _b64
from jiuwenclaw_manager.security.frame_signer import attach_signature
from jiuwenclaw_manager.security.sign_provider import (
    get_config_signature_provider,
    get_manager_signing_key,
)
from jiuwenclaw_manager.security.keys import (
    load_instance_enc_pubkey,
    store_instance_enc_pubkey,
)
from jiuwenclaw_manager.core.instance.instance_service import (
    bootstrap_gateway_log_masking,
    bootstrap_gateway_templates,
    apply_gateway_ws_heartbeat,
    mark_instance_offline,
    register_gateway_via_ws,
)
from jiuwenclaw_manager.manager_ws_server.handshake_auth import check_handshake
from jiuwenclaw_manager.manager_ws_server.protocol import (
    FRAME_TYPE_CONFIG_ACK,
    FRAME_TYPE_HEARTBEAT,
    FRAME_TYPE_POD_STATUS_REPORT,
    FRAME_TYPE_REGISTER,
    build_config_push,
    build_connection_ack,
    build_error,
    build_heartbeat_ack,
    build_register_ack,
)
from jiuwenclaw_manager.manager_ws_server.pod_status_cache import (
    update_pod_status_snapshot,
)

logger = get_logger(__name__)


@dataclass
class _ConnectedClient:
    ws: Any
    jiuwenclaw_id: str
    service_type: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class _PendingConfigAck:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    success_flag: bool = False
    error_message: str | None = None
    result: dict[str, Any] | None = None


class ManagerWsServer:
    """接收 Gateway manager_ws_client 长连接，用于配置下发。"""

    _instance: ClassVar[ManagerWsServer | None] = None

    @classmethod
    def get_instance(cls) -> ManagerWsServer | None:
        """返回由 lifespan 注册的单例（未启动时为 ``None``）。"""
        return cls._instance

    @classmethod
    def set_instance(cls, server: ManagerWsServer | None) -> None:
        cls._instance = server

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8766,
        *,
        manager_id: str = "default",
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
    ) -> None:
        self._host = host
        self._port = port
        self._manager_id = manager_id
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server: Any = None
        self._clients: dict[int, _ConnectedClient] = {}
        # jiuwenclaw_id -> 当前活跃 WS 的 id(ws)
        self._jiuwenclaw_id_ws_id_map: dict[str, int] = {}
        self._clients_lock = asyncio.Lock()
        self._pending_acks: dict[str, _PendingConfigAck] = {}
        self._pending_acks_lock = asyncio.Lock()
        # link-auth: 防重放 nonce 缓存 + Gateway 公钥指纹固定表（仅当 CLAW_LINK_AUTH_MODE != off 时生效）
        self._link_nonce_cache = NonceCache()
        self._gateway_pin_store = InMemoryPinStore()

    @staticmethod
    def _link_keypair() -> tuple[str | None, str | None]:
        """Manager 的 link-auth 身份密钥对（复用签名身份密钥 manager_identity）。"""
        key = get_manager_signing_key()
        if key is None:
            return None, None
        return _b64(key.private_raw), _b64(key.public_raw)

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        if self._server is not None:
            logger.warning("[ManagerWsServer] already running")
            return

        try:
            from websockets.legacy.server import serve as legacy_serve

            serve_fn = legacy_serve
        except ImportError:
            import websockets

            serve_fn = websockets.serve

        self._server = await serve_fn(
            self._connection_handler,
            self._host,
            self._port,
            process_request=self._process_request,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )
        logger.info(
            "[ManagerWsServer] listening ws://%s:%s pid=%s",
            self._host,
            self._port,
            os.getpid(),
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        async with self._clients_lock:
            self._clients.clear()
            self._jiuwenclaw_id_ws_id_map.clear()
        logger.info("[ManagerWsServer] stopped")

    def _link_client_locked(self, key: int, client: _ConnectedClient) -> None:
        """在已持有 ``_clients_lock`` 时登记连接，并设为该实例的活跃 WS。"""
        old_key = self._jiuwenclaw_id_ws_id_map.get(client.jiuwenclaw_id)
        if old_key is not None and old_key != key:
            self._clients.pop(old_key, None)
        self._clients[key] = client
        self._jiuwenclaw_id_ws_id_map[client.jiuwenclaw_id] = key

    def _unlink_client_locked(self, key: int) -> _ConnectedClient | None:
        """在已持有 ``_clients_lock`` 时移除连接；仅当其为活跃连接时更新索引。"""
        client = self._clients.pop(key, None)
        if client is None:
            return None
        if self._jiuwenclaw_id_ws_id_map.get(client.jiuwenclaw_id) == key:
            del self._jiuwenclaw_id_ws_id_map[client.jiuwenclaw_id]
        return client

    def _get_active_client(
        self,
        jiuwenclaw_id: str,
        *,
        service_type: str | None = None,
    ) -> _ConnectedClient | None:
        """在已持有 ``_clients_lock`` 时返回 ``jiuwenclaw_id`` 对应的活跃连接。"""
        if not jiuwenclaw_id:
            return None
        client_key = self._jiuwenclaw_id_ws_id_map.get(jiuwenclaw_id)
        if client_key is None:
            return None
        client = self._clients.get(client_key)
        if client is None:
            return None
        if service_type is not None and client.service_type.lower() != service_type.strip().lower():
            return None
        return client

    async def lookup_active_client(
        self,
        jiuwenclaw_id: str,
        *,
        service_type: str | None = None,
    ) -> _ConnectedClient | None:
        """在连接锁保护下查找 ``jiuwenclaw_id`` 对应的活跃客户端。"""
        async with self._clients_lock:
            return self._get_active_client(jiuwenclaw_id, service_type=service_type)

    async def bind_pending_config_ack(
        self, revision: str, pending: _PendingConfigAck
    ) -> None:
        """登记待处理的 config.push ack。"""
        async with self._pending_acks_lock:
            self._pending_acks[revision] = pending

    async def release_pending_config_ack(self, revision: str) -> None:
        """移除已结束的 config.push ack 登记。"""
        async with self._pending_acks_lock:
            self._pending_acks.pop(revision, None)

    async def take_pending_config_ack(self, revision: str) -> _PendingConfigAck | None:
        """取出并保留 revision 对应的待处理 ack（供 config.ack 处理使用）。"""
        async with self._pending_acks_lock:
            return self._pending_acks.get(revision)

    async def list_registered_jiuwenclaw_ids(self) -> list[str]:
        """返回当前已注册连接的 ``jiuwenclaw_id`` 列表（去重）。"""
        async with self._clients_lock:
            return sorted(self._jiuwenclaw_id_ws_id_map.keys())

    @staticmethod
    def revision_now() -> str:
        """生成 config.push 使用的 UTC revision 字符串。"""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    async def _encrypt_config_section(
        jiuwenclaw_id: str,
        section: str,
        section_body: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """对单 section 配置做信封加密，返回（下发用 config, enc 元数据）。

        - 未开启或无该实例 Gateway 加密公钥：原样返回；若 ``config_enc_required``
          且含敏感字段则拒绝下发（fail-closed）。
        - 已开启：生成一次性 DEK 加密敏感字段，再用 Gateway 公钥包裹 DEK。
        """
        gw_pub = None
        if settings.config_enc_enabled:
            gw_pub = await load_instance_enc_pubkey(get_db_handler(), jiuwenclaw_id)
        if gw_pub is None:
            if settings.config_enc_required and has_sensitive(section, section_body):
                raise ValueError(
                    "config encryption required but no gateway enc pubkey for "
                    f"jiuwenclaw_id={jiuwenclaw_id!r}"
                )
            return {section: section_body}, None

        dek = new_dek()
        enc_body, enc_fields = encrypt_sensitive_fields(section, section_body, dek)
        if not enc_fields:
            return {section: enc_body}, None
        epk_b64, wrapped_dek_b64 = wrap_dek_for_gateway(gw_pub.public_raw, dek)
        enc_meta = {
            "scheme": ENC_SCHEME,
            "version": ENC_VERSION,
            "dek_alg": DEK_ALG,
            "gw_key_fp": gw_pub.fingerprint,
            "epk": epk_b64,
            "wrapped_dek": wrapped_dek_b64,
            "fields": enc_fields,
        }
        logger.info(
            "[ManagerWsServer] encrypted config fields jiuwenclaw_id=%s section=%s fields=%s",
            jiuwenclaw_id,
            section,
            enc_fields,
        )
        return {section: enc_body}, enc_meta

    @staticmethod
    def _sign_frame(frame: dict[str, Any]) -> dict[str, Any]:
        """按 settings 为 config.push 帧加签；未开启或无密钥时原样返回。

        在字段级加密之后执行（Encrypt-then-Sign），签名覆盖密文，
        Gateway 侧先验签后解密。
        """
        if not settings.config_sign_enabled:
            return frame
        signer = get_config_signature_provider()
        if signer is None:
            raise ValueError(
                "config signing enabled but manager signing key not loaded"
            )
        return attach_signature(frame, signer, signer.key_version)

    async def push_config_op(
        self,
        jiuwenclaw_id: str,
        config: dict[str, Any],
        *,
        revision: str | None = None,
        service_type: str | None = "gateway",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """向指定 ``jiuwenclaw_id`` 的活跃 Gateway 连接推送 config.push 并等待 ack。

        ``config`` 应为单 section 字典，例如 ``{"channel_config": {"op": "upsert", ...}}``。
        ``jiuwenclaw_id`` 写入帧 payload 顶层，不混入 section body。
        默认仅推送给 ``service_type=gateway``，避免同实例下 agent_server 等进程重复写入 Gateway 库。
        """
        if not jiuwenclaw_id:
            raise ValueError("jiuwenclaw_id is required")
        if not config:
            raise ValueError("config is required")
        if len(config) != 1:
            raise ValueError("config must contain exactly one section")
        section, section_body = next(iter(config.items()))
        if not isinstance(section_body, dict):
            raise ValueError(f"config[{section!r}] must be an object")
        rev = revision or self.revision_now()

        config, enc_meta = await self._encrypt_config_section(
            jiuwenclaw_id, section, section_body
        )

        frame = build_config_push(
            revision=rev,
            jiuwenclaw_id=jiuwenclaw_id,
            config=config,
            enc=enc_meta,
        )
        frame = self._sign_frame(frame)
        raw = json.dumps(frame, ensure_ascii=False)
        st_filter = (service_type or "").strip().lower() or None

        pending = _PendingConfigAck()
        await self.bind_pending_config_ack(rev, pending)
        try:
            client = await self.lookup_active_client(
                jiuwenclaw_id,
                service_type=st_filter,
            )
            if client is None:
                registered = await self.list_registered_jiuwenclaw_ids()
                raise ValueError(
                    f"no gateway websocket connected for jiuwenclaw_id={jiuwenclaw_id!r}; "
                    f"registered_jiuwenclaw_ids={registered}; "
                    "ensure gateway manager_ws_client is connected "
                )
            try:
                async with client.send_lock:
                    await client.ws.send(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ManagerWsServer] push failed jiuwenclaw_id=%s service_type=%s: %s",
                    jiuwenclaw_id,
                    client.service_type,
                    exc,
                )
                raise ValueError(
                    f"config.push send failed for jiuwenclaw_id={jiuwenclaw_id!r}: {exc}"
                ) from exc
            try:
                await asyncio.wait_for(pending.event.wait(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise ValueError(
                    f"gateway config.push ack timeout after {timeout}s "
                    f"(revision={rev!r})"
                ) from exc
            if not pending.success_flag:
                detail = pending.error_message or "gateway config sync failed"
                raise ValueError(detail)
            return {
                "revision": rev,
                "success_flag": True,
                "result": pending.result,
            }
        finally:
            await self.release_pending_config_ack(rev)

    async def push_config_op_to_all(
        self,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """向所有已注册 Gateway 下发配置变更（逐实例独立 revision 等待 ack）。"""
        jiuwenclaw_ids = await self.list_registered_jiuwenclaw_ids()
        if not jiuwenclaw_ids:
            return {
                "pushed_jiuwenclaw_ids": [],
                "pushed_count": 0,
                "last_ack": None,
            }

        base_rev = self.revision_now()
        last_ack: dict[str, Any] | None = None
        for jid in jiuwenclaw_ids:
            last_ack = await self.push_config_op(
                jid,
                config,
                revision=f"{base_rev}:{jid}",
            )
        return {
            "pushed_jiuwenclaw_ids": jiuwenclaw_ids,
            "pushed_count": len(jiuwenclaw_ids),
            "last_ack": last_ack,
        }

    async def _process_request(self, *args: Any) -> Any:
        """握手期 link-auth 校验。CLAW_LINK_AUTH_MODE=off 时恒放行（返回 None）。"""
        rejected = check_handshake(args, self._link_nonce_cache, self._gateway_pin_store)
        if rejected is None:
            return None
        response, reason = rejected
        logger.warning("[ManagerWsServer] handshake rejected reason=link_auth:%s", reason)
        return response

    async def _connection_handler(self, ws: Any) -> None:
        import websockets

        remote = ws.remote_address
        key = id(ws)
        logger.info("[ManagerWsServer] new connection %s", remote)

        try:
            ack = build_connection_ack(manager_id=self._manager_id)
            # link-auth 双向：Manager 在 connection.ack 里附一枚自己签的令牌，
            # 供 Gateway 反向核验"对面确是合法 Manager"。off 时不签。
            _mgr_priv, _mgr_pub = self._link_keypair()
            _ack_token = build_token(
                service_id=self._manager_id,
                service_type="manager",
                private_b64=_mgr_priv,
                public_b64=_mgr_pub,
            )
            if _ack_token and isinstance(ack.get("payload"), dict):
                ack["payload"]["link_token"] = _ack_token
            await ws.send(json.dumps(ack, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ManagerWsServer] connection.ack failed: %s", exc)

        try:
            async for raw in ws:
                await self._handle_frame(ws, key, raw)
        except websockets.exceptions.ConnectionClosed:
            logger.info("[ManagerWsServer] connection closed %s", remote)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ManagerWsServer] handler error (%s): %s", remote, exc)
        finally:
            mark_offline = False
            disconnected_jid: str | None = None
            async with self._clients_lock:
                client = self._unlink_client_locked(key)
                if client is not None:
                    disconnected_jid = client.jiuwenclaw_id
                    mark_offline = disconnected_jid not in self._jiuwenclaw_id_ws_id_map
            if disconnected_jid is not None:
                if mark_offline:
                    try:
                        handler = get_db_handler()
                        await mark_instance_offline(handler, disconnected_jid)
                        logger.info(
                            "[ManagerWsServer] gateway disconnected jiuwenclaw_id=%s -> offline",
                            disconnected_jid,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[ManagerWsServer] mark offline failed jiuwenclaw_id=%s: %s",
                            disconnected_jid,
                            exc,
                        )
                else:
                    logger.info(
                        "[ManagerWsServer] stale gateway connection closed jiuwenclaw_id=%s",
                        disconnected_jid,
                    )

    async def _handle_frame(self, ws: Any, key: int, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            err = build_error(f"invalid json: {exc}")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return

        frame_type = data.get("type")
        if frame_type == FRAME_TYPE_REGISTER:
            await self._handle_register(ws, key, data)
            return
        if frame_type == FRAME_TYPE_CONFIG_ACK:
            await self._handle_config_ack(data)
            return
        if frame_type == FRAME_TYPE_HEARTBEAT:
            await self._handle_heartbeat(ws, key, data)
            return
        if frame_type == FRAME_TYPE_POD_STATUS_REPORT:
            await self._handle_pod_status_report(key, data)
            return

        err = build_error(f"unsupported frame type: {frame_type!r}")
        await ws.send(json.dumps(err, ensure_ascii=False))

    async def _handle_config_ack(self, data: dict[str, Any]) -> None:
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        revision = str(payload.get("revision") or "")
        success_flag = bool(payload.get("success_flag"))
        error = payload.get("error_message")
        err_text = str(error).strip() if error else None
        logger.info(
            "[ManagerWsServer] config.ack revision=%s success_flag=%s",
            revision,
            success_flag,
        )
        if not revision:
            return
        pending = await self.take_pending_config_ack(revision)
        if pending is None:
            return
        pending.success_flag = success_flag
        pending.error_message = err_text
        raw_result = payload.get("result")
        pending.result = raw_result if isinstance(raw_result, dict) else None
        pending.event.set()

    async def _handle_heartbeat(self, ws: Any, key: int, data: dict[str, Any]) -> None:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return
        async with self._clients_lock:
            client = self._clients.get(key)
            if client is None:
                return
            if self._jiuwenclaw_id_ws_id_map.get(client.jiuwenclaw_id) != key:
                return
        jid = payload.get("jiuwenclaw_id") or client.jiuwenclaw_id
        if jid != client.jiuwenclaw_id:
            logger.warning(
                "[ManagerWsServer] heartbeat jiuwenclaw_id mismatch: %s != %s",
                jid,
                client.jiuwenclaw_id,
            )
            return
        raw_seq = payload.get("seq")
        seq = int(raw_seq) if isinstance(raw_seq, int) else None
        try:
            handler = get_db_handler()
            await apply_gateway_ws_heartbeat(
                handler,
                jiuwenclaw_id=jid,
                manager_id=self._manager_id,
                endpoint=str(payload.get("endpoint") or "").strip() or None,
                version=str(payload.get("version") or "").strip() or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ManagerWsServer] heartbeat failed jiuwenclaw_id=%s: %s",
                jid,
                exc,
            )
            return
        try:
            ack = build_heartbeat_ack(jiuwenclaw_id=jid, seq=seq)
            await ws.send(json.dumps(ack, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ManagerWsServer] heartbeat.ack send failed jiuwenclaw_id=%s: %s",
                jid,
                exc,
            )

    async def _handle_pod_status_report(self, key: int, data: dict[str, Any]) -> None:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return
        async with self._clients_lock:
            client = self._clients.get(key)
            if client is None:
                return
            if self._jiuwenclaw_id_ws_id_map.get(client.jiuwenclaw_id) != key:
                return
        jid = str(payload.get("jiuwenclaw_id") or "").strip()
        if jid != client.jiuwenclaw_id:
            logger.warning(
                "[ManagerWsServer] pod_status.report jiuwenclaw_id mismatch: %s != %s",
                jid,
                client.jiuwenclaw_id,
            )
            return
        status_data = payload.get("data")
        if not isinstance(status_data, dict):
            logger.warning(
                "[ManagerWsServer] pod_status.report data must be object jiuwenclaw_id=%s",
                jid,
            )
            return
        snapshot_time = payload.get("snapshot_time")
        update_pod_status_snapshot(
            jid,
            snapshot_time=str(snapshot_time) if snapshot_time else None,
            data=status_data,
        )
        logger.debug(
            "[ManagerWsServer] pod_status.report cached jiuwenclaw_id=%s total=%s",
            jid,
            status_data.get("total"),
        )

    async def _handle_register(self, ws: Any, key: int, data: dict[str, Any]) -> None:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            err = build_error("register payload must be an object")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return

        service_type = str(payload.get("service_type") or "").strip().lower()
        if not service_type:
            err = build_error("register requires service_type")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return
        if service_type != "gateway":
            err = build_error("manager ws register only accepts service_type=gateway")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return

        try:
            handler = get_db_handler()
            jiuwenclaw_id = await register_gateway_via_ws(
                handler,
                payload,
                manager_id=self._manager_id,
            )
        except RuntimeError as exc:
            err = build_error(f"manager database unavailable: {exc}")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ManagerWsServer] register failed: %s", exc)
            err = build_error(f"register failed: {exc}")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return

        client = _ConnectedClient(
            ws=ws,
            jiuwenclaw_id=jiuwenclaw_id,
            service_type=service_type,
        )
        async with self._clients_lock:
            self._link_client_locked(key, client)

        # 握手期密钥交换：① 落库 Gateway 上交的加密公钥；② 回传 Manager 签名公钥。
        enc_pubkey = str(payload.get("enc_pubkey") or "").strip()
        if enc_pubkey:
            try:
                await store_instance_enc_pubkey(
                    get_db_handler(),
                    jiuwenclaw_id,
                    enc_pubkey,
                    enc_alg=str(payload.get("enc_alg") or "X25519"),
                    fingerprint=str(payload.get("enc_pubkey_fp") or "") or None,
                )
            except _KEY_STORE_ERRORS as exc:
                # 公钥落库为握手附带步骤，失败不阻断注册（仅该实例后续无法加密）。
                logger.warning(
                    "[ManagerWsServer] store gateway enc pubkey failed jiuwenclaw_id=%s: %s",
                    jiuwenclaw_id,
                    exc,
                )
        signing_key = get_manager_signing_key()
        try:
            if signing_key is not None:
                ack = build_register_ack(
                    jiuwenclaw_id=jiuwenclaw_id,
                    sign_pubkey=_b64(signing_key.public_raw),
                    sign_alg="Ed25519",
                    key_version=signing_key.key_version,
                    sign_pubkey_fp=signing_key.fingerprint,
                )
            else:
                ack = build_register_ack(jiuwenclaw_id=jiuwenclaw_id)
            await ws.send(json.dumps(ack, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ManagerWsServer] register.ack failed: %s", exc)
        try:
            handler = get_db_handler()
            await apply_gateway_ws_heartbeat(
                handler,
                jiuwenclaw_id=jiuwenclaw_id,
                manager_id=self._manager_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ManagerWsServer] initial heartbeat on register failed jiuwenclaw_id=%s: %s",
                jiuwenclaw_id,
                exc,
            )
        registered = await self.list_registered_jiuwenclaw_ids()
        logger.info(
            "[ManagerWsServer] registered jiuwenclaw_id=%s service_type=%s "
            "active_jiuwenclaw_ids=%s pid=%s",
            jiuwenclaw_id,
            service_type,
            registered,
            os.getpid(),
        )
        # 勿在 register 帧处理栈内 await push/ack：会阻塞同连接读循环，导致 config.ack 假超时。
        asyncio.create_task(
            bootstrap_gateway_log_masking(get_db_handler(), jiuwenclaw_id),
            name=f"log_masking_bootstrap:{jiuwenclaw_id}",
        )
        asyncio.create_task(
            bootstrap_gateway_templates(get_db_handler(), jiuwenclaw_id),
            name=f"template_bootstrap:{jiuwenclaw_id}",
        )


async def push_config_op(
    jiuwenclaw_id: str,
    config: dict[str, Any],
    *,
    revision: str | None = None,
    service_type: str | None = "gateway",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """通过单例 ``ManagerWsServer`` 向 Gateway 推送配置（模块级入口）。"""
    server = ManagerWsServer.get_instance()
    if server is None:
        raise ValueError("manager_ws_server is not running")
    return await server.push_config_op(
        jiuwenclaw_id,
        config,
        revision=revision,
        service_type=service_type,
        timeout=timeout,
    )


async def push_config_op_to_all(config: dict[str, Any]) -> dict[str, Any]:
    """通过单例 ``ManagerWsServer`` 向所有 Gateway 推送配置（模块级入口）。"""
    server = ManagerWsServer.get_instance()
    if server is None:
        raise ValueError("manager_ws_server is not running")
    return await server.push_config_op_to_all(config)
