# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例注册与心跳（Gateway → Manager REST，上报 gateway_endpoint）。

对齐 Manager ``manager_server.core.instance.instance_service`` 中的
``register_gateway_via_ws`` / ``apply_gateway_ws_heartbeat``（HTTP 出站侧）。

连不上 Manager 时沿用原 Manager WS 客户端策略：快速重试 → 慢探测。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ...infrastructure.config import Settings, get_settings
from ...infrastructure.ha import is_gateway_primary
from ...infrastructure.utils import (
    get_gateway_register_identity,
    get_jiuwenclaw_id,
    set_jiuwenclaw_id,
)
from ...security.keys import get_or_create_gateway_enc_keypair, store_manager_sign_pubkey

logger = logging.getLogger(__name__)


def resolve_public_endpoint(cfg: Settings | None = None) -> str:
    """Manager 回调用的 Gateway Config Receiver Base URL（无尾斜杠）。

    由 ``GATEWAY_CONFIG_PUBLIC_HOST`` + ``GATEWAY_CONFIG_HTTP_PORT`` 拼接；
    host 未配置时默认 ``127.0.0.1``。
    """
    cfg = cfg or get_settings()
    port = int(cfg.gateway_config_http_port or 8775)
    host = (cfg.gateway_config_public_host or "").strip() or "127.0.0.1"
    return f"http://{host}:{port}"


def resolve_manager_http_base(cfg: Settings | None = None) -> str:
    cfg = cfg or get_settings()
    return (cfg.gateway_manager_http_url or "").strip().rstrip("/")


class InstanceService:
    """周期向 Manager 注册/心跳，刷新 ``gateway_endpoint``。

    会话模型对齐原 ``ManagerWsClient``：
    - 注册成功后按心跳间隔上报；
    - 注册/心跳失败进入快速重试，超过次数后进入慢探测。
    """

    def __init__(self, cfg: Settings | None = None) -> None:
        self._cfg = cfg or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._seq = 0
        self._stop = asyncio.Event()
        self._heartbeat_interval_seconds = max(
            5.0, float(self._cfg.gateway_config_heartbeat_seconds or 60)
        )
        self._max_reconnect_attempts = max(
            0, int(self._cfg.gateway_manager_max_reconnect_attempts)
        )
        self._reconnect_interval_seconds = max(
            0.1, float(self._cfg.gateway_manager_reconnect_interval_seconds)
        )
        self._probe_interval_seconds = max(
            self._reconnect_interval_seconds,
            float(self._cfg.gateway_manager_probe_interval_seconds),
        )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        base = resolve_manager_http_base(self._cfg)
        if not base:
            logger.info(
                "[InstanceService] skip: GATEWAY_MANAGER_HTTP_URL unset"
            )
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="instance-heartbeat")
        logger.info(
            "[InstanceService] started manager=%s endpoint=%s heartbeat=%ss "
            "fast_attempts=%s reconnect=%ss probe=%ss",
            base,
            resolve_public_endpoint(self._cfg),
            self._heartbeat_interval_seconds,
            self._max_reconnect_attempts,
            self._reconnect_interval_seconds,
            self._probe_interval_seconds,
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _compute_reconnect_delay(self, consecutive_failures: int) -> float:
        """根据连续失败次数计算下次重试等待时间（秒）。

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

    async def _wait(self, timeout: float) -> bool:
        """等待 timeout 秒；若收到 stop 则返回 True。"""
        if timeout <= 0:
            return self._stop.is_set()
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _loop(self) -> None:
        consecutive_failures = 0
        probe_mode_logged = False
        while not self._stop.is_set():
            reconnect_delay = self._reconnect_interval_seconds
            try:
                await self._session_once()
                consecutive_failures = 0
                probe_mode_logged = False
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                reconnect_delay = self._compute_reconnect_delay(consecutive_failures)
                jid_label = get_jiuwenclaw_id() or "unassigned"
                in_probe = self._in_probe_mode(consecutive_failures)
                if in_probe and not probe_mode_logged:
                    probe_mode_logged = True
                    if self._max_reconnect_attempts > 0:
                        logger.warning(
                            "[InstanceService] Manager unavailable after %s fast attempts; "
                            "entering probe mode (retry every %ss) jiuwenclaw_id=%s "
                            "last_error=%s",
                            self._max_reconnect_attempts,
                            self._probe_interval_seconds,
                            jid_label,
                            exc,
                        )
                    else:
                        logger.warning(
                            "[InstanceService] Manager unavailable; "
                            "entering probe mode (retry every %ss) jiuwenclaw_id=%s "
                            "last_error=%s",
                            self._probe_interval_seconds,
                            jid_label,
                            exc,
                        )
                elif in_probe:
                    logger.info(
                        "[InstanceService] probe register/heartbeat failed "
                        "jiuwenclaw_id=%s next in %ss: %s",
                        jid_label,
                        reconnect_delay,
                        exc,
                    )
                else:
                    logger.warning(
                        "[InstanceService] session failed jiuwenclaw_id=%s "
                        "attempt=%s/%s retry in %ss: %s",
                        jid_label,
                        consecutive_failures,
                        self._max_reconnect_attempts or "∞",
                        reconnect_delay,
                        exc,
                    )
            if self._stop.is_set():
                break
            await self._wait(reconnect_delay)

    async def _session_once(self) -> None:
        """注册一次，成功后按心跳间隔上报，直到失败或 stop。"""
        await self._register_once()
        while not self._stop.is_set():
            if await self._wait(self._heartbeat_interval_seconds):
                return
            await self._heartbeat_once()

    async def _register_payload(self) -> dict[str, Any]:
        enc_pubkey = enc_pubkey_fp = None
        try:
            keypair = await get_or_create_gateway_enc_keypair()
            from base64 import b64encode

            enc_pubkey = b64encode(keypair.public_raw).decode("ascii")
            enc_pubkey_fp = keypair.fingerprint
        except Exception:  # noqa: BLE001
            logger.debug("[InstanceService] enc keypair unavailable", exc_info=True)
        data: dict[str, Any] = {
            "service_type": "gateway",
            "jiuwenclaw_id": get_jiuwenclaw_id(),
            "enc_pubkey": enc_pubkey,
            "enc_alg": "X25519",
            "enc_pubkey_fp": enc_pubkey_fp,
            "endpoint": resolve_public_endpoint(self._cfg),
            "version": "jiuwenswarm",
        }
        data.update(get_gateway_register_identity())
        return data

    async def _register_once(self) -> None:
        base = resolve_manager_http_base(self._cfg)
        if not base:
            raise RuntimeError("GATEWAY_MANAGER_HTTP_URL unset")
        payload = await self._register_payload()
        url = f"{base}/api/v1/instances/register"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"register failed status={resp.status_code} body={resp.text[:300]}"
            )
        body = resp.json() if resp.content else {}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("register response missing data")
        await self._apply_register_ack(data)
        logger.info(
            "[InstanceService] registered jiuwenclaw_id=%s endpoint=%s",
            get_jiuwenclaw_id(),
            payload.get("endpoint"),
        )

    async def _apply_register_ack(self, data: dict[str, Any]) -> None:
        jid = str(data.get("jiuwenclaw_id") or "").strip()
        if jid:
            set_jiuwenclaw_id(jid)
            try:
                from ..enterprise_config.gateway_db import GatewayDb

                GatewayDb.bind(jid)
            except Exception:  # noqa: BLE001
                logger.debug("[InstanceService] GatewayDb.bind failed", exc_info=True)
        sign_pubkey = str(data.get("sign_pubkey") or "").strip()
        if jid and sign_pubkey:
            await store_manager_sign_pubkey(
                jid,
                sign_pubkey,
                key_version=str(data.get("key_version") or "v1"),
                manager_id=str(data.get("manager_id") or "default"),
                sign_alg=str(data.get("sign_alg") or "Ed25519"),
                fingerprint=str(data.get("sign_pubkey_fp") or "") or None,
            )

    async def _heartbeat_once(self) -> None:
        base = resolve_manager_http_base(self._cfg)
        jid = get_jiuwenclaw_id()
        if not base:
            raise RuntimeError("GATEWAY_MANAGER_HTTP_URL unset")
        if not jid:
            # 尚未拿到 id：回到注册
            await self._register_once()
            return
        self._seq += 1
        payload = {
            "jiuwenclaw_id": jid,
            "service_type": "gateway",
            "version": "jiuwenswarm",
            "endpoint": resolve_public_endpoint(self._cfg),
            "seq": self._seq,
            "role": "PRIMARY" if is_gateway_primary() else "STANDBY",
        }
        url = f"{base}/api/v1/instances/heartbeat"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"heartbeat failed status={resp.status_code} body={resp.text[:200]}"
            )
