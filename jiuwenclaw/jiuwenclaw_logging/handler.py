# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""日志文件处理器模块

提供安全的日志文件轮转处理器。

异常处理策略：
    本模块采用 Python 标准库的异常处理风格。doRollover() 和 _cleanup_old_backups()
    方法在文件操作失败时会抛出 OSError，由 BaseRotatingHandler.emit() 的异常处理
    机制捕获并调用 handleError() 输出到 stderr。这种设计确保了：

    1. 代码简洁：无冗余的 try-except 嵌套
    2. 与标准库一致：遵循 logging 框架的设计模式
    3. 错误可见：所有错误通过 stderr 输出，程序不会崩溃
"""

import datetime
import logging
import shutil
from logging.handlers import BaseRotatingHandler
from pathlib import Path


_LOG_FILE_MAX_BYTES = 20 * 1024 * 1024
_LOG_FILE_BACKUP_COUNT = 20


class SafeRotatingFileHandler(BaseRotatingHandler):
    """安全的日志文件轮转处理器"""

    def __init__(self, filename, maxBytes=0, backupCount=0, encoding=None,
                 delay=False, errors=None):
        """初始化处理器。

        Args:
            filename: 日志文件路径
            maxBytes: 日志文件最大字节数，超过此大小触发轮转
            backupCount: 保留的备份文件数量
            encoding: 文件编码
            delay: 是否延迟打开文件
            errors: 编码错误处理方式
        """
        super().__init__(filename, 'a', encoding, errors)
        self.max_bytes = maxBytes
        self.backup_count = backupCount
        self._current_filename = filename

        if delay:
            self.stream = None

    def emit(self, record):
        """Emit a record and release the current stream if rollover fails.

        On Windows, an unclosed stream will keep the log file locked and break
        temporary-directory cleanup in tests. Closing only the current stream
        keeps the handler reusable because FileHandler.emit() will reopen it on
        the next successful write.
        """
        try:
            if self.shouldRollover(record):
                self.doRollover()
            logging.FileHandler.emit(self, record)
        except Exception:
            if self.stream is not None:
                try:
                    self.stream.close()
                finally:
                    self.stream = None
            self.handleError(record)

    def shouldRollover(self, record):
        """
        确定是否需要轮转日志文件。

        当日志文件大小超过 maxBytes 时返回 True。

        Args:
            record: 日志记录对象

        Returns:
            bool: 如果需要轮转返回 True，否则返回 False
        """
        if self.stream is None:
            return False
        if self.max_bytes > 0:
            msg = "%s\n" % self.format(record)
            self.stream.seek(0, 2)  # Seek to end of file
            if self.stream.tell() + len(msg) >= self.max_bytes:
                return True
        return False

    def doRollover(self):
        """
        执行日志轮转，保持 app.log 作为活动日志文件。

        创建带时间戳的备份文件，清理旧备份文件，然后截断当前日志文件。

        Raises:
            OSError: 文件操作失败时抛出，由 emit() 的异常处理机制捕获。
        """
        base_path = Path(self.baseFilename)

        timestamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_filename = base_path.parent / f"{base_path.stem}_{timestamp}{base_path.suffix}"

        # 复制备份文件（异常向上传播）
        if base_path.exists():
            shutil.copy2(base_path, backup_filename)

        # 清理旧备份文件
        self._cleanup_old_backups()

        # 截断当前文件（异常向上传播）
        if self.stream:
            self.stream.seek(0)  # Seek to beginning
            self.stream.truncate(0)  # Truncate to 0 bytes

    def _cleanup_old_backups(self):
        """
        清理超过 backupCount 数量的旧备份文件。

        备份文件按修改时间排序（最旧的优先）。

        Raises:
            OSError: 文件操作失败时抛出。
        """
        if self.backup_count <= 0:
            return

        base_path = Path(self.baseFilename)
        log_dir = base_path.parent

        # 使用列表推导式收集备份文件
        backup_files = [
            f for f in log_dir.glob(f"{base_path.stem}_*{base_path.suffix}")
            if f.is_file() and f != base_path
        ]

        # 按修改时间排序（最旧的优先）
        backup_files.sort(key=lambda x: x.stat().st_mtime)

        # 删除超出数量的文件
        files_to_delete = len(backup_files) - self.backup_count
        if files_to_delete > 0:
            for f in backup_files[:files_to_delete]:
                f.unlink()  # 失败会抛异常
