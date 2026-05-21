from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ExtensionMetadata:
    """扩展元数据"""
    id: str                      # 扩展唯一标识
    name: str                    # 扩展名称
    version: str                 # 扩展版本
    description: str             # 扩展描述
    author: str                  # 扩展作者
    min_jiuwenclaw_version: str  # 最小兼容版本
    dependencies: dict[str, str]  # 扩展依赖 {"extension_id": ">=1.0.0"}
    config_schema: dict | None = None  # 配置模式 (JSON Schema)
    priority: int = 10           # 加载优先级（数值越小越先加载，默认 10）


@dataclass
class ExtensionConfig:
    config: dict[str, Any]
    logger: Any


@dataclass
class WsHandlerContext:
    """WebSocket 处理器上下文，包含请求信息供扩展处理函数使用。"""
    request_id: str          # 请求ID
    channel_id: str          # 渠道标识（如 "web", "feishu"）
    session_id: str | None = None   # 会话ID（可选）
    params: dict[str, Any] = field(default_factory=dict)  # 请求参数
    metadata: dict[str, Any] | None = None    # 请求元数据（可选）

    # 扩展可写入
    response_metadata: dict[str, Any] = field(default_factory=dict)  # 响应元数据


@dataclass
class WsHandlerEntry:
    """WebSocket 处理器注册记录。"""
    method: str                              # 请求方法名（点号分隔命名空间）
    handler: Callable[..., Awaitable[dict]]       # 异步处理函数
    source_id: str = ""                      # 扩展标识（用于日志）
    is_stream: bool = False                  # 是否流式响应（预留字段）
