# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""chat.send 参数契约。

本文件定义 chat.send 的参数结构。
"""

from typing import TypedDict, NotRequired

# ── plan_entry_source 契约常量（P7：跨层共享单源） ──
# ``plan_entry_source`` 的合法取值，表示"用户这一条消息明确要求进入 plan"。
# 一次性字段：TUI 的 ``/plan`` 命令发 ``slash_command``，Web 用户手动打开 Plan
# 开关后的第一条消息发 ``plan_toggle``。
#
# P4.2 坑点：原先后端 ``agent_ws_server._PLAN_ENTRY_SOURCES`` 硬编码、前端各自
# 字面量，无共享 schema。P7 提到 schema 层做单源，前后端引用同一组字面量。
# 跨语言（Python/TS）无法共享一个常量对象，故前端各自定义同名 TS 常量，
# 跨层契约测试确保字面量一致（见 tests/unit_tests/test_plan_entry_source_contract.py）。
PLAN_ENTRY_SOURCE_SLASH_COMMAND = "slash_command"
PLAN_ENTRY_SOURCE_PLAN_TOGGLE = "plan_toggle"

PLAN_ENTRY_SOURCES: frozenset[str] = frozenset(
    {PLAN_ENTRY_SOURCE_SLASH_COMMAND, PLAN_ENTRY_SOURCE_PLAN_TOGGLE}
)


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

    supports_user_interaction: NotRequired[bool]
    """客户端是否能处理 ask_user 等用户交互。缺省为 True，兼容现有客户端。"""

    plan_entry_source: NotRequired[str]
    """plan 模式入口来源（internal use）。

    合法取值见 :data:`PLAN_ENTRY_SOURCES`：``slash_command``（TUI /plan）、
    ``plan_toggle``（Web 手动打开 Plan 开关的首条消息）。一次性字段，防重入闸门
    据此区分"用户明确要求进入 plan"和"开关没复位导致的残留请求"。
    """

    answers: NotRequired[list]
    """用户交互问答（interrupt resume 场景）。"""

    original_request: NotRequired[str]
    """原始请求（supplement 场景保留）。"""

    session_id: NotRequired[str]
    """会话 ID（Web 前端通过 params 传递，通常由 Message 框架层提取到 request.session_id）。"""

    model_name: NotRequired[str]
    """模型名称（Web 前端可选传递）。"""

    request_id: NotRequired[str]
    """请求 ID（interrupt resume / 问答回复场景关联）。"""

    source: NotRequired[str]
    """来源标识（如 permission_interrupt / confirm_interrupt / ask_user_interrupt / evolution_interrupt）。"""

    is_supplement: NotRequired[bool]
    """是否为补充请求（Gateway 用于判断 supplement 流程）。"""

    supplement_input: NotRequired[str]
    """补充请求的原始输入。"""

    plan_approval_kind: NotRequired[str]
    """team.plan 审批类型（如 plan_approval）。"""

    plan_content: NotRequired[str]
    """team.plan 审批内容。"""

    plan_language: NotRequired[str]
    """team.plan 审批语言（cn / en）。"""

    approval_schema: NotRequired[str]
    """审批 schema（evolution interrupt 场景）。"""

    evolution_meta: NotRequired[dict]
    """进化元数据（evolution interrupt 场景）。"""

    activate_response: NotRequired[dict]
    """auto_harness 激活响应（{interaction_id, action, feedback}）。"""

    team: NotRequired[bool]
    """团队模式布尔标志。"""

    run: NotRequired[dict]
    """Run 上下文结构（cron / 定时任务场景由 Gateway 注入）。"""

    cron: NotRequired[dict]
    """定时任务信息（由 Gateway cron scheduler 注入）。"""
