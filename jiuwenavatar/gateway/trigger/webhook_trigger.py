# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Webhook Trigger — Webhook 回调触发器，接收外部 HTTP 回调并触发分身."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from jiuwenavatar.gateway.trigger.base import ITrigger, TriggerCallback
from jiuwenavatar.gateway.trigger.models import TriggerConfig

logger = logging.getLogger(__name__)


class WebhookTrigger(ITrigger):
    """Webhook trigger that fires when an HTTP request hits the webhook path.

    The actual HTTP endpoint is registered by TriggerEngine in the
    FastAPI app. When a request arrives, the engine calls `handle_request()`
    on the matching WebhookTrigger.
    """

    def __init__(self, config: TriggerConfig, callback: TriggerCallback) -> None:
        super().__init__(config, callback)
        self._registered = False

    async def start(self) -> None:
        webhook_path = self._config.webhook_path
        if not webhook_path:
            logger.error("WebhookTrigger %s: no webhook_path configured", self.trigger_id)
            return
        self._registered = True
        logger.info("WebhookTrigger %s registered at path '%s'", self.trigger_id, webhook_path)

    async def stop(self) -> None:
        self._registered = False
        logger.info("WebhookTrigger %s unregistered", self.trigger_id)

    def is_running(self) -> bool:
        return self._registered

    async def handle_request(self, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """Handle an incoming webhook request.

        Args:
            body: Raw request body.
            headers: Request headers.

        Returns:
            Response dict to send back to the caller.
        """
        # Verify signature if secret is configured
        if not self._config.webhook_secret:
            logger.warning("WebhookTrigger %s: no webhook_secret configured, signature verification DISABLED (security risk)", self.trigger_id)
        if self._config.webhook_secret:
            signature = headers.get("x-hub-signature-256", "") or headers.get("x-signature", "")
            if not self._verify_signature(body, self._config.webhook_secret, signature):
                logger.warning("WebhookTrigger %s: signature verification failed", self.trigger_id)
                return {"error": "Invalid signature", "status": 401}

        # Build prompt from webhook payload
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"raw_body": body.decode("utf-8", errors="replace")}

        # Augment prompt with webhook data
        prompt = self._config.trigger_prompt
        if payload:
            prompt += f"\n\nWebhook 数据:\n```json\n{json.dumps(payload, indent=2, ensure_ascii=False)[:2000]}\n```"

        # Fire the trigger
        await self.fire(prompt)
        logger.info("WebhookTrigger %s fired from webhook request", self.trigger_id)

        return {"status": "ok", "trigger_id": self.trigger_id}

    @staticmethod
    def _verify_signature(body: bytes, secret: str, signature: str) -> bool:
        """Verify HMAC-SHA256 signature."""
        if not signature:
            return False
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
