"""RabbitMQ 消费者：订阅设计文档 5.2 的 instance.* 事件，写入心跳与上下线状态。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.messaging import consume_topic_json_forever

from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.core.instance import InstanceService
from jiuwenclaw_manager.infrastructure.logger import get_logger

_log = get_logger(__name__)


async def start_consumer(handler: DBHandler) -> None:
    """阻塞消费 AMQP 消息，直到任务被 cancel（由 FastAPI lifespan 在进程退出时触发）。"""
    url = settings.rabbitmq_url
    if not url:
        _log.info("rabbitmq_consumer_skipped", reason="MANAGER_RABBITMQ_URL unset")
        return

    queue_name = settings.rabbitmq_queue_name or f"claw_manager_{settings.manager_id}"
    _log.info(
        "rabbitmq_consumer_starting",
        rabbitmq_url=url,
        exchange=settings.rabbitmq_exchange,
        routing_key=settings.rabbitmq_routing_key,
        queue=queue_name,
        manager_id=settings.manager_id,
    )

    async def _handler(body: dict[str, Any], routing_key: str) -> None:
        svc = InstanceService(handler)
        try:
            await svc.process_instance_mq_payload(body, routing_key)
        except ValueError as exc:
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
