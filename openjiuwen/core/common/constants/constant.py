# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import sys

# IR userFields key
USER_FIELDS = "userFields"
QUERY = "query"
# IR systemFields key
SYSTEM_FIELDS = "systemFields"

# Workflow
INTERACTION = sys.intern("__interaction__")
# for dynamic interaction raised by nodes
INTERACTIVE_INPUT = sys.intern("__interactive_input__")
INPUTS_KEY = "inputs"
CONFIG_KEY = "config"
END_FRAME = "all streaming outputs finish"
END_NODE_STREAM = "end node stream"
LOOP_ID = "__sys_loop_id"
INDEX = "index"
FINISH_INDEX = "finish_index"

# safe limit constants
MAX_COLLECTION_SIZE = 100000  # maximum allowed collection size
MAX_EXPRESSION_LENGTH = 5000  # maximum allowed expression length
MAX_AST_DEPTH = 50  # maximum allowed AST depth
NESTED_LOOP_DEPTH = 1  # maximum allowed nested loop depth (1 means no nesting)
