"""RabbitMQ 消费者：订阅设计文档 5.2 的 instance.* 事件，写入心跳与上下线状态。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.messaging import consume_topic_json_forever

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

    async def _handler(body: dict[str, Any], routing_key: str) -> None:
        factory = get_session_factory()
        async with factory() as session:
            svc = InstanceService(session)
            try:
                await svc.process_instance_mq_payload(body, routing_key)
                await session.commit()
            except ValueError as exc:
                await session.rollback()
                _log.warning(
                    "rabbitmq_instance_event_skipped",
                    routing_key=routing_key,
                    error=str(exc),
                )

    await consume_topic_json_forever(
        amqp_url=url,
        exchange_name=settings.rabbitmq_exchange,
        routing_key_pattern=settings.rabbitmq_routing_key,
        queue_name=queue_name,
        prefetch_count=20,
        handler=_handler,
    )

    _log.info("rabbitmq_consumer_stopped")
