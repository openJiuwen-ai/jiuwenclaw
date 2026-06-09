# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

""" /recap 命令的 prompt 模板与常量 """

RECENT_MESSAGE_WINDOW = 30


def build_recap_prompt(memory: str | None, language: str = "en") -> str:
    """构建 /recap prompt

    Args:
        memory: Session memory 内容（broader context），可为 None。
        language: 语言偏好，"zh" 系列输出中文，"en" 系列输出英文。
    """
    memory_block = f"Session memory (broader context):\n{memory}\n\n" if memory else ""

    if language and language.lower().startswith("zh"):
        return (
            f"{memory_block}"
            "用户正在请求当前会话的快速回顾。"
            "用恰好1-3个短句来回答。"
            "首先说明高层任务——他们正在构建或调试什么，不要涉及实现细节。"
            "接下来：具体的下一步操作。"
            "跳过状态报告和提交记录。"
        )
    return (
        f"{memory_block}"
        "The user is requesting a quick recap of the current session. "
        "Write exactly 1-3 short sentences. "
        "Start by stating the high-level task — what they are building or debugging, not implementation details. "
        "Next: the concrete next step. "
        "Skip status reports and commit recaps."
    )