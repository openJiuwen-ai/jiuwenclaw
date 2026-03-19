# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Message Router Module.

Routes P2P and Pub-Sub messages to agents via Runner.run_agent.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

if TYPE_CHECKING:
    from openjiuwen.core.multi_agent.group_runtime.envelope import MessageEnvelope
    from openjiuwen.core.multi_agent.group_runtime.group_runtime import GroupRuntime
    from openjiuwen.core.multi_agent.group_runtime.subscription_manager import SubscriptionManager


class MessageRouter:
    """Routes messages to agents via Runner.run_agent.

    Supports both P2P (point-to-point) and Pub-Sub (fan-out) patterns.
    """

    def __init__(self, subscription_manager: SubscriptionManager, runtime: "GroupRuntime"):
        self._subscription_manager = subscription_manager
        self._runtime = runtime

    async def route_p2p_message(self, envelope: MessageEnvelope) -> Any:
        """Route a P2P message to the recipient and return the response.

        Args:
            envelope: Message envelope with recipient and optional session_id

        Returns:
            Response from the target agent
        """
        logger.debug(
            f"[{self.__class__.__name__}] Routing P2P message {envelope.message_id} "
            f"to {envelope.recipient} with session_id={envelope.session_id}"
        )

        from openjiuwen.core.runner import Runner

        try:
            session = self._build_agent_session(envelope.session_id, envelope.recipient)
            response = await Runner.run_agent(
                agent=envelope.recipient,
                inputs=envelope.message,
                session=session if session is not None else envelope.session_id,
            )
            logger.debug(
                f"[{self.__class__.__name__}] P2P message {envelope.message_id} completed"
            )
            return response

        except AttributeError as e:
            error_msg = f"Runner.run_agent not available: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.RUNNER_RUN_AGENT_ERROR,
                agent=envelope.recipient,
                reason=error_msg
            ) from e
        except Exception as e:
            error_msg = f"Error routing P2P message {envelope.message_id}: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.RUNNER_RUN_AGENT_ERROR,
                agent=envelope.recipient,
                reason=error_msg
            ) from e

    async def route_pubsub_message(self, envelope: MessageEnvelope) -> None:
        """Fan-out a Pub-Sub message to all matching subscribers.

        Subscribers are invoked concurrently. Errors per subscriber are
        logged but do not abort delivery to other subscribers.

        Args:
            envelope: Message envelope with topic_id
        """
        logger.debug(
            f"[{self.__class__.__name__}] Routing Pub-Sub message {envelope.message_id} "
            f"to topic {envelope.topic_id}"
        )

        subscribers = self._subscription_manager.get_subscribers(envelope.topic_id)

        if not subscribers:
            logger.warning(
                f"[{self.__class__.__name__}] No subscribers for topic '{envelope.topic_id}', "
                f"message '{envelope.message_id}' dropped (fire-and-forget)."
            )
            return

        tasks = [
            self._invoke_subscriber(subscriber, envelope)
            for subscriber in subscribers
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.debug(
            f"[{self.__class__.__name__}] Pub-Sub message {envelope.message_id} "
            f"delivered to {len(subscribers)} subscribers"
        )

    async def _invoke_subscriber(
            self,
            subscriber: str,
            envelope: MessageEnvelope
    ) -> None:
        """Invoke a single subscriber agent via Runner.

        Errors are logged but not re-raised so that one failing subscriber
        does not affect others.

        Args:
            subscriber: Subscriber agent ID
            envelope: Message envelope with optional session_id
        """
        try:
            from openjiuwen.core.runner import Runner
            session = self._build_agent_session(envelope.session_id, subscriber)
            await Runner.run_agent(
                agent=subscriber,
                inputs=envelope.message,
                session=session if session is not None else envelope.session_id
            )
        except AttributeError as e:
            error_msg = f"Runner.run_agent not available for subscriber {subscriber}: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
        except Exception as e:
            error_msg = f"Error invoking subscriber {subscriber}: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)

    def _build_agent_session(self, session_id: str | None, agent_id: str):
        group_session = self._runtime.get_group_session(session_id)
        if group_session is None:
            return None
        card = self._runtime.get_agent_card(agent_id)
        return group_session.create_agent_session(card=card, agent_id=agent_id)
