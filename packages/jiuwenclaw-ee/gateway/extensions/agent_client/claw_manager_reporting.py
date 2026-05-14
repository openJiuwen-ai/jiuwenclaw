# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""向 Claw Manager 上报 instance.online / heartbeat / offline（RabbitMQ Topic）。

由扩展在 ``WEB_CHANNEL_CREATED``（Gateway）与 ``AGENT_SERVER_LISTENING``（AgentServer）上各启动一次后台任务。

环境变量（由 Manager 本地拉起子进程时注入）：
- ``CLAWMANAGER_RABBITMQ_URL``：AMQP URL
- ``JIUWENCLAW_PROVISIONED_INSTANCE_ID``：组网实例 ID
- ``CLAWMANAGER_MANAGER_ID``：管理面 ID（默认 ``default``）
- ``JIUWENCLAW_SERVICE_ID``：本进程稳定 service_id
- ``MANAGEMENT_API_BASE``（可选）：agent_client REST 根

需安装 ``openjiuwen-runtime-foundation[amqp]`` 或 ``aio-pika>=9.4``。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from openjiuwen_runtime.foundation.messaging import AmqpTopicJsonPublisher

if TYPE_CHECKING:
    from jiuwenclaw.extensions.registry import ExtensionRegistry

logger = logging.getLogger(__name__)

_EXCHANGE = "jiuwenclaw.events"

_report_task: asyncio.Task[None] | None = None
_publisher: AmqpTopicJsonPublisher | None = None


def _enabled() -> bool:
    return bool(os.getenv("CLAWMANAGER_RABBITMQ_URL") and os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID"))


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload(
    *,
    event_type: str,
    manager_id: str,
    jiuwenclaw_id: str,
    service_id: str,
    service_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "timestamp": _iso(),
        "service_id": service_id,
        "service_type": service_type,
        "manager_id": manager_id,
        "jiuwenclaw_id": jiuwenclaw_id,
        "data": data,
    }


def _get_publisher() -> AmqpTopicJsonPublisher:
    global _publisher
    if _publisher is None:
        _publisher = AmqpTopicJsonPublisher(os.environ["CLAWMANAGER_RABBITMQ_URL"])
    return _publisher


async def _publish(routing_key: str, body: dict[str, Any]) -> None:
    pub = _get_publisher()
    await pub.publish(exchange_name=_EXCHANGE, routing_key=routing_key, body=body)


async def claw_manager_event_loop(
    *,
    service_type: str,
    service_id: str,
    heartbeat_interval_seconds: float = 10.0,
    extra_data_factory: Callable[[], dict[str, Any]] | None = None,
) -> None:
    if not _enabled():
        return

    manager_id = os.getenv("CLAWMANAGER_MANAGER_ID", "default")
    jiuwenclaw_id = os.environ["JIUWENCLAW_PROVISIONED_INSTANCE_ID"]

    def _merge_data(base: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        if extra_data_factory is not None:
            try:
                extra = extra_data_factory()
                if isinstance(extra, dict):
                    out.update(extra)
            except Exception:  # noqa: BLE001
                logger.exception("[claw_manager_reporting] extra_data_factory failed")
        return out

    online_body = _payload(
        event_type="instance.online",
        manager_id=manager_id,
        jiuwenclaw_id=jiuwenclaw_id,
        service_id=service_id,
        service_type=service_type,
        data=_merge_data({}),
    )
    try:
        await _publish("event.instance.online", online_body)
    except Exception:  # noqa: BLE001
        logger.exception("[claw_manager_reporting] publish online failed")

    try:
        while True:
            await asyncio.sleep(heartbeat_interval_seconds)
            hb = _payload(
                event_type="instance.heartbeat",
                manager_id=manager_id,
                jiuwenclaw_id=jiuwenclaw_id,
                service_id=service_id,
                service_type=service_type,
                data=_merge_data({}),
            )
            await _publish("event.instance.heartbeat", hb)
    except asyncio.CancelledError:
        raise


async def publish_offline_safe(
    *,
    service_type: str,
    service_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    if not _enabled():
        return
    manager_id = os.getenv("CLAWMANAGER_MANAGER_ID", "default")
    jiuwenclaw_id = os.environ["JIUWENCLAW_PROVISIONED_INSTANCE_ID"]
    body = _payload(
        event_type="instance.offline",
        manager_id=manager_id,
        jiuwenclaw_id=jiuwenclaw_id,
        service_id=service_id,
        service_type=service_type,
        data=data or {},
    )
    try:
        await _publish("event.instance.offline", body)
    except Exception:  # noqa: BLE001
        logger.warning("[claw_manager_reporting] publish offline failed", exc_info=True)
    global _publisher
    if _publisher is not None:
        try:
            await _publisher.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("[claw_manager_reporting] publisher close failed", exc_info=True)
        _publisher = None


def register_claw_manager_dmq_hooks(registry: ExtensionRegistry) -> None:
    from jiuwenclaw.schema.hook_event import AgentServerHookEvents, GatewayHookEvents

    registry.register(
        GatewayHookEvents.WEB_CHANNEL_CREATED,
        _on_web_channel_created_sync,
        priority=500,
    )
    registry.register(
        AgentServerHookEvents.AGENT_SERVER_LISTENING,
        _on_agent_server_listening_sync,
        priority=500,
    )


def _on_web_channel_created_sync(ctx: Any) -> None:
    if not _enabled():
        return
    try:
        import aio_pika  # noqa: F401
    except ImportError:
        logger.warning(
            "[claw_manager_reporting] aio-pika missing; skip DMQ (pip install aio-pika>=9.4)"
        )
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _ensure_report_task(
        loop,
        service_type="gateway",
        service_id=os.getenv("JIUWENCLAW_SERVICE_ID", "gateway-1"),
        extra_data_factory=lambda: {
            "version": os.getenv("JIUWENCLAW_VERSION", "0.0.0"),
            "endpoint": f"http://{getattr(ctx, 'host', '127.0.0.1')}:{int(getattr(ctx, 'port', 19000))}",
            **(
                {"management_api_base": m}
                if (m := os.getenv("MANAGEMENT_API_BASE", "").strip())
                else {}
            ),
        },
    )


def _on_agent_server_listening_sync(ctx: Any) -> None:
    if not _enabled():
        return
    try:
        import aio_pika  # noqa: F401
    except ImportError:
        logger.warning(
            "[claw_manager_reporting] aio-pika missing; skip DMQ (pip install aio-pika>=9.4)"
        )
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    host = str(getattr(ctx, "host", os.getenv("AGENT_SERVER_HOST", "127.0.0.1")))
    port = int(getattr(ctx, "port", int(os.getenv("AGENT_SERVER_PORT", "18092"))))
    _ensure_report_task(
        loop,
        service_type="agent_server",
        service_id=os.getenv("JIUWENCLAW_SERVICE_ID", "agentserver-1"),
        extra_data_factory=lambda: {
            "version": os.getenv("JIUWENCLAW_VERSION", "0.0.0"),
            "endpoint": f"ws://{host}:{port}",
        },
    )


def _ensure_report_task(
    loop: asyncio.AbstractEventLoop,
    *,
    service_type: str,
    service_id: str,
    extra_data_factory: Callable[[], dict[str, Any]],
) -> None:
    global _report_task
    if _report_task is not None and not _report_task.done():
        return

    async def _runner() -> None:
        try:
            await claw_manager_event_loop(
                service_type=service_type,
                service_id=service_id,
                heartbeat_interval_seconds=float(
                    os.getenv("CLAWMANAGER_HEARTBEAT_INTERVAL_SECONDS", "10")
                ),
                extra_data_factory=extra_data_factory,
            )
        except asyncio.CancelledError:
            await publish_offline_safe(service_type=service_type, service_id=service_id)
            raise

    _report_task = loop.create_task(_runner(), name="claw-manager-dmq-events")


async def shutdown_reporting_task() -> None:
    global _report_task
    if _report_task is not None:
        _report_task.cancel()
        try:
            await _report_task
        except asyncio.CancelledError:
            pass
        _report_task = None
