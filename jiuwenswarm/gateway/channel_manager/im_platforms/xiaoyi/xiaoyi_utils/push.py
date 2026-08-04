# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""XiaoYi Push Message Service - 主动推送消息服务."""

import logging
import base64
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp


logger = logging.getLogger(__name__)

PUSH_URL = "https://hag.cloud.huawei.com/open-ability-agent/v1/agent-webhook"


@dataclass
class PushConfig:
    """Push 消息配置."""
    mode: str = ""
    api_id: str = ""
    push_id: str = ""
    ak: str = ""
    sk: str = ""
    uid: str = ""
    api_key: str = ""
    push_url: str = ""


class XiaoYiPushService:
    """
    华为小艺主动推送服务.
    通过 HTTP Webhook API 向用户设备发送推送通知.
    """
    def __init__(self, config: PushConfig):
        self.config = config

    @staticmethod
    def _generate_uuid() -> str:
        """生成 UUID."""
        return str(uuid.uuid4())

    def _generate_signature(self, timestamp: str) -> str:
        """生成 HMAC-SHA256 签名 (Base64 编码)."""
        h = hmac.new(
            self.config.sk.encode("utf-8"),
            timestamp.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(h.digest()).decode("utf-8")

    async def send_push(
        self,
        text: str,
        push_text: str,
        session_id: str | None = None,
        push_data_id: str | None = None,
        push_id: str | None = None,
        cron_job_id: str | None = None,
        cron_title: str | None = None,
    ) -> bool:
        """
        发送推送通知.

        Args:
            text: 摘要文本 (如前30个字符)，对应协议字段 ``pushText``.
            push_text: 推送通知正文，当 ``push_data_id`` 为空时通过
                ``kind="text"`` part 下发。
            session_id: 可选 sessionId（随 ``kind="task"`` 下发）。
            push_data_id: 可选 pushDataId；提供时改用 ``kind="data"`` part，
                仅下发 ``pushDataId`` / ``cronId`` / ``cronTitle``，不再下发正文。
            push_id: 可选 pushId；未提供时回退到 ``self.config.push_id``。
            cron_job_id: 可选 cron 任务 jobId（线上字段名 ``cronId``，随
                ``kind="data"`` 下发，客户端据此识别 push 来源）。
            cron_title: 可选 cron 任务标题（随 ``kind="data"`` 下发）。

        Returns:
            bool: 是否发送成功
        """

        try:
            timestamp = str(int(time.time() * 1000))
            message_id = self._generate_uuid()
            actual_push_id = push_id or self.config.push_id

            logger.info(
                "[PUSH] Preparing to send push message with pushId: %s...",
                actual_push_id[:20],
            )
            if cron_job_id or cron_title:
                logger.info(
                    "[PUSH] Cron push: pushDataId=%s cronId=%s cronTitle=%s",
                    push_data_id or "-",
                    cron_job_id or "-",
                    cron_title or "-",
                )

            # 当提供 push_data_id 时，parts 改为 kind="data"，仅下发 pushDataId
            # 及可选 cronId/cronTitle；否则维持原 kind="text" 正文下发。
            if push_data_id:
                parts = [{
                    "kind": "data",
                    "data": {
                        "pushDataId": push_data_id,
                        **({"cronId": cron_job_id} if cron_job_id else {}),
                        **({"cronTitle": cron_title} if cron_title else {}),
                    },
                }]
            else:
                parts = [{
                    "kind": "text",
                    "text": push_text,
                }]

            result_block: dict[str, Any] = {
                "id": self._generate_uuid(),
                "apiId": self.config.api_id,
                "pushId": actual_push_id,
                "pushText": text,
                "kind": "task",
                "artifacts": [{
                    "artifactId": self._generate_uuid(),
                    "parts": parts,
                }],
            }
            if session_id:
                result_block["sessionId"] = session_id
            # 维持与历史调用方一致的状态字段（仅 kind="text" 路径历史携带）。
            if not push_data_id:
                result_block["status"] = {"state": "completed"}

            payload = {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": result_block,
            }

            logger.info(f"[PUSH] Sending push notification: {push_text}")
            if self.config.mode == "xiaoyi_claw":
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-hag-trace-id": self._generate_uuid(),
                    "x-uid": self.config.uid,
                    "x-api-key": self.config.api_key,
                    "x-request-from": "openclaw"
                } 
            else:
                signature = self._generate_signature(timestamp)
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-hag-trace-id": self._generate_uuid(),
                    "X-Access-Key": self.config.ak,
                    "X-Sign": signature,
                    "X-Ts": timestamp,
                }
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.push_url or PUSH_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                ) as response:
                    if response.status == 200:
                        logger.info("[PUSH] Push notification sent successfully")
                        return True
                    else:
                        logger.error(f"[PUSH] Failed: HTTP {response.status}")
                        return False

        except aiohttp.ClientError as e:
            logger.error(f"[PUSH] Network error: {e}")
            return False
        except Exception as e:
            logger.error(f"[PUSH] Error: {e}")
            return False

    async def send_push_with_directives(
        self,
        push_id: str,
        session_id: str,
        directives: list[dict[str, Any]],
    ) -> bool:
        try:
            timestamp = str(int(time.time() * 1000))
            payload = {
                "jsonrpc": "2.0",
                "id": self._generate_uuid(),
                "result": {
                    "id": self._generate_uuid(),
                    "apiId": self.config.api_id,
                    "pushId": push_id,
                    "pushText": "",
                    "pushType": 101,
                    "kind": "task",
                    "sessionId": session_id,
                    "artifacts": [
                        {
                            "artifactId": self._generate_uuid(),
                            "parts": [
                                {
                                    "kind": "data",
                                    "data": {"directives": directives},
                                }
                            ],
                        }
                    ],
                },
            }
            if self.config.mode == "xiaoyi_claw":
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-hag-trace-id": self._generate_uuid(),
                    "x-uid": self.config.uid,
                    "x-api-key": self.config.api_key,
                    "x-request-from": "openclaw",
                }
            else:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-hag-trace-id": self._generate_uuid(),
                    "X-Access-Key": self.config.ak,
                    "X-Sign": self._generate_signature(timestamp),
                    "X-Ts": timestamp,
                }

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.push_url or PUSH_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                ) as response:
                    if response.status == 200:
                        logger.info("[PUSH] Directive push sent successfully")
                        return True
                    error_text = await response.text()
                    logger.error(
                        "[PUSH] Directive push failed: HTTP %s body=%s",
                        response.status,
                        error_text,
                    )
                    return False
        except aiohttp.ClientError as exc:
            logger.error("[PUSH] Directive push network error: %s", exc)
            return False
        except Exception as exc:
            logger.error("[PUSH] Directive push error: %s", exc)
            return False


# ─── pushBroadcast：向所有已注册 pushId 广播 ───────────────────


@dataclass
class PushBroadcastResult:
    """pushBroadcast 返回值，统计成功/失败 pushId 数量."""

    success_count: int = 0
    failure_count: int = 0


async def push_broadcast(
    config: PushConfig,
    text: str,
    title: str,
    to: str,
    push_data_id: str,
    cron_job_id: str | None = None,
    cron_title: str | None = None,
) -> PushBroadcastResult:
    """向所有已注册 pushId 广播推送通知（单 pushId 失败不影响其他）.

    对应 xy_channel 的 ``conversation/outbound-gateway.ts`` 里的
    ``pushBroadcast``。

    Args:
        config: 推送配置（api_id / push_id / uid / api_key 等）。
        text: 摘要文本（对应协议字段 ``pushText``）。
        title: 推送通知标题（``kind="text"`` 路径下作为正文下发）。
        to: 目标会话标识（push 服务侧使用，可为空字符串）。
        push_data_id: pushDataId；非空时走 ``kind="data"`` 路径。
        cron_job_id: 可选 cron 任务 jobId（线上字段名 ``cronId``）。
        cron_title: 可选 cron 任务标题。

    Returns:
        PushBroadcastResult: 成功/失败 pushId 计数。
    """
    from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.pushid_manager import (
        get_all_push_ids,
    )

    push_id_list: list[str] = []
    try:
        push_id_list = get_all_push_ids()
    except Exception as exc:
        logger.error("[outbound-gateway] Failed to load pushIds: %s", exc)
    if not push_id_list:
        push_id_list = [str(config.push_id or "")]

    push_service = XiaoYiPushService(config)
    success_count = 0
    failure_count = 0

    for push_id in push_id_list:
        if not push_id:
            continue
        try:
            sent = await push_service.send_push(
                text=text,
                push_text=title,
                session_id=to or None,
                push_data_id=push_data_id or None,
                push_id=push_id,
                cron_job_id=cron_job_id,
                cron_title=cron_title,
            )
            if sent:
                success_count += 1
                logger.info(
                    "[outbound-gateway] Push sent to pushId: %s...",
                    push_id[:20],
                )
            else:
                failure_count += 1
                logger.error(
                    "[outbound-gateway] Failed to send to pushId: %s... (send_push returned False)",
                    push_id[:20],
                )
        except Exception as exc:
            failure_count += 1
            logger.error(
                "[outbound-gateway] Failed to send to pushId: %s... error=%s",
                push_id[:20],
                exc,
            )

    return PushBroadcastResult(
        success_count=success_count,
        failure_count=failure_count,
    )

