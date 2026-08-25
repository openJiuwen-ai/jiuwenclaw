"""
版权所有 (c) 华为技术有限公司 2026-2026
"""

import os

# 配置文件
CONFIG_FILE_NAME = 'configs.json'
ENV_FILE_PATH = os.path.join(os.path.dirname(__file__), '.xiaoyienv')

# API
API_URL_SUFFIX = '/celia-claw/v1/rest-api/skill/execute'

# OSMS 文件上传
OSMS_PREPARE_URL_SUFFIX = '/osms/v1/file/manager/prepare'
OSMS_COMPLETE_URL_SUFFIX = '/osms/v1/file/manager/completeAndQuery'
TEMPORARY_MATERIAL_PACKAGE = 'TEMPORARY_MATERIAL_PACKAGE'
FILE_OWNER_UID = 'openclaw'
FILE_OWNER_TEAM_ID = 'openclaw'
MAX_TIMES = 3
CONNECT_TIMEOUT = 15000
READ_TIMEOUT = 300000
EXPIRE_TIME = 259200

# 环境变量
REQUIRED_ENV_VARS = ['PERSONAL-API-KEY', 'PERSONAL-UID', 'SERVICE_URL', 'SANDBOX-ID']

# questionText 最大长度
MAX_QUESTION_LENGTH = 2000

# 哈希计算忽略
IGNORE_DIRS = {".github", ".git", "__pycache__", "tests"}
IGNORE_FILES = {
    "LICENSE.txt",
    "LICENSE",
    "README.md",
    "_meta.json",
    "pytest.ini",
    "requirements.txt",
    ".gitignore",
    ".env.example",
}

# 扫描结果
SCAN_STATUS_ACCEPT = 'ACCEPT'
SCAN_STATUS_REJECT = 'REJECT'
SCAN_STATUS_ERROR = 'ERROR'
