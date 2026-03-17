# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import sys

START = sys.intern("__start__")
END = sys.intern("__end__")
MAX_RECURSIVE_LIMIT = 10000

TASK_STATUS_INTERRUPT = "__interrupt__"
TASK_STATUS_ERROR = "__error__"

NS_SEPARATOR = ":"
NS_REPLACE_CHAR = "#"

NS: str = "ns"
PARENT_NS: str = "parent_ns"
SESSION_ID: str = "session_id"
RECURSION_LIMIT: str = "recursion_limit"
