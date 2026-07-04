# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""演进系统日志配置

提供更清晰的日志格式，显示文件名和行号，只对 evolve 模块生效。
"""

import logging
import sys
from pathlib import Path


class FilePathFormatter(logging.Formatter):
    """自定义 Formatter，显示文件路径和行号而不是 logger name。

    格式示例：
    20:18:04 [INFO] diagnosis/agent.py:95 - Turn 1: Tool calls detected
    而不是：
    20:18:04 [INFO] jiuwenswarm.evolve.ahe.diagnosis.agent - Turn 1: Tool calls detected
    """

    def format(self, record: logging.LogRecord) -> str:
        # 从完整路径提取相对路径
        # 例如：D:\github\jiuwenswarm\jiuwenswarm\evolve\ahe\diagnosis\agent.py
        # 提取为：evolve/ahe/diagnosis/agent.py

        filepath = record.pathname

        # 找到 evolve 的起始位置
        if "evolve" in filepath:
            # 提取从 evolve 开始的相对路径
            parts = filepath.split("/")
            # Windows 也可能用反斜杠
            if len(parts) == 1:
                parts = filepath.split("\\")

            # 找到 evolve 的索引
            evolve_idx = -1
            for i, part in enumerate(parts):
                if part == "evolve":
                    evolve_idx = i
                    break

            if evolve_idx >= 0:
                # 提取相对路径
                relative_parts = parts[evolve_idx:]
                relative_path = "/".join(relative_parts[-3:])  # 只保留最后3层
            else:
                relative_path = Path(filepath).name
        else:
            relative_path = Path(filepath).name

        # 替换 logger name 为文件路径+行号
        record.name = f"{relative_path}:{record.lineno}"

        return super().format(record)


def setup_evolve_logging(
    log_file: str | None = None,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    show_file_path: bool = True,
) -> logging.Logger:
    """配置演进系统的日志输出

    Args:
        log_file: 日志文件路径（详细日志写入文件）
        console_level: stdout 显示级别 (DEBUG, INFO, WARNING, ERROR)
        file_level: 文件日志级别（默认 DEBUG，记录所有细节）
        show_file_path: 是否显示文件路径+行号（True=文件路径，False=logger name）

    Returns:
        配置好的 logger

    Example:
        >>> setup_evolve_logging(
        ...     log_file="benchmark/report/evolution.log",
        ...     console_level="INFO",
        ...     show_file_path=True,
        ... )

        # stdout 输出：
        # 20:18:04 [INFO] ahe/diagnosis/agent.py:95 - Turn 1: Tool calls detected

        # 文件输出（DEBUG级别）：
        # 2026-07-03 20:18:04 [DEBUG] ahe/diagnosis/agent.py:95 - executing tool: read_trace
    """
    # 只对 evolve 模块生效
    evolve_logger = logging.getLogger("jiuwenswarm.evolve")
    evolve_logger.setLevel(logging.DEBUG)  # 捕获所有级别
    evolve_logger.handlers.clear()  # 清除已有 handlers

    # 选择 formatter
    if show_file_path:
        # 文件路径+行号的 formatter
        console_formatter = FilePathFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%H:%M:%S",
        )
        file_formatter = FilePathFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        # 传统 logger name formatter
        console_formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%H:%M:%S",
        )
        file_formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console handler - stdout 显示关键信息
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_level.upper()))
    console_handler.setFormatter(console_formatter)
    evolve_logger.addHandler(console_handler)

    # File handler - 详细日志写入文件
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(getattr(logging, file_level.upper()))
        file_handler.setFormatter(file_formatter)
        evolve_logger.addHandler(file_handler)

    # 阻止向上传播到 root logger（避免重复输出）
    evolve_logger.propagate = False

    return evolve_logger


def get_log_file_path(workspace: Path, prefix: str = "evolution") -> str:
    """生成日志文件路径

    Args:
        workspace: 工作目录
        prefix: 日志文件前缀

    Returns:
        日志文件路径字符串

    Example:
        >>> get_log_file_path(Path("~/.jiuwenswarm"))
        '~/.jiuwenswarm/logs/evolution_20260703_201804.log'
    """
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = workspace / "logs"
    log_file = log_dir / f"{prefix}_{timestamp}.log"

    return str(log_file)


# 使用示例
if __name__ == "__main__":
    # 示例：配置日志
    setup_evolve_logging(
        log_file="example.log",
        console_level="INFO",
        show_file_path=True,
    )

    # 测试日志输出
    logger = logging.getLogger("jiuwenswarm.evolve.ahe.diagnosis.agent")
    logger.info("This is an INFO message")
    logger.debug("This is a DEBUG message (only in file)")
    logger.warning("This is a WARNING message")