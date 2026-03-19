# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Final

# ========== Workflow Related Constants ==========

WORKFLOW_REQUEST_CONTENT: Final[str] = (
    "Please provide your desired workflow description so I can generate "
    "the corresponding flowchart for you. If unclear, you can reply 'unclear' "
    "and I will plan the process for you."
)

WORKFLOW_DESIGN_RESPONSE_CONTENT: Final[str] = "Workflow design content:\n"

GENERATE_DL_FROM_DESIGN_CONTENT: Final[str] = (
    "Please generate the corresponding Process Definition Language (DL) "
    "description based on the following workflow design content:\n"
)

MODIFY_DL_CONTENT: Final[str] = (
    "Please correct the Process Definition Language (DL) based on the following error message:\n"
)

# ========== Default Configuration ==========

DEFAULT_MAX_HISTORY_SIZE: Final[int] = 50
DEFAULT_MAX_RETRIES: Final[int] = 3
DEFAULT_TIMEOUT: Final[int] = 30  # seconds

# ========== Resource Types ==========

RESOURCE_TYPE_PLUGIN: Final[str] = "plugin"
RESOURCE_TYPE_KNOWLEDGE: Final[str] = "knowledge"
RESOURCE_TYPE_WORKFLOW: Final[str] = "workflow"

# ========== Regex Patterns ==========

JSON_EXTRACT_PATTERN: Final[str] = r'```(?:json)?\s*([\s\S]*?)\s*```'

# ========== API Related Constants ==========

API_VERSION: Final[str] = "v1"
API_BASE_PATH: Final[str] = f"/api/{API_VERSION}"

# ========== Progress Related Constants ==========

PROGRESS_UPDATE_INTERVAL: Final[float] = 0.1  # seconds
PROGRESS_HEARTBEAT_INTERVAL: Final[float] = 30.0  # seconds

# ========== Limit Constants ==========

MAX_QUERY_LENGTH: Final[int] = 5000
MIN_QUERY_LENGTH: Final[int] = 1
MAX_SESSION_ID_LENGTH: Final[int] = 255
MAX_HISTORY_SIZE: Final[int] = 1000
MIN_HISTORY_SIZE: Final[int] = 1
