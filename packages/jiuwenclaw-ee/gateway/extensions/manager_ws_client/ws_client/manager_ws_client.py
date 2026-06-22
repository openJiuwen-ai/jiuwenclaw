# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway manager_ws_client：连接 Claw Manager 配置下发 WebSocket。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from base64 import b64encode

from openjiuwen_runtime.foundation.security.link_auth import InMemoryPinStore, build_token_header, verify_and_pin
from sqlalchemy.exc import SQLAlchemyError

from ..core.enterprise_config.gateway_db import GatewayDb
from ..infrastructure.config import get_settings
from ..infrastructure.utils import get_jiuwenclaw_id, set_jiuwenclaw_id
from ..security.field_crypto import DecryptError, decrypt_config, unwrap_dek
from ..security.frame_verifier import Ed25519Verifier, ReplayGuard, verify_frame
from ..security.keys import (
    get_or_create_gateway_enc_keypair,
    get_or_create_gateway_sign_keypair,
    load_gateway_enc_privkey_by_fp,
    load_manager_sign_pubkey,
    store_manager_sign_pubkey,
)
from .protocol import (
    EVENT_CONNECTION_ACK,
    EVENT_REGISTER_ACK,
    FRAME_TYPE_CONFIG_PUSH,
    FRAME_TYPE_ERROR,
    FRAME_TYPE_EVENT,
    FRAME_TYPE_HEARTBEAT_ACK,
    build_config_ack,
    build_heartbeat,
    build_pod_status_report,
    build_register,
)

logger = logging.getLogger(__name__)

# 密钥准备/落库的预期失败类型：DB 未就绪/连接/语句错误、表未初始化、格式问题。
# 收敛后真正的代码 bug 不再被吞掉。
_KEY_STORE_ERRORS = (RuntimeError, OSError, ValueError, SQLAlchemyError)

ManagerWsConfigPushHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class ManagerWsClient:
    """连接 Claw Manager manager_ws_server，接收 config.push 并回 ack。"""

    @staticmethod
    def _build_ws_origin(uri: str) -> str | None:
        try:
            parsed = urlsplit(uri)
        except ValueError:
            return None
        if not parsed.netloc:
            return None
        scheme = "https" if parsed.scheme == "wss" else "http"
        return f"{scheme}://{parsed.netloc}"

    def __init__(
        self,
        *,
        service_type: str = "gateway",
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
        on_config_push: ManagerWsConfigPushHandler | None = None,
    ) -> None:
        cfg = get_settings()
        self._service_type = service_type
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._heartbeat_interval_seconds = max(
            1.0, float(cfg.gateway_manager_ws_heartbeat_interval_seconds)
        )
        self._max_reconnect_attempts = max(
            0, int(cfg.gateway_manager_ws_max_reconnect_attempts)
        )
        self._reconnect_interval_seconds = max(
            0.1, float(cfg.gateway_manager_ws_reconnect_interval_seconds)
        )
        self._probe_interval_seconds = max(
            self._reconnect_interval_seconds,
            float(cfg.gateway_manager_ws_probe_interval_seconds),
        )
        self._on_config_push = on_config_push
        self._uri: str | None = None
        self._ws: Any = None
        self._running = False
        self._session_task: asyncio.Task[None] | None = None
        self._ready = False
        self._heartbeat_ack_event: asyncio.Event | None = None
        self._heartbeat_seq = 0
        self._heartbeat_ack_timeout_seconds = max(
            3.0,
            min(self._heartbeat_interval_seconds * 0.8, self._heartbeat_interval_seconds - 1.0),
        )
        # 防重放状态：nonce 去重 TTL 取时间窗 2 倍余量。
        self._replay_guard = ReplayGuard(
            ttl_seconds=max(60.0, float(cfg.gateway_config_sign_skew_seconds) * 2)
        )
        # link-auth：对 Manager 做 TOFU 指纹固定（仅当 CLAW_LINK_AUTH_MODE != off 时生效）。
        self._manager_pin_store = InMemoryPinStore()

        self._pod_status_interval_seconds = max(
            1.0, float(cfg.gateway_manager_ws_pod_status_interval_seconds)
        )
        self._send_lock = asyncio.Lock()

    @property
    def jiuwenclaw_id(self) -> str | None:
        return get_jiuwenclaw_id()

    @property
    def ready(self) -> bool:
        return self._ready and self._ws is not None

    def set_config_push_handler(self, handler: ManagerWsConfigPushHandler | None) -> None:
        self._on_config_push = handler

    async def connect(self, uri: str) -> None:
        # 同一 URI 且会话任务仍在跑时勿重复 disconnect，否则会清空 JIUWENCLAW_ID
        # 并以无 id 的 register 在 Manager 侧再建一条 instance。
        if (
            self._uri == uri
            and self._session_task is not None
            and not self._session_task.done()
        ):
            return
        if self._session_task is not None and not self._session_task.done():
            await self.disconnect()

        self._uri = uri
        self._running = True
        self._session_task = asyncio.create_task(
            self._session_loop(uri),
            name="manager-ws-client",
        )

    async def disconnect(self) -> None:
        self._running = False
        self._ready = False
        set_jiuwenclaw_id(None)
        GatewayDb.bind(None)
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ManagerWsClient] close error: %s", exc)
            self._ws = None
        if self._session_task is not None:
            self._session_task.cancel()
            try:
                await self._session_task
            except asyncio.CancelledError:
                pass
            self._session_task = None
        logger.info("[ManagerWsClient] disconnected")

    def _compute_reconnect_delay(self, consecutive_failures: int) -> float:
        """根据连续失败次数计算下次重连等待时间（秒）。

        - 有 fast 阶段（max_reconnect_attempts > 0）：前 N 次用短间隔，之后进入 probe 模式。
        - 无 fast 阶段（max_reconnect_attempts == 0）：指数退避，上限为 probe 间隔。
        """
        if consecutive_failures <= 0:
            return self._reconnect_interval_seconds
        if self._max_reconnect_attempts == 0:
            backoff = self._reconnect_interval_seconds * (2 ** (consecutive_failures - 1))
            return min(backoff, self._probe_interval_seconds)
        if consecutive_failures <= self._max_reconnect_attempts:
            return self._reconnect_interval_seconds
        return self._probe_interval_seconds

    def _in_probe_mode(self, consecutive_failures: int) -> bool:
        if consecutive_failures <= 0:
            return False
        if self._max_reconnect_attempts == 0:
            delay = self._compute_reconnect_delay(consecutive_failures)
            return delay >= self._probe_interval_seconds
        return consecutive_failures > self._max_reconnect_attempts

    async def _session_loop(self, uri: str) -> None:
        consecutive_failures = 0
        probe_mode_logged = False
        while self._running:
            reconnect_delay = self._reconnect_interval_seconds
            try:
                await self._connect_once(uri)
                consecutive_failures = 0
                probe_mode_logged = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                reconnect_delay = self._compute_reconnect_delay(consecutive_failures)
                jid_label = get_jiuwenclaw_id() or "unassigned"
                in_probe = self._in_probe_mode(consecutive_failures)
                if in_probe and not probe_mode_logged:
                    probe_mode_logged = True
                    if self._max_reconnect_attempts > 0:
                        logger.warning(
                            "[ManagerWsClient] Manager unavailable after %s fast attempts; "
                            "entering probe mode (retry every %ss) jiuwenclaw_id=%s "
                            "last_error=%s",
                            self._max_reconnect_attempts,
                            self._probe_interval_seconds,
                            jid_label,
                            exc,
                        )
                    else:
                        logger.warning(
                            "[ManagerWsClient] Manager unavailable; "
                            "entering probe mode (retry every %ss) jiuwenclaw_id=%s "
                            "last_error=%s",
                            self._probe_interval_seconds,
                            jid_label,
                            exc,
                        )
                elif in_probe:
                    logger.info(
                        "[ManagerWsClient] probe reconnect failed jiuwenclaw_id=%s "
                        "next in %ss: %s",
                        jid_label,
                        reconnect_delay,
                        exc,
                    )
                else:
                    logger.warning(
                        "[ManagerWsClient] session failed jiuwenclaw_id=%s "
                        "attempt=%s/%s retry in %ss: %s",
                        jid_label,
                        consecutive_failures,
                        self._max_reconnect_attempts or "∞",
                        reconnect_delay,
                        exc,
                    )
            self._ready = False
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ws = None
            if not self._running:
                break
            await asyncio.sleep(reconnect_delay)

    async def _connect_once(self, uri: str) -> None:
        origin = self._build_ws_origin(uri)
        try:
            from websockets.legacy.client import connect as legacy_connect

            connect_fn = legacy_connect
        except ImportError:
            import websockets

            connect_fn = websockets.connect

        # link-auth: 以 gateway 身份（Ed25519 持久签名密钥）向 Manager 出示链路令牌
        # （mode=off 时为空，不加头）。
        _gw_sign_kp = await get_or_create_gateway_sign_keypair()
        extra_headers = build_token_header(
            service_id=os.getenv("JIUWENCLAW_SERVICE_ID", "gateway-1"),
            service_type=self._service_type,
            private_b64=_gw_sign_kp.private_b64,
            public_b64=_gw_sign_kp.public_b64,
        )
        logger.info("[ManagerWsClient] connecting %s", uri)
        async with connect_fn(
            uri,
            origin=origin,
            extra_headers=extra_headers or None,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=5.0,
        ) as ws:
            self._ws = ws
            if not await self._handshake(ws):
                raise RuntimeError("manager ws handshake failed")
            logger.info(
                "[ManagerWsClient] session active jiuwenclaw_id=%s uri=%s",
                get_jiuwenclaw_id(),
                uri,
            )
            await self._recv_loop(ws)

    async def _handshake(self, ws: Any) -> bool:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        data = json.loads(raw)
        if (
            data.get("type") == FRAME_TYPE_EVENT
            and data.get("event") == EVENT_CONNECTION_ACK
        ):
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
            # link-auth 双向：核验 Manager 在 connection.ack 里出示的令牌，
            # 验签 + TOFU 指纹固定，确认连到的是合法 Manager（防 MITM/冒充）。off 时恒放行。
            _res = verify_and_pin(
                self._manager_pin_store,
                payload.get("link_token"),
                expect_type="manager",
            )
            if not _res.allowed:
                raise RuntimeError(f"manager link-auth failed: {_res.reason}")
            logger.info(
                "[ManagerWsClient] connection.ack manager_id=%s",
                payload.get("manager_id"),
            )
        else:
            logger.warning("[ManagerWsClient] unexpected first frame: %s", data.get("type"))

        # 握手期上交本机加密公钥（首启自动生成密钥对，私钥永不外发）。
        enc_pubkey = enc_pubkey_fp = None
        try:
            keypair = await get_or_create_gateway_enc_keypair()
            enc_pubkey = b64encode(keypair.public_raw).decode("ascii")
            enc_pubkey_fp = keypair.fingerprint
        except _KEY_STORE_ERRORS as exc:
            # 准备加密公钥为握手附带步骤，失败不阻断注册（仅后续无法解密）。
            logger.warning("[ManagerWsClient] prepare enc keypair failed: %s", exc)

        reg = build_register(
            service_type=self._service_type,
            enc_pubkey=enc_pubkey,
            enc_pubkey_fp=enc_pubkey_fp,
        )
        await ws.send(json.dumps(reg, ensure_ascii=False))

        raw_reg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        reg_data = json.loads(raw_reg)
        if reg_data.get("type") == FRAME_TYPE_ERROR:
            payload = reg_data.get("payload") if isinstance(reg_data.get("payload"), dict) else {}
            raise RuntimeError(f"manager ws register rejected: {payload.get('message')}")
        if not (
            reg_data.get("type") == FRAME_TYPE_EVENT
            and reg_data.get("event") == EVENT_REGISTER_ACK
        ):
            raise RuntimeError(
                f"manager ws expected register.ack, got type={reg_data.get('type')!r} "
                f"event={reg_data.get('event')!r}"
            )
        ack_payload = reg_data.get("payload") if isinstance(reg_data.get("payload"), dict) else {}
        ack_jiuwenclaw_id = str(ack_payload.get("jiuwenclaw_id") or "").strip()
        if not ack_jiuwenclaw_id:
            raise RuntimeError("manager ws register.ack missing jiuwenclaw_id")
        set_jiuwenclaw_id(ack_jiuwenclaw_id)
        GatewayDb.bind(ack_jiuwenclaw_id)

        # 确认配对：落库 Manager 签名公钥（按版本+指纹），供后续验签。
        sign_pubkey = str(ack_payload.get("sign_pubkey") or "").strip()
        if sign_pubkey:
            try:
                await store_manager_sign_pubkey(
                    ack_jiuwenclaw_id,
                    sign_pubkey,
                    key_version=str(ack_payload.get("key_version") or "v1"),
                    manager_id=str(ack_payload.get("manager_id") or "default"),
                    sign_alg=str(ack_payload.get("sign_alg") or "Ed25519"),
                    fingerprint=str(ack_payload.get("sign_pubkey_fp") or "") or None,
                )
            except _KEY_STORE_ERRORS as exc:
                # 落库签名公钥为握手附带步骤，失败不阻断注册（仅后续无法验签）。
                logger.warning(
                    "[ManagerWsClient] store manager sign pubkey failed: %s", exc
                )

        self._ready = True
        logger.info(
            "[ManagerWsClient] registered jiuwenclaw_id=%s service_type=%s (register.ack ok)",
            ack_jiuwenclaw_id,
            self._service_type,
        )
        return True

    async def _recv_loop(self, ws: Any) -> None:
        self._heartbeat_ack_event = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(ws),
            name="manager-ws-heartbeat",
        )
        pod_status_task = asyncio.create_task(
            self._pod_status_report_loop(ws),
            name="manager-ws-pod-status-report",
        )
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.warning("[ManagerWsClient] invalid json: %s", exc)
                    continue
                await self._dispatch(ws, data)
        finally:
            heartbeat_task.cancel()
            pod_status_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            try:
                await pod_status_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_ack_event = None

    async def _heartbeat_loop(self, ws: Any) -> None:
        """周期性发送 heartbeat，等待 Manager ``heartbeat.ack``，超时则断开以触发重连。"""
        ack_event = self._heartbeat_ack_event
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            jid = get_jiuwenclaw_id()
            if not jid or ack_event is None:
                continue
            self._heartbeat_seq += 1
            seq = self._heartbeat_seq
            ack_event.clear()
            frame = build_heartbeat(
                jiuwenclaw_id=jid,
                service_type=self._service_type,
                seq=seq,
            )
            try:
                async with self._send_lock:
                    await ws.send(json.dumps(frame, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                logger.debug("[ManagerWsClient] heartbeat send failed: %s", exc)
                return
            try:
                await asyncio.wait_for(
                    ack_event.wait(),
                    timeout=self._heartbeat_ack_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[ManagerWsClient] heartbeat.ack timeout jiuwenclaw_id=%s "
                    "seq=%s after %ss; reconnecting",
                    jid,
                    seq,
                    self._heartbeat_ack_timeout_seconds,
                )
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
                return

    async def _pod_status_report_loop(self, ws: Any) -> None:
        """周期性上报当前 Gateway 创建的 AgentServer Pod 状态。"""
        while True:
            await asyncio.sleep(self._pod_status_interval_seconds)
            jid = get_jiuwenclaw_id()
            if not jid:
                continue
            try:
                from jiuwenclaw.extensions.registry import ExtensionRegistry

                registry = ExtensionRegistry.get_instance()
                ext = registry.get_agent_server_client_extension()
                if ext is None or not hasattr(ext, "get_client"):
                    continue
                client = ext.get_client()
                if client is None or not hasattr(client, "collect_pod_status"):
                    continue
                status_data = await client.collect_pod_status()
                try:
                    if hasattr(client, "collect_request_volume"):
                        bv = client.collect_request_volume()
                        if bv is not None:
                            status_data["request_volume"] = bv
                except Exception as _bv_exc:
                    logger.debug("[ManagerWsClient] collect_request_volume failed: %s", _bv_exc)
                snapshot_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                frame = build_pod_status_report(
                    jiuwenclaw_id=jid,
                    service_type=self._service_type,
                    snapshot_time=snapshot_time,
                    data=status_data,
                )
                async with self._send_lock:
                    await ws.send(json.dumps(frame, ensure_ascii=False))
                logger.debug(
                    "[ManagerWsClient] pod_status.report sent jiuwenclaw_id=%s total=%s",
                    jid,
                    status_data.get("total"),
                )
            except Exception as exc:
                logger.warning("[ManagerWsClient] pod_status.report failed: %s", exc)

    def _notify_heartbeat_ack(self, data: dict[str, Any]) -> None:
        ack_event = self._heartbeat_ack_event
        if ack_event is None:
            return
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return
        if str(payload.get("status") or "").strip().lower() != "ok":
            return
        jid = get_jiuwenclaw_id()
        ack_jid = str(payload.get("jiuwenclaw_id") or "").strip()
        if jid and ack_jid and ack_jid != jid:
            return
        raw_seq = payload.get("seq")
        if isinstance(raw_seq, int) and raw_seq != self._heartbeat_seq:
            return
        ack_event.set()

    async def _dispatch(self, ws: Any, data: dict[str, Any]) -> None:
        frame_type = data.get("type")
        if frame_type == FRAME_TYPE_HEARTBEAT_ACK:
            self._notify_heartbeat_ack(data)
            return
        if frame_type == FRAME_TYPE_CONFIG_PUSH:
            await self._handle_config_push(ws, data)
            return
        if frame_type == FRAME_TYPE_ERROR:
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
            logger.warning("[ManagerWsClient] server error: %s", payload.get("message"))
            return
        logger.debug("[ManagerWsClient] ignored frame type=%s", frame_type)

    async def _handle_config_push(self, ws: Any, data: dict[str, Any]) -> None:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return
        revision = str(payload.get("revision") or "")
        jiuwenclaw_id = payload.get("jiuwenclaw_id")
        if not isinstance(jiuwenclaw_id, str) or not jiuwenclaw_id:
            raise ValueError("config.push payload requires jiuwenclaw_id")
        config = payload.get("config")
        if not isinstance(config, dict):
            config = {}

        cfg = get_settings()

        # 1) 验签 + 防重放：未经验证的配置永不进入业务层（fail-closed）。
        #    先验签后解密——签名覆盖的是收到的密文帧，先确认未被篡改/重放再解密。
        if cfg.gateway_config_verify_enabled:
            sig = payload.get("sig") if isinstance(payload.get("sig"), dict) else None
            verifier = None
            if isinstance(sig, dict):
                pub = await load_manager_sign_pubkey(
                    jiuwenclaw_id, key_version=str(sig.get("key_id") or "") or None
                )
                if pub is not None:
                    verifier = Ed25519Verifier(pub.public_raw)
            ok, err = verify_frame(
                payload,
                sig,
                verifier,
                self._replay_guard,
                skew_seconds=float(cfg.gateway_config_sign_skew_seconds),
                required=cfg.gateway_config_verify_required,
            )
            if not ok:
                logger.warning(
                    "[ManagerWsClient] reject config.push revision=%s: %s",
                    revision,
                    err,
                )
                ack = build_config_ack(
                    revision=revision,
                    success_flag=False,
                    error_message=f"signature verify failed: {err}",
                )
                await ws.send(json.dumps(ack, ensure_ascii=False))
                return

        # 2) 信封解密：用本机私钥解出 DEK，再还原 ENC 信封字段（fail-closed）。
        enc = payload.get("enc") if isinstance(payload.get("enc"), dict) else None
        if cfg.gateway_config_dec_enabled:
            try:
                dek = None
                if enc and enc.get("scheme") == "hybrid":
                    priv = await load_gateway_enc_privkey_by_fp(enc.get("gw_key_fp"))
                    if priv is None:
                        raise DecryptError("no matching gateway enc privkey for gw_key_fp")
                    dek = unwrap_dek(priv, str(enc.get("epk") or ""), str(enc.get("wrapped_dek") or ""))
                config = decrypt_config(config, dek)
            except ValueError as exc:
                logger.warning(
                    "[ManagerWsClient] config decrypt failed revision=%s: %s",
                    revision,
                    exc,
                )
                ack = build_config_ack(
                    revision=revision,
                    success_flag=False,
                    error_message=f"config decrypt failed: {exc}",
                )
                await ws.send(json.dumps(ack, ensure_ascii=False))
                return

        success_flag = True
        err_msg: str | None = None
        sync_result: dict[str, Any] | None = None
        if self._on_config_push is not None:
            try:
                sync_result = await self._on_config_push(
                    revision,
                    jiuwenclaw_id,
                    config,
                )
            except Exception as exc:
                success_flag = False
                err_msg = str(exc)
                logger.exception("[ManagerWsClient] on_config_push failed: %s", exc)

        ack = build_config_ack(
            revision=revision,
            success_flag=success_flag,
            error_message=err_msg,
            result=sync_result,
        )
        async with self._send_lock:
            await ws.send(json.dumps(ack, ensure_ascii=False))
        logger.info(
            "[ManagerWsClient] config.ack revision=%s success_flag=%s",
            revision,
            success_flag,
        )
