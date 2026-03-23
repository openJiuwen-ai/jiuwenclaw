# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""XiaoyiChannel - 华为小艺 A2A 协议客户端."""

from __future__ import annotations

import asyncio
import base64
import hmac
import hashlib
import inspect
import json
import os
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import aiohttp

from jiuwenclaw.utils import logger
from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import EventType, Message, ReqMethod
from jiuwenclaw.channel.xiaoyi_utils.push import XiaoYiPushService, PushConfig


@dataclass
class XiaoyiChannelConfig:
    """小艺通道配置（客户端模式）."""

    enabled: bool = False
    mode: str = "xiaoyi_channel"  # xiaoyi_channel or xiaoyi_claw
    ak: str = ""
    sk: str = ""
    agent_id: str = ""
    ws_url1: str = ""
    ws_url2: str = ""
    enable_streaming: bool = True
    # Push notification configuration
    uid: str = ""
    api_key: str = ""
    api_id: str = ""
    push_id: str = ""
    push_url: str = ""
    file_upload_url: str = ""
    # Task timeout in milliseconds (default: 1 hour)
    task_timeout_ms: int = 3600000
    # Session cleanup timeout in milliseconds (default: 1 hour)
    session_cleanup_timeout_ms: int = 3600000


def _generate_signature(sk: str, timestamp: str) -> str:
    """生成 HMAC-SHA256 签名（Base64 编码）."""
    h = hmac.new(
        sk.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(h.digest()).decode("utf-8")


class XYFileUploadService:
    def __init__(self, base_url: str, api_key: str, uid: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.uid = uid
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.close()
    
    async def upload_file(self, file_path: str, object_type: str = "TEMPORARY_MATERIAL_DOC") -> Optional[str]:
        try:
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            file_name = os.path.basename(file_path)
            file_size = len(file_content)
            file_sha256 = hashlib.sha256(file_content).hexdigest()
            
            prepare_url = f"{self.base_url}/osms/v1/file/manager/prepare"
            prepare_data = {
                "objectType": object_type,
                "fileName": file_name,
                "fileSha256": file_sha256,
                "fileSize": file_size,
                "fileOwnerInfo": {
                    "uid": self.uid,
                    "teamId": self.uid,
                },
                "useEdge": True,
            }
            
            headers = {
                "Content-Type": "application/json",
                "x-uid": self.uid,
                "x-api-key": self.api_key,
                "x-request-from": "openclaw",
            }
            
            async with self.session.post(prepare_url, json=prepare_data, headers=headers) as resp:
                if not resp.ok:
                    raise Exception(f"Prepare failed: HTTP {resp.status}")
                
                prepare_resp = await resp.json()
                if prepare_resp.get("code") != "0":
                    raise Exception(f"Prepare failed: {prepare_resp.get('desc', 'Unknown error')}")
            
            object_id = prepare_resp.get("objectId")
            draft_id = prepare_resp.get("draftId")
            upload_infos = prepare_resp.get("uploadInfos", [])
            
            if not upload_infos:
                raise Exception("No upload information returned")
            
            upload_info = upload_infos[0]
            upload_url = upload_info.get("url")
            upload_method = upload_info.get("method", "PUT")
            upload_headers = upload_info.get("headers", {})
            
            async with self.session.request(
                upload_method, 
                upload_url, 
                data=file_content, 
                headers=upload_headers
            ) as resp:
                if not resp.ok:
                    raise Exception(f"Upload failed: HTTP {resp.status}")
            
            complete_url = f"{self.base_url}/osms/v1/file/manager/complete"
            complete_data = {
                "objectId": object_id,
                "draftId": draft_id,
            }
            
            async with self.session.post(complete_url, json=complete_data, headers=headers) as resp:
                if not resp.ok:
                    raise Exception(f"Complete failed: HTTP {resp.status}")
                
                complete_resp = await resp.json()
                if complete_resp.get("code") != "0":
                    raise Exception(f"Complete failed: {complete_resp.get('desc', 'Unknown error')}")
            
            return object_id
            
        except Exception as e:
            logger.error(f"[XY File Upload] Error: {e}")
            return None


def _generate_auth_headers(config: XiaoyiChannelConfig) -> dict[str, str]:
    """生成鉴权 Header."""
    if config.mode == "xiaoyi_claw":
        return {
            "x-uid": config.uid,
            "x-api-key": config.api_key,
            "x-agent-id": config.agent_id,
            "x-request-from": "openclaw"
        }
    timestamp = str(int(time.time() * 1000))
    signature = _generate_signature(config.sk, timestamp)
    return {
        "x-access-key": config.ak,
        "x-sign": signature,
        "x-ts": timestamp,
        "x-agent-id": config.agent_id
    }


class XiaoyiChannel(BaseChannel):
    """小艺通道：作为客户端连接到小艺服务器，实现 A2A 协议."""

    name = "xiaoyi"
    _TASK_KEEPALIVE_INTERVAL_SECONDS = 8.0

    def __init__(self, config: XiaoyiChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: XiaoyiChannelConfig = config
        self._ws_connections: dict[str, Any] = {}  # Dual channel connections
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._running = False
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}  # Heartbeat tasks for each channel
        self._connect_tasks: dict[str, asyncio.Task] = {}  # Connection tasks for each channel
        self._session_task_map: dict[str, str] = {}
        self._session_heartbeat_tasks: dict[str, asyncio.Task] = {}  # Response heartbeat tasks for each session
        self._stream_text_buffers: dict[str, str] = {}
        self._task_keepalive_tasks: dict[str, asyncio.Task] = {}
        self._task_last_activity: dict[str, float] = {}
        self._on_message_cb: Callable[[Message], Any] | None = None
        # Task timeout management
        self._session_active: set[str] = set()  # Active sessions (concurrent request detection)
        self._task_timeout_tasks: dict[str, asyncio.Task] = {}  # 1-hour task timeout tasks
        self._session_timeout_tasks: dict[str, asyncio.Task] = {}  # 60-second periodic timeout tasks
        self._sessions_waiting_for_push: dict[str, str] = {}  # {session: task} waiting for push
        # Session cleanup management
        self._sessions_marked_for_cleanup: dict[str, dict[str, Any]] = {}  # Session cleanup state
        # File upload service configuration
        self.file_upload_config = {
            "baseUrl": config.file_upload_url,
            "apiKey": config.api_key,
            "uid": config.uid,
        }
        # Save additional configuration fields
        self.api_id = config.api_id
        self.push_id = config.push_id
        self._accumulated_texts: dict[str, str] = {}  # Accumulated text per session for push notification

    @property
    def channel_id(self) -> str:
        return self.name

    @property
    def clients(self) -> set[Any]:
        return set()

    def on_message(self, callback: Callable[[Message], None]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        if self._running:
            logger.warning("XiaoyiChannel 已在运行")
            return
        if not self.config.enabled:
            logger.warning("XiaoyiChannel 未启用（enabled=False）")
            return
        if self.config.mode == "xiaoyi_channel":
            if not self.config.ak or not self.config.sk or not self.config.agent_id:
                logger.error("XiaoyiChannel 未配置 ak/sk/agent_id")
                return

        self._running = True
        # Start dual channel connections
        for url_key, url in [("ws_url1", self.config.ws_url1), ("ws_url2", self.config.ws_url2)]:
            if url:
                self._connect_tasks[url_key] = asyncio.create_task(self._reconnect_loop(url_key, url))
        logger.info("XiaoyiChannel 已启动（客户端模式，双通道）")

    async def stop(self) -> None:
        self._running = False
        # Cancel all heartbeat tasks
        for url_key in list(self._heartbeat_tasks.keys()):
            if self._heartbeat_tasks[url_key]:
                self._heartbeat_tasks[url_key].cancel()
                self._heartbeat_tasks[url_key] = None
        # Cancel all connection tasks
        for url_key in list(self._connect_tasks.keys()):
            if self._connect_tasks[url_key]:
                self._connect_tasks[url_key].cancel()
                self._connect_tasks[url_key] = None
        # Cancel all session heartbeat tasks
        for session_id in list(self._session_heartbeat_tasks.keys()):
            if self._session_heartbeat_tasks[session_id]:
                self._session_heartbeat_tasks[session_id].cancel()
                self._session_heartbeat_tasks[session_id] = None
        # Cancel all task timeout tasks
        for session_id in list(self._task_timeout_tasks.keys()):
            if self._task_timeout_tasks[session_id]:
                self._task_timeout_tasks[session_id].cancel()
                self._task_timeout_tasks[session_id] = None
        # Cancel all session timeout tasks
        for session_id in list(self._session_timeout_tasks.keys()):
            if self._session_timeout_tasks[session_id]:
                self._session_timeout_tasks[session_id].cancel()
                self._session_timeout_tasks[session_id] = None
        # Close all websocket connections
        for url_key, ws in list(self._ws_connections.items()):
            if ws:
                try:
                    await ws.close()
                except Exception as e:
                    logger.warning(f"关闭 WebSocket 连接失败 ({url_key}): {e}")
                self._ws_connections[url_key] = None
        self._heartbeat_tasks.clear()
        self._connect_tasks.clear()
        self._session_heartbeat_tasks.clear()
        self._task_timeout_tasks.clear()
        self._session_timeout_tasks.clear()
        self._ws_connections.clear()
        self._session_active.clear()
        self._sessions_waiting_for_push.clear()
        self._sessions_marked_for_cleanup.clear()
        self._accumulated_texts.clear()
        logger.info("XiaoyiChannel 已停止")

    def _extract_platform_receive_info(self, msg: Message) -> tuple[str, str]:
        """
        从消息中提取小艺平台会话 ID 与任务 ID。
        优先使用 metadata（避免 \new_session 覆盖 session_id 后无法回发），否则回退到 session_id 与 _session_task_map。
        """
        meta = getattr(msg, "metadata", None) or {}
        platform_session_id = (meta.get("xiaoyi_session_id") or "").strip()
        platform_task_id = (meta.get("xiaoyi_task_id") or "").strip()
        if platform_session_id or platform_task_id:
            return (
                platform_session_id or (msg.session_id or ""),
                platform_task_id or platform_session_id,
            )
        session_id = msg.session_id or ""
        task_id = self._session_task_map.get(session_id, session_id)
        return session_id, task_id

    async def send(self, msg: Message) -> None:
        """发送消息到小艺服务端（A2A 格式，双通道发送）."""
        if not self._ws_connections:
            return
        logger.info(f"XiaoyiChannel 发送消息: {msg}")
        session_id, task_id = self._extract_platform_receive_info(msg)
        # Handle chat.file event
        if self.config.mode == "xiaoyi_claw" and msg.event_type == EventType.CHAT_FILE:
            files = msg.payload.get("files", []) if isinstance(msg.payload, dict) else []
            if files:
                for file_info in files:
                    # Convert file path to file info dict if it's a string
                    if isinstance(file_info, str):
                        file_info = {
                            "success": True,
                            "result_type": "file_created",
                            "fullPath": file_info,
                            "path": os.path.basename(file_info),
                            "fileName": os.path.basename(file_info)
                        }
                    
                    # Send file response
                    for url_key, ws in self._ws_connections.items():
                        if ws:
                            try:
                                await self._send_file_response(session_id, task_id, file_info, url_key)
                            except Exception as e:
                                logger.warning(f"XiaoyiChannel 发送文件响应失败 ({url_key}): {e}")
            return

        content = ""
        cron_job_name = ""
        if isinstance(msg.payload, dict):
            content = msg.payload.get("content", "\n")
            if isinstance(content, dict):
                content = content.get("output", str(content))
            content = str(content)
            cron_job_name = msg.payload.get("cron", {}).get("job_name", "")
        elif msg.payload:
            content = str(msg.payload)
        
        # 推送消息发送
        if msg.id.startswith("cron-push"):
            await self._send_push_notification(cron_job_name, content)
            return

        # 如果禁用流式，总是作为完整消息发送
        if not self.config.enable_streaming:
            append = False
            last_chunk = True
            final = True
        else:
            # 流式模式 - 类似 TS 版本的实现
            # 判断事件类型
            is_delta = msg.event_type == EventType.CHAT_DELTA
            last_chunk = msg.event_type == EventType.CHAT_FINAL
            is_final = msg.payload.get("is_complete", False)
            last_chunk = True if is_final else last_chunk

            # 获取之前发送的文本
            previous_text = self._accumulated_texts.get(session_id, "")

            # 累积当前文本
            self._accumulated_texts[session_id] = content

            # 计算增量文本
            if is_delta:
                incremental_text = content[len(previous_text):]
            else:
                incremental_text = content

            # 在消息流中，总是使用 append=true, isFinal=false
            append = True
            final = False
            last_chunk = last_chunk
            final = is_final

        # Get accumulated text for this session (for push notification)
        accumulated_text = self._accumulated_texts.get(session_id, "")
        self._accumulated_texts[session_id] = content

        # Send to all active connections
        for url_key, ws in self._ws_connections.items():
            if ws:
                try:
                    await self._send_text_response(
                        session_id,
                        task_id,
                        content,
                        url_key,
                        append=append,
                        last_chunk=last_chunk,
                        is_final=final
                    )
                except Exception as e:
                    logger.warning(f"XiaoyiChannel 发送消息失败 ({url_key}): {e}")

        if final and session_id:
            await self._stop_session_heartbeat(session_id)
            # Clean up tasks and mark session as completed
            self._clear_task_timeout(session_id)
            self._clear_session_timeout(session_id)
            self._mark_session_completed(session_id)

            # Check if session was waiting for push and send notification
            if self._is_session_waiting_for_push(session_id, task_id) and accumulated_text:
                summary = accumulated_text[:30] + "..." if len(accumulated_text) > 30 else accumulated_text
                await self._send_push_notification(summary, "后台任务已完成：" + summary)
                self._clear_session_waiting_for_push(session_id, task_id)

            # Clear accumulated text
            self._accumulated_texts.pop(session_id, None)

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={
                "mode": "client",
                "ws_url1": self.config.ws_url1,
                "ws_url2": self.config.ws_url2,
                "agent_id": self.config.agent_id,
            },
        )

    async def _reconnect_loop(self, url_key: str, url: str) -> None:
        """自动重连循环（双通道）."""
        while self._running:
            try:
                await self._connect(url_key, url)
                if not self._running:
                    break
                # 连接被远端正常关闭时也做退避，避免瞬时重连刷屏。
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"XiaoyiChannel 连接失败 ({url}): {e}")
                await asyncio.sleep(5)

    async def _connect(self, url_key: str, url: str) -> None:
        """连接到小艺服务器（双通道）."""
        import websockets

        headers = _generate_auth_headers(self.config)
        parsed = urlparse(url)
        is_ip = bool(parsed.hostname and parsed.hostname.replace(".", "").isdigit())

        ssl_context = ssl.create_default_context()
        if is_ip:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(
            url,
            additional_headers=headers,
            ssl=ssl_context,
            ping_interval=15,
            ping_timeout=15,
            close_timeout=5,
        ) as ws:
            self._ws_connections[url_key] = ws
            self._send_locks[url_key] = asyncio.Lock()
            logger.info(f"XiaoyiChannel 已连接 {url_key}: {url}")

            # 发送初始化消息（必须在 heartbeat 之前）
            await self._send_init_message(url_key)

            # 启动心跳
            self._heartbeat_tasks[url_key] = asyncio.create_task(self._heartbeat_loop(url_key))

            try:
                async for raw in ws:
                    await self._handle_raw_message(raw)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 连接异常 ({url_key}): {e}")
            finally:
                if self._heartbeat_tasks.get(url_key):
                    self._heartbeat_tasks[url_key].cancel()
                    self._heartbeat_tasks[url_key] = None
                self._ws_connections[url_key] = None
                self._send_locks.pop(url_key, None)
                close_code = getattr(ws, "close_code", None)
                close_reason = getattr(ws, "close_reason", None)
                logger.info(
                    f"XiaoyiChannel 连接关闭 {url_key}: {url} (code={close_code}, reason={close_reason})"
                )
    async def _send_init_message(self, url_key: str) -> None:
        """发送初始化消息 (clawd_bot_init) 到指定通道."""
        ws = self._ws_connections.get(url_key)
        if not ws:
            return
        init_message = {
            "msgType": "clawd_bot_init",
            "agentId": self.config.agent_id,
        }
        try:
            await self._safe_ws_send(url_key, init_message)
            logger.info(f"XiaoyiChannel 已发送初始化消息 ({url_key})")
        except Exception as e:
            logger.warning(f"XiaoyiChannel 发送初始化消息失败 ({url_key}): {e}")
            raise

    async def _heartbeat_loop(self, url_key: str) -> None:
        """应用层心跳循环（20秒间隔）."""
        while self._running and self._ws_connections.get(url_key):
            try:
                heartbeat = {"msgType": "heartbeat", "agentId": self.config.agent_id}
                await self._safe_ws_send(url_key, heartbeat)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 心跳发送失败 ({url_key}): {e}")
                ws = self._ws_connections.get(url_key)
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                break
            await asyncio.sleep(20)

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        """处理接收到的原始消息，转换为 JiuwenClaw 内部格式."""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("XiaoyiChannel JSON 解析失败")
            return

        msg_type = message.get("msgType")
        if msg_type == "heartbeat":
            return

        method = message.get("method")
        if method == "message/stream":
            await self._handle_message_stream(message)
        elif method == "clearContext":
            await self._handle_clear_context(message)
        elif method == "tasks/cancel":
            await self._handle_tasks_cancel(message)
        else:
            logger.warning(f"XiaoyiChannel 未知方法: {method}")

    async def _handle_message_stream(self, message: dict[str, Any]) -> None:
        """处理 message/stream 消息，转换为 JiuwenClaw Message."""
        session_id = message.get("sessionId") or message.get("params", {}).get("sessionId", "")
        task_id = message.get("params", {}).get("id",) or ""
        user_message = message.get("params", {}).get("message", {})
        parts = user_message.get("parts", [])

        # Mark session as active
        self._mark_session_active(session_id)
        self._session_task_map[session_id] = task_id

        # ==================== PROCESS PARTS (TEXT & FILES) ====================
        text = ""
        file_attachments: list[str] = []
        media_files: list[dict[str, Any]] = []

        for part in parts:
            kind = part.get("kind")
            if kind == "text" and part.get("text"):
                text += part.get("text", "")
            elif kind == "file" and part.get("file"):
                file_info = part["file"]
                uri = file_info.get("uri")
                mime_type = file_info.get("mimeType", "")
                name = file_info.get("name", "")

                if not uri:
                    logger.warning(f"XiaoYi: File part without URI, skipping: {name}")
                    continue

                try:
                    media_files.append({"uri": uri, "mime_type": mime_type, "name": name})

                    # For text-based files, extract content inline
                    from jiuwenclaw.channel.xiaoyi_utils.media import is_text_mime_type, extract_text_from_url
                    if is_text_mime_type(mime_type):
                        try:
                            text_content = await extract_text_from_url(uri, 5_000_000, 30_000)
                            text += f"\n\n[文件内容: {name}]\n{text_content}"
                            file_attachments.append(f"[文件: {name}]")
                            logger.info(f"XiaoYi: Successfully extracted text from: {name}")
                        except Exception:
                            logger.warning(f"XiaoYi: Text extraction failed for {name}, will download as binary")
                            file_attachments.append(f"[文件: {name}]")
                    else:
                        file_attachments.append(f"[文件: {name}]")
                except Exception as e:
                    logger.error(f"XiaoYi: Failed to process file {name}: {e}")
                    file_attachments.append(f"[文件处理失败: {name}]")
            elif kind == "data":
                data = part.get("data", {})
                if isinstance(data, dict):
                    push_id = data.get("variables", {}).get("systemVariables", {}).get("push_id", "")
                    self.config.push_id = push_id if push_id else self.config.push_id
        # =================================================================

        # Log summary of processed attachments
        if file_attachments:
            logger.info(f"XiaoYi: Processed {len(file_attachments)} file(s): {', '.join(file_attachments)}")

        # ==================== DOWNLOAD AND SAVE MEDIA FILES ====================
        media_payload: dict[str, Any] = {}
        if media_files:
            logger.info(f"XiaoYi: Downloading {len(media_files)} media file(s)...")
            from jiuwenclaw.channel.xiaoyi_utils.media import (
                MediaFile,
                MediaDownloadOptions,
                download_and_save_media_list,
                build_xiaoyi_media_payload,
            )
            files_to_download = [
                MediaFile(uri=f["uri"], mime_type=f["mime_type"], name=f["name"])
                for f in media_files
            ]
            options = MediaDownloadOptions(max_bytes=30_000_000, timeout_ms=60_000)
            downloaded_media = await download_and_save_media_list(files_to_download, options)
            logger.info(f"XiaoYi: Successfully downloaded {len(downloaded_media)}/{len(media_files)} file(s)")
            media_payload = build_xiaoyi_media_payload(downloaded_media)
        # =================================================================

        # 将最近一次可回发的小艺身份写入 config.yaml，供 cron 推送时使用
        try:
            from jiuwenclaw.config import update_channel_in_config

            update_channel_in_config(
                "xiaoyi",
                {
                    "last_session_id": session_id or "",
                    "last_task_id": task_id or "",
                },
            )
        except Exception:
            pass

        # ==================== BUILD MESSAGE AND ROUTE ====================
        # 平台身份写入 metadata，供回发时使用（与 session_id 解耦，\new_session 后仍可正确回发）
        metadata = {
            "method": "message/stream",
            "xiaoyi_session_id": session_id,
            "xiaoyi_task_id": task_id,
        }
        # Add media payload to metadata
        params = {"query": text, "task_id": task_id}
        if media_payload:
            params["files"] = media_payload

        user_message = Message(
            id=message.get("id", ""),
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params=params,
            timestamp=time.time(),
            is_stream=self.config.enable_streaming,
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            metadata=metadata,
        )

        # ==================== START TASK TIMEOUT PROTECTION ====================
        # Start 1-hour task timeout timer
        task_timeout_ms = self.config.task_timeout_ms
        logger.info(f"[TASK TIMEOUT] Starting {task_timeout_ms}ms task timeout protection for session {session_id}")

        async def task_timeout_handler():
            """1-hour task timeout handler."""
            try:
                await asyncio.sleep(task_timeout_ms / 1000)
                logger.info(f"[TASK TIMEOUT] 1-hour timeout triggered for session {session_id}")
                # Send default message with is_final=true
                for url_key in list(self._ws_connections.keys()):
                    await self._send_text_response(session_id, task_id, "任务还在处理中，完成后将提醒您~", url_key, is_final=True)
                # Mark session as waiting for push state
                self._mark_session_waiting_for_push(session_id, task_id)
            except asyncio.CancelledError:
                pass

        self._task_timeout_tasks[session_id] = asyncio.create_task(task_timeout_handler())

        # Start 60-second periodic timeout for status updates
        async def periodic_timeout_handler():
            """60-second periodic timeout for status updates."""
            try:
                while session_id in self._session_active:
                    await asyncio.sleep(60)
                    # Skip if already waiting for push (1-hour timeout triggered)
                    if self._is_session_waiting_for_push(session_id, task_id):
                        break
                    # Send status update
                    await self._send_status_update(task_id, session_id, "任务正在处理中，请稍后")
            except asyncio.CancelledError:
                pass

        self._session_timeout_tasks[session_id] = asyncio.create_task(periodic_timeout_handler())
        # =================================================================

        handled = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            handled = bool(result)

        if not handled:
            await self.bus.route_user_message(user_message)

        # Start session heartbeat to prevent xiaoyi client timeout
        if not self.config.enable_streaming and session_id:
            await self._start_session_heartbeat(session_id, task_id)

    async def _start_session_heartbeat(self, session_id: str, task_id: str) -> None:
        """启动会话心跳任务，每隔5秒发送空消息直到final消息发出."""
        await self._stop_session_heartbeat(session_id)

        async def heartbeat_loop():
            try:
                while self._running:
                    await asyncio.sleep(5)
                    # Send empty heartbeat message (non-final)
                    for url_key, ws in self._ws_connections.items():
                        if ws:
                            try:
                                await self._send_text_response(
                                    session_id,
                                    task_id,
                                    "",
                                    url_key,
                                    append=True,
                                    is_final=False,
                                )
                            except Exception as e:
                                logger.warning(f"XiaoyiChannel 发送心跳消息失败 ({url_key}): {e}")
            except asyncio.CancelledError:
                logger.info(f"XiaoyiChannel 会话心跳已停止: {session_id}")
            except Exception as e:
                logger.warning(f"XiaoyiChannel 会话心跳异常 ({session_id}): {e}")

        self._session_heartbeat_tasks[session_id] = asyncio.create_task(heartbeat_loop())
        logger.info(f"XiaoyiChannel 会话心跳已启动: {session_id}")

    async def _stop_session_heartbeat(self, session_id: str) -> None:
        """停止会话心跳任务."""
        if session_id in self._session_heartbeat_tasks:
            task = self._session_heartbeat_tasks[session_id]
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._session_heartbeat_tasks.pop(session_id, None)
            logger.info(f"XiaoyiChannel 会话心跳已停止: {session_id}")

    async def _send_status_update(self, task_id: str, session_id: str, message: str) -> None:
        """发送状态更新消息（A2A 格式）."""
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "status-update",
                "final": False,
                "status": {
                    "message": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": message}],
                    },
                    "state": "working",
                },
            },
        }
        # Send to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, task_id, response, url_key)

    def _is_session_active(self, session_id: str) -> bool:
        """检查会话是否有活跃任务."""
        return session_id in self._session_active

    def _mark_session_active(self, session_id: str) -> None:
        """标记会话为活跃状态."""
        self._session_active.add(session_id)

    def _mark_session_completed(self, session_id: str) -> None:
        """标记会话已完成."""
        self._session_active.discard(session_id)

    def _is_session_waiting_for_push(self, session_id: str, task_id: str) -> bool:
        """检查会话是否正在等待推送."""
        return self._sessions_waiting_for_push.get(session_id) == task_id

    def _mark_session_waiting_for_push(self, session_id: str, task_id: str) -> None:
        """标记会话正在等待推送."""
        self._sessions_waiting_for_push[session_id] = task_id

    def _clear_session_waiting_for_push(self, session_id: str, task_id: str) -> None:
        """清除会话的推送等待状态."""
        if self._sessions_waiting_for_push.get(session_id) == task_id:
            self._sessions_waiting_for_push.pop(session_id, None)

    def _is_session_pending_cleanup(self, session_id: str) -> bool:
        """检查会话是否待清理."""
        return session_id in self._sessions_marked_for_cleanup

    def _mark_session_for_cleanup(self, session_id: str, reason: str = "unknown") -> None:
        """标记会话待清理."""
        self._sessions_marked_for_cleanup[session_id] = {
            "reason": reason,
            "marked_at": time.time(),
        }

    def _force_cleanup_session(self, session_id: str) -> None:
        """强制清理会话."""
        self._sessions_marked_for_cleanup.pop(session_id, None)
        self._session_task_map.pop(session_id, None)

    async def _handle_clear_context(self, message: dict[str, Any]) -> None:
        """处理清空上下文请求."""
        session_id = message.get("sessionId", "")
        logger.info(f"XiaoyiChannel 清空上下文: {session_id}")

        # Check if there's an active task for this session
        if self._is_session_active(session_id):
            logger.info(f"[CLEAR] Active task exists for session {session_id}, will continue in background")
            # Mark session for cleanup (delayed cleanup)
            self._mark_session_for_cleanup(session_id, "user_cleared")
        else:
            logger.info(f"[CLEAR] No active task for session {session_id}, clean up immediately")
            self._force_cleanup_session(session_id)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"status": {"state": "cleared"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, session_id, response, url_key)

    async def _handle_tasks_cancel(self, message: dict[str, Any]) -> None:
        """处理取消任务请求."""
        session_id = message.get("sessionId", "")
        task_id = message.get("params", {}).get("id") or message.get("taskId", "")
        logger.info(f"XiaoyiChannel 取消任务: {session_id} {task_id}")
        await self._stop_task_keepalive(self._make_task_key(session_id, task_id))
        if session_id:
            await self._stop_session_heartbeat(session_id)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"id": message.get("id", ""), "status": {"state": "canceled"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, task_id, response, url_key)

        # 清理超时任务和推送状态
        self._clear_task_timeout(session_id)
        self._clear_session_timeout(session_id)
        self._clear_session_waiting_for_push(session_id, task_id)
        self._mark_session_completed(session_id)

    async def _send_text_response(
        self,
        session_id: str,
        task_id: str,
        text: str,
        url_key: str,
        *,
        append: bool = False,
        last_chunk: bool = True,
        is_final: bool = True,
    ) -> None:
        """发送文本响应（A2A 格式）到指定通道."""
        if last_chunk:
            data = {"kind": "text", "text": text}
        else:
            data = {"kind": "reasoningText", "reasoningText": text}
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "artifact-update",
                "append": append,
                "lastChunk": last_chunk,
                "final": is_final,
                "artifact": {
                    "artifactId": f"artifact_{int(time.time() * 1000)}",
                    "parts": [data],
                },
            },
        }
        await self._send_agent_response(session_id, task_id, response, url_key)

    async def _send_agent_response(self, session_id: str, task_id: str, response: dict[str, Any], url_key: str) -> None:
        """发送 agent_response 包装的消息（A2A 格式）到指定通道."""
        wrapper = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(response),
        }
        try:
            await self._safe_ws_send(url_key, wrapper)
        except Exception as e:
            logger.warning(f"XiaoyiChannel 发送响应失败 ({url_key}): {e}")

    async def _send_file_response_base64(self, session_id: str, task_id: str, file_info: dict, url_key: str) -> None:
        """发送文件响应（Base64 格式）到指定通道."""
        try:
            file_name = file_info.get("fileName", os.path.basename(file_info.get("path", "file.txt")))
            file_path = file_info.get("fullPath", file_info.get("path", file_name))
            
            # Check if file exists
            if not os.path.exists(file_path):
                logger.error(f"XiaoyiChannel 文件不存在: {file_path}")
                return
            
            # Check file size (limit to 20MB for Base64)
            file_size = os.path.getsize(file_path)
            if file_size > 20 * 1024 * 1024:  # 20MB limit
                logger.error(f"XiaoyiChannel 文件过大，使用OSMS上传: {file_path}")
                # Use OSMS upload for large files
            base_url = self.file_upload_config.get("baseUrl")
            api_key = self.file_upload_config.get("apiKey")
            uid = self.file_upload_config.get("uid")
            
            if not all([base_url, api_key, uid]):
                logger.error("XiaoyiChannel OSMS配置不完整，无法上传大文件")
                return
            
            object_id = ""
            async with XYFileUploadService(base_url, api_key, uid) as upload_service:
                object_id = await upload_service.upload_file(file_path)
                logger.info(f"file upload success: {object_id}")
                if object_id:
                    # Send file reference response
                    payload = {
                        "jsonrpc": "2.0",
                        "id": f"msg_{int(time.time() * 1000)}",
                        "result": {
                            "taskId": task_id,
                            "kind": "artifact-update",
                            "append": True,
                            "lastChunk": False,
                            "isFinal": False,
                            "artifact": {
                                "artifactId": task_id,
                                "parts": [
                                    {
                                        "kind": "file",
                                        "file": {
                                            "fieldId": object_id,
                                            "name": file_name,
                                            "mimeType": file_name.split(".")[-1]
                                        }
                                    }
                                ],
                            },
                        },
                    }
                    response = {
                        "msgType": "agent_response",
                        "agentId": self.config.agent_id,
                        "session_id": session_id,
                        "task_id": task_id,
                        "msgDetail": json.dumps(payload)
                        }
                    await self._safe_ws_send(url_key, response)
            return object_id
        except Exception as e:
            logger.error(f"XiaoyiChannel 发送文件响应失败: {e}")

    async def _send_file_response(self, session_id: str, task_id: str, file_info: dict, url_key: str) -> None:
        """发送文件响应到指定通道."""
        try:            
            # If file is available locally, send as Base64
            if file_info.get("fullPath") or file_info.get("path"):
                await self._send_file_response_base64(session_id, task_id, file_info, url_key)
                return
        except Exception as e:
            logger.error(f"XiaoyiChannel 发送文件响应失败: {e}")

    async def _safe_ws_send(self, url_key: str, payload: dict[str, Any]) -> None:
        ws = self._ws_connections.get(url_key)
        if not ws:
            raise RuntimeError(f"ws connection not available: {url_key}")
        lock = self._send_locks.get(url_key)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[url_key] = lock
        data = json.dumps(payload, ensure_ascii=False)
        async with lock:
            await ws.send(data)

    def _clear_task_timeout(self, session_id: str) -> None:
        """清除任务超时任务."""
        if session_id in self._task_timeout_tasks:
            task = self._task_timeout_tasks[session_id]
            if task and not task.done():
                task.cancel()
            self._task_timeout_tasks.pop(session_id, None)

    def _clear_session_timeout(self, session_id: str) -> None:
        """清除会话超时任务."""
        if session_id in self._session_timeout_tasks:
            task = self._session_timeout_tasks[session_id]
            if task and not task.done():
                task.cancel()
            self._session_timeout_tasks.pop(session_id, None)

    async def _send_push_notification(self, text: str, push_text: str) -> bool:
        """发送推送通知."""
        if not (self.config.api_id):
            logger.info("[PUSH] Push not configured, skipping")
            return False

        try:
            push_config = PushConfig(
                mode=self.config.mode,
                api_id=self.config.api_id,
                push_id=self.config.push_id,
                push_url=self.config.push_url,
                ak=self.config.ak,
                sk=self.config.sk,
                uid=self.config.uid,
                api_key=self.config.api_key
            )
            push_service = XiaoYiPushService(push_config)
            result = await push_service.send_push(text, push_text)
            logger.info(f"[PUSH] Push notification sent: {result}")
            return result
        except Exception as e:
            logger.error(f"[PUSH] Error sending push: {e}")
            return False
