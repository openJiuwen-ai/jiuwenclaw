# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServer 工具函数与共享常量."""

from typing import Any

# harness read_file：False 时不把图片字节注入主模型对话，改走 image_ocr / VQA 等视觉工具。
# 用于 create_deep_agent(enable_read_image_multimodal=...) 与 ReadFileTool(enable_image_multimodal=...)。
DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL: bool = False

from jiuwenclaw.schema.agent import AgentRequest


def get_chat_id(request: AgentRequest) -> str | None:
    """获取请求的 Chat ID（平台聊天标识）。

    优先使用顶层字段，向后兼容 metadata 方式。

    Args:
        request: AgentServer 请求对象

    Returns:
        平台聊天标识（Chat ID），如果无法获取则返回 None
    """
    # 1. 优先使用顶层字段
    if request.chat_id:
        return request.chat_id

    # 2. 向后兼容：从 metadata 获取（优先级按平台）
    if request.metadata:
        return (
            request.metadata.get('feishu_chat_id') or
            request.metadata.get('wecom_chat_id') or
            request.metadata.get('dingtalk_chat_id') or
            request.metadata.get('xiaoyi_session_id')
        )
    return None


def extract_uploaded_files(params: dict):
    """检查 request.params.files 中是否有新上传的文件。

     当用户中断后上传了新文件（任何类型），该函数返回 True，用于
     plan_pause 提示中引导 LLM 选择"全新任务"而非"续跑旧计划"。

     兼容两种 files 格式：
     - dict 格式（OfficeClaw）: {"uploaded": [{"name": "...", "path": "...", "type": "..."}]}
     - list 格式（各 channel）: [{"name": "...", "path": "...", "type": "..."}]
    """
    files = params.get("files")
    if files is None:
        return None
    if isinstance(files, dict):
        entries = files.get("uploaded", [])
    elif isinstance(files, list):
        entries = files
    else:
        return None
    if not isinstance(entries, list):
        return None
    return entries
