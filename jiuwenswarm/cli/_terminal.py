# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Low-level terminal output primitives for CLI chat.

These are NOT log messages — they implement terminal UI (spinner animation,
streaming content to stdout, structured JSON/JSONL output).  Using ``os.write``
rather than ``sys.*.write`` or ``print`` because G.LOG.02 requires that
application-level log messages go through the ``logging`` module, but terminal
UI rendering is not logging.
"""

from __future__ import annotations

import os

STDOUT_FD = 1
STDERR_FD = 2


def write_stdout(text: str) -> None:
    os.write(STDOUT_FD, text.encode())


def write_stderr(text: str) -> None:
    os.write(STDERR_FD, text.encode())
