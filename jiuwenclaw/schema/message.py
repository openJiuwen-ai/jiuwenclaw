# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""统一消息模型."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal


class ReqMethod(Enum):
    CHAT_SEND = "chat.send"
    CHAT_RESUME = "chat.resume"
    CHAT_CANCEL = "chat.interrupt"
    CHAT_ANSWER = "chat.user_answer"

    CONFIG_GET = "config.get"
    CONFIG_SET = "config.set"
    CHANNEL_GET = "channel.get"

    SESSION_LIST = "session.list"
    SESSION_CREATE = "session.create"
    SESSION_DELETE = "session.delete"

    PATH_GET = "path.get"
    PATH_SET = "path.set"

    BROWSER_START = "browser.start"
    
    MEMORY_COMPUTE = "memory.compute"
    
    FILES_LIST = "files.list"
    FILES_GET = "files.get"
    TTS_SYNTHESIZE = "tts.synthesize"

    SKILLS_MARKETPLACE_LIST = "skills.marketplace.list"
    SKILLS_LIST = "skills.list"
    SKILLS_INSTALLED = "skills.installed"
    SKILLS_GET = "skills.get"
    SKILLS_INSTALL = "skills.install"
    SKILLS_IMPORT_LOCAL = "skills.import_local"
    SKILLS_MARKETPLACE_ADD = "skills.marketplace.add"
    SKILLS_MARKETPLACE_REMOVE = "skills.marketplace.remove"
    SKILLS_MARKETPLACE_TOGGLE = "skills.marketplace.toggle"
    SKILLS_UNINSTALL = "skills.uninstall"

    HEARTBEAT_GET_CONF = "heartbeat.get_conf"
    HEARTBEAT_SET_CONF = "heartbeat.set_conf"

    CHANNEL_FEISHU_GET_CONF = "channel.feishu.get_conf"
    CHANNEL_FEISHU_SET_CONF = "channel.feishu.set_conf"

    CHANNEL_XIAOYI_GET_CONF = "channel.xiaoyi.get_conf"
    CHANNEL_XIAOYI_SET_CONF = "channel.xiaoyi.set_conf"

    CHANNEL_DINGTALK_GET_CONF = "channel.dingtalk.get_conf"
    CHANNEL_DINGTALK_SET_CONF = "channel.dingtalk.set_conf"

class EventType(Enum):
    CONNECTION_ACK = "connection.ack"
    HELLO = "hello"
    CHAT_DELTA = "chat.delta"
    CHAT_FINAL = "chat.final"
    CHAT_MEDIA = "chat.media"
    CHAT_TOOL_CALL = "chat.tool_call"
    CHAT_TOOL_RESULT = "chat.tool_result"
    CONTEXT_COMPRESSED = "context.compressed"
    TODO_UPDATED = "todo.updated"
    CHAT_PROCESSING_STATUS = "chat.processing_status"
    CHAT_ERROR = "chat.error"
    CHAT_INTERRUPT_RESULT = "chat.interrupt_result"
    CHAT_SUBTASK_UPDATE = "chat.subtask_update"
    CHAT_ASK_USER_QUESTION = "chat.ask_user_question"
    HEARTBEAT_RELAY = "heartbeat.relay"


class Mode(Enum):
    AGENT = "agent"
    PLAN = "plan"


@dataclass
class Message:
    """统一消息结构."""
    id: str
    type: Literal["req", "res", "event"]
    channel_id: str
    session_id: str | None
    params: dict
    timestamp: float
    ok: bool
    payload: dict | None = None
    req_method: ReqMethod | None = None
    event_type: EventType | None = None
    mode: Mode = Mode.PLAN
    is_stream: bool = False
    stream_seq: int | None = None
    stream_id: str | None = None
    metadata: dict[str, Any] | None = None
