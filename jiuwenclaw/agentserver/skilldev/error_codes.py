# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""file.write 错误码常量.

三段式数字码 999001XXX，前两段固定 999001，第三段 3 位区分具体错误。
"""

from __future__ import annotations

# -- file.write 基础校验 (250-255) ------------------------------------------

ERR_FW_MISSING_TASK_OR_PATH = "999001250"  # 请求参数不完整：task_id 或 path 字段缺失或为空
ERR_FW_MISSING_CONTENT = "999001251"  # 请求参数不完整：content 字段未提供（值为 None）
ERR_FW_CONTENT_TOO_LARGE = "999001252"  # 写入内容超过单文件大小上限（_MAX_FILE_CONTENT_SIZE = 1MB）
ERR_FW_PATH_ESCAPE = "999001253"  # 路径越界：resolve 后的绝对路径不在 workspace/skill/ 目录内
ERR_FW_FILE_NOT_FOUND = "999001254"  # 目标路径在 workspace/skill/ 下不存在或不是普通文件
ERR_FW_BINARY_FILE = "999001255"  # 目标文件无法以 UTF-8 解码，判定为二进制文件，不允许写入

# -- SKILL.md 校验 (260-265) ------------------------------------------------

ERR_FW_SKILLMD_FRONTMATTER_FORMAT = "999001260"  # SKILL.md frontmatter 结构异常：缺少起始/闭合 ---、YAML 解析失败、或解析结果不是 dict
ERR_FW_SKILLMD_FRONTMATTER_KEY = "999001261"  # SKILL.md frontmatter 存在重复 key 或包含不在白名单内的未知 key
ERR_FW_SKILLMD_NAME = "999001262"  # SKILL.md name 字段校验失败：缺失、为空、非 kebab-case、含连续连字符、超过 64 字符、或类型非字符串
ERR_FW_SKILLMD_DESCRIPTION = "999001263"  # SKILL.md description 字段校验失败：缺失、为空、字符数超限(中文512，英文1024)、token 数超限(300)、或类型非字符串
ERR_FW_SKILLMD_BODY = "999001264"  # SKILL.md 正文（frontmatter 之后的内容）校验失败：为空、行数超过 500 行、或 token 数超过 5000
ERR_FW_SKILLMD_CREDENTIAL = "999001265"  # SKILL.md 中检测到疑似硬编码凭据（如 AWS Key、API Key、私钥、数据库连接串等）

# -- 脚本安全校验 (270-271) --------------------------------------------------

ERR_FW_SCRIPT_DANGEROUS_PATTERN = "999001270"  # scripts/ 目录下的脚本文件命中危险命令模式（如 rm -rf、chmod 777、curl|bash、eval()、exec() 等）
ERR_FW_SCRIPT_CREDENTIAL = "999001271"  # scripts/ 目录下的脚本文件中检测到疑似硬编码凭据（如 AWS Key、API Key、私钥、数据库连接串等）
