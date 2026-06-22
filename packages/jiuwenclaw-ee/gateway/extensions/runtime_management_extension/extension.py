# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Extension."""

from __future__ import annotations

import asyncio
import logging

from jiuwenclaw.extensions import ExtensionConfig
from jiuwenclaw.extensions.sdk.agent_server_client import AgentServerClientExtension

logger = logging.getLogger(__name__)

_NOISY_THIRD_PARTY_LOGGERS = (
    "kubernetes",
    "kubernetes_asyncio",
    "urllib3",
)
_RUNTIME_SDK_LOGGER_NAME = "openjiuwen_runtime"
_RUNTIME_SDK_LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
)
_RUNTIME_SDK_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_RUNTIME_SDK_LOG_MAX_BYTES = 10 * 1024 * 1024
_RUNTIME_SDK_LOG_BACKUP_COUNT = 5
_runtime_sdk_logging_configured = False


def _configure_runtime_sdk_logging() -> str | None:
    """仅初始化 ``openjiuwen_runtime`` 日志，不改动 root 级别或 handler。

    ``openjiuwen_runtime.foundation.log.setup_logging()`` 在缺少 YAML 时会
    ``basicConfig(level=DEBUG)`` 并清空 root handler，导致 ``kubernetes_asyncio``
    等第三方库刷屏；此处改为只挂载 SDK 命名空间 logger。
    """
    global _runtime_sdk_logging_configured

    from openjiuwen_runtime.foundation.log.handler import CompressedRotatingFileHandler

    if _runtime_sdk_logging_configured:
        return None

    formatter = logging.Formatter(
        fmt=_RUNTIME_SDK_LOG_FORMAT,
        datefmt=_RUNTIME_SDK_LOG_DATEFMT,
    )

    rt_logger = logging.getLogger(_RUNTIME_SDK_LOGGER_NAME)
    rt_logger.setLevel(logging.INFO)
    rt_logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    rt_logger.addHandler(console_handler)

    log_file: str | None = None
    try:
        from jiuwenclaw.utils import get_logs_dir

        log_path = get_logs_dir() / "openjiuwen_runtime" / "openjiuwen_runtime.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = str(log_path)
        file_handler = CompressedRotatingFileHandler(
            log_file,
            maxBytes=_RUNTIME_SDK_LOG_MAX_BYTES,
            backupCount=_RUNTIME_SDK_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        rt_logger.addHandler(file_handler)
    except Exception as exc:
        logger.warning("[RuntimeManagement] runtime SDK file log unavailable: %s", exc)

    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _runtime_sdk_logging_configured = True
    return log_file


class RuntimeManagementExtension(AgentServerClientExtension):
    """Runtime 管理扩展。"""

    def __init__(self, client: RuntimeManagementAgentClient) -> None:
        self._client = client

    async def initialize(self, config: ExtensionConfig) -> None:
        return None

    def get_client(self) -> RuntimeManagementAgentClient:
        return self._client

    async def shutdown(self) -> None:
        try:
            await self._client.disconnect()
        except Exception as exc:
            logger.warning("[RuntimeManagement] shutdown error: %s", exc)

    def setup_gateway_shutdown_signals(self, shutdown_requested: asyncio.Event) -> None:
        """Gateway 调用：注册 SIGINT/SIGTERM；退出路径由 Gateway ``finally`` 里 ``await client.disconnect()``（与本扩展 shutdown 一致）。"""
        import signal
        import sys

        logger.info(
            "[RuntimeManagement] 注册停机信号处理器（K8s 删 Pod/SIGINT）；"
            "退出时将执行 RuntimeManagementAgentClient.disconnect()。"
        )

        def _on_signal() -> None:
            logger.info("[RuntimeManagement] 收到停机信号，准备退出…")
            shutdown_requested.set()

        if sys.platform == "win32":
            signal.signal(signal.SIGINT, lambda signum, frame: _on_signal())
            logger.info("[RuntimeManagement] 已注册 SIGINT (Windows)")
            signal.signal(signal.SIGTERM, lambda signum, frame: _on_signal())
            logger.info("[RuntimeManagement] 已注册 SIGTERM (Windows)")
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, lambda signum, frame: _on_signal())
                logger.info("[RuntimeManagement] 已注册 SIGBREAK (Windows)")
        else:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _on_signal)
                    logger.info("[RuntimeManagement] 已注册 %s", sig)
                except (NotImplementedError, OSError) as exc:
                    logger.info("[RuntimeManagement] 无法注册 %s: %s", sig, exc)


async def register_extensions(registry) -> list[RuntimeManagementExtension]:
    """注册 Runtime Management 扩展。"""
    log_file = _configure_runtime_sdk_logging()
    if log_file:
        logger.info("[RuntimeManagement] runtime SDK log file: %s", log_file)

    from .runtime_management_client import RuntimeManagementAgentClient

    client = RuntimeManagementAgentClient()
    ext = RuntimeManagementExtension(client)
    registry.register_agent_server_client(ext)
    return [ext]
