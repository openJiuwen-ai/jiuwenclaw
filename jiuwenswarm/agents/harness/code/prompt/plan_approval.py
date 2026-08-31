# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan approval definitions — templates and helpers.

Plan approval is text-only: ``exit_plan_mode`` tool_result shows the plan
plus a short marker; the next user message is interpreted by the server
(``_check_and_handle_pending_approval``) as approve or feedback.
"""

from __future__ import annotations

import re
from typing import Literal

# ── Legacy event type (wire compat only; no longer pushed to clients) ───

PLAN_APPROVAL_EVENT_TYPE = "plan.approval_required"

# Pushed to clients after the user approves and server restores normal mode.
PLAN_MODE_EXITED_EVENT_TYPE = "plan.mode_exited"

# ── Approve / Reject command prefixes ───────────────────────────────────
# The frontend sends a new user request with one of these as the query text
# after the user interacts with the approval dialog.

APPROVE_CMD_PREFIX = "plan.approve"
REJECT_CMD_PREFIX = "plan.reject"

# Set on request.params when the user explicitly approved the plan this turn.
# Consumed by ``_ensure_code_mode_state`` to allow plan → normal restoration.
PLAN_USER_APPROVED_FLAG = "_plan_user_approved"

PlanUserIntent = Literal["approve", "revise"]

# ── Pending approval marker (fallback — appended to exit_plan_mode tool_result) ──

_PENDING_APPROVAL_MARKER_CN = (
    "\n\n---\n"
    "**仍在规划模式**，等待你确认计划。直接在输入框回复：\n"
    "- 同意开始执行：回复「好」「可以」「按计划实现」等（批准后将退出规划模式）\n"
    "- 需要修改：直接说明修改意见"
)

_PENDING_APPROVAL_MARKER_EN = (
    "\n\n---\n"
    "**Still in plan mode** — reply in chat to review this plan:\n"
    '- To approve and start implementation: "ok", "approve", "implement the plan", etc.\n'
    "- To revise: describe what to change"
)

PENDING_APPROVAL_MARKER = {
    "cn": _PENDING_APPROVAL_MARKER_CN,
    "en": _PENDING_APPROVAL_MARKER_EN,
}

# ── Approved notification (injected on the NEXT request when user approves) ──

_APPROVED_NOTIFICATION_CN = (
    "\n\n<system-reminder>\n"
    "用户已批准你的计划。立即开始执行。\n"
    "你现在处于 normal 模式，可以编辑文件、运行命令、进行修改。\n"
    "## 已批准的计划：\n{plan_content}\n"
    "</system-reminder>"
)

_APPROVED_NOTIFICATION_EN = (
    "\n\n<system-reminder>\n"
    "User has approved your plan. Proceed with implementation.\n"
    "You are now in normal mode. You can edit files, run commands, and make changes.\n"
    "## Approved Plan:\n{plan_content}\n"
    "</system-reminder>"
)

APPROVED_NOTIFICATION = {
    "cn": _APPROVED_NOTIFICATION_CN,
    "en": _APPROVED_NOTIFICATION_EN,
}

# ── Feedback injection (injected on the NEXT request when user gives feedback) ──

_FEEDBACK_INJECTION_CN = (
    "\n\n<system-reminder>\n"
    "用户要求修订计划（尚未批准执行）。请只修改计划文件，不要实现产品代码。\n"
    "你仍处于 plan 模式：禁止编辑计划文件以外的任何文件，禁止运行写操作。\n"
    "修订完成后，再次调用 exit_plan_mode 提交审批。\n\n"
    "**用户修订意见：**\n{user_message}\n"
    "</system-reminder>"
)

_FEEDBACK_INJECTION_EN = (
    "\n\n<system-reminder>\n"
    "The user wants plan revisions (implementation is NOT approved yet).\n"
    "You are still in plan mode — edit ONLY the plan file. Do NOT implement product code.\n"
    "Once revised, call exit_plan_mode again to submit for approval.\n\n"
    "**User revision request:**\n{user_message}\n"
    "</system-reminder>"
)

FEEDBACK_INJECTION = {
    "cn": _FEEDBACK_INJECTION_CN,
    "en": _FEEDBACK_INJECTION_EN,
}

# ── Intent detection helpers ─────────────────────────────────────────────

_APPROVAL_KEYWORDS_CN = frozenset({
    "好", "可以", "批准", "同意", "行", "没问题", "通过",
    "嗯", "好的", "可以了", "就这样", "ok", "okay", "approve",
})
_APPROVAL_KEYWORDS_EN = frozenset({
    "ok", "okay", "approve", "yes", "yeah", "yep", "good",
    "looks good", "approved", "go ahead", "proceed",
})

_REJECT_PREFIXES_CN = ("不行", "不好", "不要", "别", "不对", "不可以", "不同意")
_REJECT_PREFIXES_EN = ("reject", "no,", "no ", "don't", "do not")

_REVISION_SUBSTRINGS = (
    "修改", "改一下", "改成", "要改", "调整", "补充", "添加", "增加", "删除", "换成",
    "重新", "细化", "重写", "不够", "缺少", "有问题", "不满意", "换一个",
    "revise", "change", "modify", "update the plan", "add more", "remove",
    "instead", "rather than", "should also", "missing", "redo",
)

_IMPLEMENT_PATTERNS = (
    re.compile(r"按.{0,6}计划.{0,4}(实现|执行|做)"),
    re.compile(r"按.{0,6}方案.{0,4}(实现|执行|做)"),
    re.compile(r"开始(实现|执行|写代码|干活|做)"),
    re.compile(r"(可以|去|动手|直接)(实现|执行|做|开工)"),
    re.compile(r"^(实现|执行|开工)吧?[。.!]?$"),
    re.compile(r"就这样(做|执行|实现)"),
    re.compile(r"implement(\s+the)?\s+plan", re.IGNORECASE),
    re.compile(r"(start|go ahead|proceed)\s+(with\s+)?(implement|implementation|execution|building)", re.IGNORECASE),
    re.compile(r"^implement(\s+it)?[.!?]?$", re.IGNORECASE),
    re.compile(r"^(execute|build|ship)(\s+it|\s+the\s+plan)?[.!?]?$", re.IGNORECASE),
    re.compile(r"let'?s\s+(implement|build|execute)", re.IGNORECASE),
)


def classify_plan_user_intent(user_message: str) -> PlanUserIntent:
    """Classify the user's response after ``exit_plan_mode``.

    Returns:
        ``"approve"`` when the user wants to exit plan mode and implement.
        ``"revise"`` when the user wants to change the plan only.
    """
    text = user_message.strip()
    if not text:
        return "revise"

    if text.startswith(APPROVE_CMD_PREFIX):
        return "approve"
    if text.startswith(REJECT_CMD_PREFIX):
        return "revise"

    lower = text.lower()

    for prefix in _REJECT_PREFIXES_CN:
        if text.startswith(prefix):
            return "revise"
    for prefix in _REJECT_PREFIXES_EN:
        if lower.startswith(prefix):
            return "revise"

    if _has_revision_intent(text, lower):
        return "revise"

    if _has_implementation_intent(text):
        return "approve"

    if _is_pure_approval(lower):
        return "approve"

    return "revise"


def is_user_approving(user_message: str) -> bool:
    """Return ``True`` when the user message approves the plan."""
    return classify_plan_user_intent(user_message) == "approve"


def is_direct_plan_implement_request(user_message: str) -> bool:
    """True when the user clearly asks to implement an existing plan via chat.

    Stronger than bare ``好``/``可以`` — used when there is no pending
    ``exit_plan_mode`` gate but a plan file already exists in plan mode.
    """
    text = user_message.strip()
    if not text:
        return False
    if text.startswith(APPROVE_CMD_PREFIX):
        return True
    if classify_plan_user_intent(text) != "approve":
        return False
    return _has_implementation_intent(text)


def _has_revision_intent(text: str, lower: str) -> bool:
    for kw in _REVISION_SUBSTRINGS:
        if kw in text or kw in lower:
            return True
    return False


def _has_implementation_intent(text: str) -> bool:
    for pattern in _IMPLEMENT_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_pure_approval(lower: str) -> bool:
    for kw in _APPROVAL_KEYWORDS_CN | _APPROVAL_KEYWORDS_EN:
        if lower == kw or lower.startswith(kw + " ") or lower.startswith(kw + "。"):
            return True

    if len(lower) <= 12:
        for prefix in ("好", "可以", "行", "ok", "yes", "approve", "go", "yep", "proceed"):
            if lower.startswith(prefix):
                return True

    return False


def extract_feedback_from_reject(user_message: str) -> str:
    """Extract feedback text from a rejection command.

    Handles formats like: ``plan.reject <feedback>`` or plain feedback text.

    Args:
        user_message: The full user message.

    Returns:
        The extracted feedback text, or the full message if no prefix found.
    """
    text = user_message.strip()
    if text.startswith(REJECT_CMD_PREFIX):
        rest = text[len(REJECT_CMD_PREFIX):].strip()
        return rest if rest else text
    return text


__all__ = [
    "PLAN_APPROVAL_EVENT_TYPE",
    "PLAN_MODE_EXITED_EVENT_TYPE",
    "APPROVE_CMD_PREFIX",
    "REJECT_CMD_PREFIX",
    "PLAN_USER_APPROVED_FLAG",
    "PlanUserIntent",
    "PENDING_APPROVAL_MARKER",
    "APPROVED_NOTIFICATION",
    "FEEDBACK_INJECTION",
    "classify_plan_user_intent",
    "is_direct_plan_implement_request",
    "is_user_approving",
    "extract_feedback_from_reject",
]
