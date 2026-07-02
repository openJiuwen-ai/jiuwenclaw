# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""chat.send 参数契约。

本文件定义 chat.send 的参数结构。
"""

from typing import TypedDict, NotRequired


class ChatSendParams(TypedDict, total=False):
    """chat.send 参数契约（TypedDict，供类型标注与文档）。

    说明：
    - content: 用户消息正文（主字段，保留 /skill 等标记原样）
    - query: DEPRECATED. 历史双发字段，将逐步迁移到 content。
            参见 PLAN_chat_send_params_standardization.md §3.3-2。
    - skills: 用户选中的 skill 名列表（当前为 prompt 提示语义，非强制生效）。
    - mode: 运行模式（agent.plan / agent.fast / code.normal / team 等）
    - attachments: 附件列表（@file 等）
    - files: 文件更新字典（传统字段，逐步迁出到 attachments）
    """

    content: str
    """用户消息正文（主字段）。保留 /skill 等标记原样，不剥离。"""

    query: str
    """DEPRECATED: 历史双发字段，当前与 content 同值。

    将逐步废弃，未来统一只用 content。新代码应优先读 content；query 保留为兼容过渡。
    """

    skills: NotRequired[list[str]]
    """用户选中的 skill 名列表（可选）。

    - 来源：TUI/web 前端从 content 提取（如 /doc /review）或 UI 选择器。
    - 语义：当前为 prompt 提示（塞入 user_message_context["skills_to_use"]），
            模型可见但非强制。真正强制生效需 A3 阶段（SkillSelectionRail）。
    - 空列表或缺失：不指定 skill，agent 自主判断。
    """

    mode: NotRequired[str]
    """运行模式。如 agent.plan / agent.fast / code.normal / code.team / team。"""

    attachments: NotRequired[list[dict]]
    """附件列表（@file 等）。结构待统一定义。"""

    files: NotRequired[dict]
    """文件更新字典（传统字段）。逐步迁出到 attachments，当前兼容保留。"""

    trusted_dirs: NotRequired[list[str]]
    """可信目录列表（权限白名单）。"""

    project_dir: NotRequired[str]
    """项目根目录（稳定身份）。"""

    cwd: NotRequired[str]
    """当前工作目录。"""

    workspace_dir: NotRequired[str]
    """工作空间目录。"""

    plan_entry_source: NotRequired[str]
    """plan 模式入口来源（internal use）。"""

    answers: NotRequired[list]
    """用户交互问答（interrupt resume 场景）。"""

    original_request: NotRequired[str]
    """原始请求（supplement 场景保留）。"""
