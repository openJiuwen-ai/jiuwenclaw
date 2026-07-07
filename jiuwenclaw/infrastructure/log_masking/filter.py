# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""logging.Filter：对日志消息应用 LogMaskingEngine。"""

from __future__ import annotations

import logging

from .engine import LogMaskingEngine

_logger = logging.getLogger(__name__)
_LOG_MASKING_DONE_ATTR = "_jiuwenclaw_log_masking_done"


class SensitiveDataFilter(logging.Filter):
    """Mask sensitive data in all log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, _LOG_MASKING_DONE_ATTR, False):
            return True
        try:
            message = record.getMessage()
            record.msg = LogMaskingEngine.get_instance().sanitize(message)
            record.args = ()
            setattr(record, _LOG_MASKING_DONE_ATTR, True)
        except Exception:
            _logger.debug(
                "[LogMasking] sensitive data filter failed",
                exc_info=True,
            )
        return True
