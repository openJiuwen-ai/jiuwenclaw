import asyncio
import json
import time
from typing import Any, Callable

from loguru import logger
from pydantic import BaseModel, Field
import httpx


from jiuwenclaw.channel.base import RobotMessageRouter, BaseChannel
from jiuwenclaw.schema.message import Message, ReqMethod


class DingTalkConfig(BaseModel):
    """钉钉通道配置（使用Stream模式）"""
    enabled: bool = False
    client_id: str = ""  # 应用ID
    client_secret: str = ""  # 应用密钥
    allow_from: list[str] = Field(default_factory=list)  # 允许的员工ID


try:
    from dingtalk_stream import (
        DingTalkStreamClient,
        Credential,
        CallbackHandler,
        CallbackMessage,
        AckMessage,
    )
    from dingtalk_stream.chatbot import ChatbotMessage

    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    CallbackHandler = object
    CallbackMessage = None
    AckMessage = None
    ChatbotMessage = None


class DingTalkHandler(CallbackHandler):
    """
    钉钉Stream SDK标准回调处理器。
    解析传入消息并转发到通道。
    """

    def __init__(self, channel: "DingTalkChannel"):
        super().__init__()
        self.channel = channel

    def _extract_message_content(self, chatbot_msg: ChatbotMessage, raw_data: dict) -> str:
        """从消息对象中提取文本内容"""
        content = ""
        if chatbot_msg.text:
            content = chatbot_msg.text.content.strip()
        if not content:
            content = raw_data.get("text", {}).get("content", "").strip()
        return content

    def _extract_sender_info(self, chatbot_msg: ChatbotMessage) -> tuple[str, str]:
        """提取发送者信息"""
        sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id
        sender_name = chatbot_msg.sender_nick or "Unknown"
        return sender_id, sender_name

    def _extract_conversation_info(self, chatbot_msg: ChatbotMessage) -> tuple[str, str]:
        """提取会话信息"""
        conversation_id = chatbot_msg.conversation_id or ""
        conversation_type = chatbot_msg.conversation_type or "1"  # 1: 单聊；2：群聊
        return conversation_id, conversation_type

    def _create_message_task(self, content: str, sender_id: str, sender_name: str,
                              conversation_id: str, conversation_type: str) -> None:
        """创建异步任务处理消息"""
        task = asyncio.create_task(
            self.channel._on_message(content, sender_id, sender_name, conversation_id, conversation_type)
        )
        self.channel._background_tasks.add(task)
        task.add_done_callback(self.channel._background_tasks.discard)

    async def process(self, message: CallbackMessage):
        """处理传入的流消息"""
        try:
            # 使用SDK的ChatbotMessage进行健壮解析
            chatbot_msg = ChatbotMessage.from_dict(message.data)

            # 提取文本内容
            content = self._extract_message_content(chatbot_msg, message.data)

            if not content:
                logger.warning(
                    f"收到空或不支持的消息类型: {chatbot_msg.message_type}"
                )
                return AckMessage.STATUS_OK, "OK"

            # 提取发送者信息
            sender_id, sender_name = self._extract_sender_info(chatbot_msg)

            # 提取会话信息
            conversation_id, conversation_type = self._extract_conversation_info(chatbot_msg)

            logger.info(
                f"收到来自 {sender_name} ({sender_id}) 的钉钉消息: {content} (会话ID: {conversation_id})"
            )

            # 转发到通道（非阻塞）
            self._create_message_task(content, sender_id, sender_name, conversation_id, conversation_type)

            return AckMessage.STATUS_OK, "OK"

        except Exception as e:
            logger.error(f"处理钉钉消息时出错: {e}")
            # 返回OK以避免钉钉服务器重试循环
            return AckMessage.STATUS_OK, "Error"


class DingTalkChannel(BaseChannel):
    """
    使用Stream模式的钉钉通道。

    通过 `dingtalk-stream` SDK 使用 WebSocket 接收事件。
    使用直接 HTTP API 发送消息（SDK主要用于接收）。
    """

    name = "dingtalk"

    def __init__(self, config: DingTalkConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: DingTalkConfig = config
        self._client: Any = None
        self._http: httpx.AsyncClient | None = None

        self._access_token: str | None = None
        self._token_expiry: float = 0
        self._background_tasks: set[asyncio.Task] = set()

        self._gateway_callback: Callable[[Message], None] | None = None

    @property
    def channel_id(self) -> str:
        """返回通道的唯一标识"""
        return self.name

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """注册钉钉通道的回调函数"""
        self._gateway_callback = callback

    async def _handle_message(
            self,
            chat_id: str,
            content: str,
            metadata: dict[str, Any] | None = None
    ) -> None:
        """处理来自钉钉通道的传入消息（符合基类接口）"""
        # 检查发送者权限
        if not self.is_allowed(chat_id):
            logger.warning(f"发送者 {chat_id} 未被允许使用此机器人")
            return

        # 调用内部处理方法
        await self._process_incoming_message(
            chat_id=chat_id,
            sender_id=chat_id,
            content=content,
            conversation_id="",
            conversation_type="1",
            metadata=metadata,
        )

    def _build_user_message(self, chat_id: str, sender_id: str, content: str,
                            conversation_id: str, conversation_type: str,
                            metadata: dict[str, Any] | None = None) -> Message:
        """构建用户消息对象"""
        metadata = metadata or {}
        metadata.update({"conversation_id": conversation_id, "conversation_type": conversation_type})
        return Message(
            id=chat_id,
            type="req",
            channel_id=self.name,
            session_id=str(chat_id),
            params={"content": content, "query": content},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            metadata=metadata,
        )

    async def _process_incoming_message(self, chat_id: str, sender_id: str, content: str, conversation_id: str,
                              conversation_type: str, metadata: dict[str, Any] | None = None) -> None:
        """处理来自钉钉通道的传入消息"""
        msg = self._build_user_message(chat_id, sender_id, content, conversation_id, conversation_type, metadata)


        if self._gateway_callback:
            self._gateway_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    def _validate_config(self) -> bool:
        """验证配置是否有效"""
        if not DINGTALK_AVAILABLE:
            logger.error(
                "钉钉Stream SDK未安装。请运行: pip install dingtalk-stream"
            )
            return False

        if not self.config.client_id or not self.config.client_secret:
            logger.error("钉钉 client_id 和 client_secret 未配置")
            return False

        return True

    def _initialize_stream_client(self) -> None:
        """初始化钉钉Stream客户端"""
        logger.info(
            f"正在初始化钉钉Stream客户端，客户端ID: {self.config.client_id}..."
        )
        credential = Credential(self.config.client_id, self.config.client_secret)
        self._client = DingTalkStreamClient(credential)

        # 注册标准处理器
        handler = DingTalkHandler(self)
        self._client.register_callback_handler(ChatbotMessage.TOPIC, handler)

        logger.info("钉钉机器人已启动（Stream模式）")

    async def _run_stream_with_reconnect(self) -> None:
        """运行流连接并在断开时重连"""
        while self._running:
            try:
                await self._client.start()
            except Exception as e:
                logger.warning(f"钉钉流连接错误: {e}")
            if self._running:
                logger.info("5秒后重新连接钉钉流...")
                await asyncio.sleep(5)

    async def start(self) -> None:
        """启动钉钉机器人（Stream模式）"""
        try:
            if not self._validate_config():
                return

            self._running = True
            self._http = httpx.AsyncClient()

            self._initialize_stream_client()

            # 重连循环：如果SDK退出或崩溃则重启流
            await self._run_stream_with_reconnect()

        except Exception as e:
            logger.exception(f"启动钉钉通道失败: {e}")

    async def stop(self) -> None:
        """停止钉钉机器人"""
        self._running = False

        # 关闭共享HTTP客户端
        if self._http:
            await self._http.aclose()
            self._http = None

        # 取消未完成的后台任务
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

    def _is_token_valid(self) -> bool:
        """检查当前令牌是否有效"""
        return self._access_token is not None and time.time() < self._token_expiry

    def _build_token_request_data(self) -> dict:
        """构建令牌请求数据"""
        return {
            "appKey": self.config.client_id,
            "appSecret": self.config.client_secret,
        }

    def _parse_token_response(self, res_data: dict) -> None:
        """解析令牌响应"""
        self._access_token = res_data.get("accessToken")
        # 提前60秒过期以确保安全
        self._token_expiry = time.time() + int(res_data.get("expireIn", 7200)) - 60

    async def _request_new_token(self) -> str | None:
        """请求新的访问令牌"""
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        data = self._build_token_request_data()

        if not self._http:
            logger.warning("钉钉HTTP客户端未初始化，无法刷新令牌")
            return None

        try:
            resp = await self._http.post(url, json=data)
            resp.raise_for_status()
            res_data = resp.json()
            self._parse_token_response(res_data)
            return self._access_token
        except Exception as e:
            logger.error(f"获取钉钉访问令牌失败: {e}")
            return None

    async def _get_access_token(self) -> str | None:
        """获取或刷新访问令牌"""
        if self._is_token_valid():
            return self._access_token

        return await self._request_new_token()

    def _extract_message_content(self, msg: Message) -> str | None:
        """从消息对象中提取内容"""
        if msg.params and "content" in msg.params:
            return str(msg.params["content"])
        elif msg.payload and "content" in msg.payload:
            content_ = msg.payload["content"]
            if isinstance(content_, dict) and "output" in content_:
                return str(content_["output"])
            return str(content_)
        elif msg.payload and "text" in msg.payload:
            return str(msg.payload["text"])
        return None

    def _extract_chat_id(self, msg: Message) -> str | None:
        """从消息对象中提取聊天ID"""
        chat_id = msg.id if msg.id else None
        if not chat_id:
            chat_id = msg.session_id
        return chat_id

    def _build_group_message_payload(self, content: str, open_conversation_id: str) -> dict:
        """构建群聊消息负载"""
        return {
            "robotCode": self.config.client_id,
            "openConversationId": open_conversation_id,
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({
                "text": content,
                "title": "JiuClaw Reply",
            }),
        }

    def _build_private_message_payload(self, chat_id: str, content: str) -> dict:
        """构建私聊消息负载"""
        return {
            "robotCode": self.config.client_id,
            "userIds": [chat_id],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({
                "text": content,
                "title": "JiuClaw Reply",
            }),
        }

    def _get_send_api_url(self, conversation_type: str) -> str:
        """根据会话类型获取发送API URL"""
        if conversation_type == "2":
            return "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
        else:
            return "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"

    def _build_send_request(self, chat_id: str, content: str, conversation_type: str, open_conversation_id: str) -> tuple[str, dict]:
        """构建发送请求"""
        url = self._get_send_api_url(conversation_type)

        if conversation_type == "2":
            data = self._build_group_message_payload(content, open_conversation_id)
        else:
            data = self._build_private_message_payload(chat_id, content)

        return url, data

    async def _send_http_request(self, url: str, data: dict, token: str, chat_id: str) -> None:
        """发送HTTP请求"""
        headers = {"x-acs-dingtalk-access-token": token}

        if not self._http:
            logger.warning("钉钉HTTP客户端未初始化，无法发送消息")
            return

        try:
            resp = await self._http.post(url, json=data, headers=headers)
            if resp.status_code != 200:
                logger.error(f"钉钉消息发送失败: {resp.text}")
            else:
                logger.debug(f"钉钉消息已发送至 {chat_id}")
        except Exception as e:
            logger.error(f"发送钉钉消息时出错: {e}")

    async def send(self, msg: Message) -> None:
        """通过钉钉发送消息"""
        token = await self._get_access_token()
        if not token:
            return

        # 提取内容
        content = self._extract_message_content(msg)
        if not content:
            logger.warning("钉钉发送: 在 msg.params 或 msg.payload 中未找到内容")
            return

        # 提取聊天ID
        chat_id = self._extract_chat_id(msg)
        if not chat_id:
            logger.warning("钉钉发送: 在消息中未找到 chat_id 或 session_id")
            return

        # 构建请求
        metadata = msg.metadata or {}
        conversation_type = metadata.get("conversation_type", "")
        open_conversation_id = metadata.get("conversation_id", "")
        url, data = self._build_send_request(chat_id, content, conversation_type, open_conversation_id)

        # 发送HTTP请求
        await self._send_http_request(url, data, token, chat_id)

    async def _on_message(self, content: str, sender_id: str, sender_name: str, conversation_id: str,
                          conversation_type: str) -> None:
        """处理传入消息（由DingTalkHandler调用）

        委托给 _process_incoming_message()，该方法在发布到总线之前执行 allow_from
        权限检查。
        """
        try:
            logger.info(f"钉钉入站消息: {content} 来自 {sender_name}")
            await self._process_incoming_message(
                chat_id=sender_id,
                sender_id=sender_id,  # 对于私聊，chat_id == sender_id
                content=str(content),
                conversation_id=conversation_id,
                conversation_type=conversation_type,
                metadata={
                    "sender_name": sender_name,
                    "platform": "dingtalk",
                },
            )
        except Exception as e:
            logger.error(f"发布钉钉消息时出错: {e}")