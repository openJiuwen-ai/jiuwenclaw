# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

""" /recap 命令的 prompt 模板与常量 """

RECENT_MESSAGE_WINDOW = 30


def build_recap_prompt(memory: str | None) -> str:
    """构建 /recap prompt

    Args:
        memory: Session memory 内容（broader context），可为 None。
    """
    memory_block = f"Session memory (broader context):\n{memory}\n\n" if memory else ""
    return (
        f"{memory_block}"
        "The user is requesting a quick recap of the current session. "
        "Write exactly 1-3 short sentences. "
        "Start by stating the high-level task — what they are building or debugging, not implementation details. "
        "Next: the concrete next step. "
        "Skip status reports and commit recaps."
    )