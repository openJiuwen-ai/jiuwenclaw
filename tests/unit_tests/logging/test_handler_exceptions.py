# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""测试 SafeRotatingFileHandler 的异常处理行为

验证 doRollover() 和 _cleanup_old_backups() 正确传播异常。
"""

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jiuwenclaw.jiuwenclaw_logging.handler import SafeRotatingFileHandler


class TestDoRolloverExceptionPropagation:
    """测试 doRollover() 的异常传播"""

    @staticmethod
    def test_do_rollover_propagates_copy_exception():
        """验证 doRollover 传播 copy 失败的异常"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(str(log_file), maxBytes=100)

            try:
                with patch(
                    'jiuwenclaw.jiuwenclaw_logging.handler.shutil.copy2',
                    side_effect=OSError("No space left on device")
                ):
                    with pytest.raises(OSError, match="No space left on device"):
                        handler.doRollover()
            finally:
                handler.close()

    @staticmethod
    def test_do_rollover_propagates_truncate_exception():
        """验证 doRollover 传播 truncate 失败的异常"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(str(log_file), maxBytes=100)
            try:
                # 创建文件以便有流可操作
                handler.emit(
                    logging.LogRecord(
                        "test", logging.INFO, "test.py", 1, "test message", (), None
                    )
                )

                with patch.object(
                    handler.stream, "truncate", side_effect=OSError("Permission denied")
                ):
                    with pytest.raises(OSError, match="Permission denied"):
                        handler.doRollover()
            finally:
                handler.close()


class TestCleanupExceptionPropagation:
    """测试 _cleanup_old_backups() 的异常传播"""

    @staticmethod
    def test_cleanup_propagates_stat_exception():
        """验证 cleanup 传播 stat 失败的异常（通过 doRollover）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(
                str(log_file), maxBytes=100, backupCount=2
            )

            try:
                # 创建真实的备份文件
                backup_file = Path(tmpdir) / "test_20250101_120000.log"
                backup_file.write_text("backup content")

                # mock stat() 方法（需要接受 follow_symlinks 参数）
                def mock_stat(self, *, follow_symlinks=True):
                    raise OSError("I/O error")

                with patch.object(Path, "stat", mock_stat):
                    # doRollover 会调用 _cleanup_old_backups
                    # 异常应该传播出来
                    with pytest.raises(OSError, match="I/O error"):
                        handler.doRollover()
            finally:
                handler.close()

    @staticmethod
    def test_cleanup_propagates_sort_exception():
        """验证 cleanup 排序时传播 stat 异常（通过 doRollover）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(
                str(log_file), maxBytes=100, backupCount=2
            )

            try:
                # 创建多个备份文件
                for i in range(3):
                    backup_file = Path(tmpdir) / f"test_{i:010d}.log"
                    backup_file.write_text(f"backup {i}")

                # mock stat() 使其在排序时失败（需要接受 follow_symlinks 参数）
                call_count = [0]

                def mock_stat(self, *, follow_symlinks=True):
                    call_count[0] += 1
                    if call_count[0] > 2:  # 让第三次调用失败
                        raise OSError("I/O error during sort")
                    # 调用原始方法
                    return Path.stat.__get__(self, type(self))(
                        follow_symlinks=follow_symlinks
                    )

                with patch.object(Path, "stat", mock_stat):
                    # doRollover 会调用 _cleanup_old_backups
                    # 异常应该传播出来
                    with pytest.raises(OSError, match="I/O error during sort"):
                        handler.doRollover()
            finally:
                handler.close()


class TestEmitCatchesException:
    """测试 emit() 正确捕获和处理异常"""

    @staticmethod
    def test_emit_catches_rollover_exception():
        """验证 emit 捕获 doRollover 的异常并调用 handleError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(str(log_file), maxBytes=1)
            handler.backup_count = 2

            try:
                # 模拟 doRollover 抛出异常
                with patch.object(
                    handler, "doRollover", side_effect=OSError("Test error")
                ):
                    # 模拟 handleError 被调用
                    with patch.object(handler, "handleError") as mock_handle:
                        # 创建一个日志记录
                        record = logging.LogRecord(
                            "test", logging.INFO, "test.py", 1, "test message", (), None
                        )
                        # emit 应该不抛异常
                        handler.emit(record)
                        # 验证 handleError 被调用
                        mock_handle.assert_called_once()
            finally:
                handler.close()

    @staticmethod
    @pytest.mark.filterwarnings("ignore::ResourceWarning")
    def test_emit_does_not_crash_on_rollover_error():
        """验证 emit 在 doRollover 失败时不会崩溃

        注意：此测试使用 mock doRollover，会触发 logging 框架内部的资源警告。
        该警告是由于 mock 导致的框架内部状态问题，不是实际的资源泄漏。
        因此在此测试中抑制 ResourceWarning。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(str(log_file), maxBytes=1)

            try:
                # 模拟 doRollover 抛出异常
                with patch.object(
                    handler, "doRollover", side_effect=OSError("No space")
                ):
                    record = logging.LogRecord(
                        "test", logging.INFO, "test.py", 1, "test message", (), None
                    )
                    # 应该不抛异常
                    handler.emit(record)
            finally:
                handler.close()


class TestHandleErrorOutput:
    """测试 handleError() 的输出"""

    @staticmethod
    @pytest.mark.filterwarnings("ignore::ResourceWarning")
    def test_handle_error_outputs_to_stderr(capsys):
        """验证 handleError 输出到 stderr

        注意：此测试可能触发 ResourceWarning，
        源于 logging 框架的内部资源管理。
        因此在此测试中抑制 ResourceWarning。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(str(log_file), maxBytes=100)

            try:
                # 调用 handleError
                record = logging.LogRecord(
                    "test", logging.ERROR, "test.py", 1, "test error", (), None
                )
                try:
                    raise OSError("Test error for handleError")
                except OSError:
                    handler.handleError(record)

                # 捕获 stderr 输出
                captured = capsys.readouterr()
                # 应该有错误输出（Python logging 的默认行为）
                assert "Logging error" in captured.err or "Traceback" in captured.err
            finally:
                handler.close()


class TestNormalOperation:
    """测试正常操作不被影响"""

    @staticmethod
    def test_successful_rollover():
        """验证成功的轮转操作"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            handler = SafeRotatingFileHandler(
                str(log_file), maxBytes=100, backupCount=2
            )

            try:
                # 写入一些内容
                handler.emit(
                    logging.LogRecord(
                        "test", logging.INFO, "test.py", 1, "x" * 50, (), None
                    )
                )
                handler.flush()

                # 执行轮转
                handler.doRollover()

                # 验证备份文件存在
                backups = list(Path(tmpdir).glob("test_*.log"))
                assert len(backups) > 0
            finally:
                handler.close()

    @staticmethod
    def test_successful_cleanup():
        """验证成功的清理操作（通过 doRollover）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            # backupCount=2，最多保留 2 个备份
            handler = SafeRotatingFileHandler(
                str(log_file), maxBytes=100, backupCount=2
            )

            try:
                log_path = Path(tmpdir)

                # 创建初始备份文件（模拟历史备份）
                for i in range(3):
                    # 使用旧的时间戳确保排序正确
                    backup = log_path / f"test_{i:010d}.log"
                    backup.write_text(f"backup {i}")

                # 验证初始状态：有 3 个备份
                initial_backups = list(log_path.glob("test_*.log"))
                assert len(initial_backups) == 3

                # 触发轮转 → doRollover() → _cleanup_old_backups()
                handler.doRollover()

                # 验证：应该只保留 2 个备份（backupCount）
                final_backups = list(log_path.glob("test_*.log"))
                assert len(final_backups) == 2
            finally:
                handler.close()
