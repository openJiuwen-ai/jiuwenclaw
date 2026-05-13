"""RabbitMQ 消费者：订阅设计文档 5.2 的 instance.* 事件，写入心跳与上下线状态。"""

from __future__ import annotations

import json
from typing import Any

import aio_pika
from aio_pika import ExchangeType, IncomingMessage

from jiuwenclaw_manager.config import settings
from jiuwenclaw_manager.infrastructure.db import get_session_factory
from jiuwenclaw_manager.infrastructure.logger import get_logger
from jiuwenclaw_manager.services.instance_service import InstanceService

_log = get_logger(__name__)

# 与 Gateway / Agent-Server 发布端对齐的 routing key（Topic）
# - event.instance.online
# - event.instance.offline
# - event.instance.heartbeat


async def start_consumer() -> None:
    """阻塞消费 AMQP 消息，直到任务被 cancel（由 FastAPI lifespan 在进程退出时触发）。

    需配置 ``CLAWMANAGER_RABBITMQ_URL``；未配置时立即返回。
    """
    url = settings.rabbitmq_url
    _log.info(f"rabbitmq_url: {url}")

    if not url:
        _log.info("rabbitmq_consumer_skipped", reason="CLAWMANAGER_RABBITMQ_URL unset")
        return

    queue_name = settings.rabbitmq_queue_name or f"claw_manager_{settings.manager_id}"
    _log.info(
        "rabbitmq_consumer_starting",
        exchange=settings.rabbitmq_exchange,
        routing_key=settings.rabbitmq_routing_key,
        queue=queue_name,
        manager_id=settings.manager_id,
    )

    connection = await aio_pika.connect_robust(url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=20)

        exchange = await channel.declare_exchange(
            settings.rabbitmq_exchange,
            ExchangeType.TOPIC,
            durable=True,
        )
        queue = await channel.declare_queue(queue_name, durable=True, auto_delete=False)
        await queue.bind(exchange, routing_key=settings.rabbitmq_routing_key)

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                await _handle_message(message)

    _log.info("rabbitmq_consumer_stopped")


async def _handle_message(message: IncomingMessage) -> None:
    routing_key = message.routing_key or ""
    async with message.process(requeue=False):
        try:
            raw = message.body.decode("utf-8")
            body: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _log.warning("rabbitmq_bad_encoding_or_json", routing_key=routing_key, error=str(exc))
            return
        if not isinstance(body, dict):
            _log.warning("rabbitmq_body_not_object", routing_key=routing_key)
            return

        factory = get_session_factory()
        async with factory() as session:
            svc = InstanceService(session)
            await svc.process_instance_mq_payload(body, routing_key)
            await session.commit()
