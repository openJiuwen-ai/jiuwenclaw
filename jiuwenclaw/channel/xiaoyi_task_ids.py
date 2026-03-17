"""Helpers for allocating proactive Xiaoyi task ids."""


def build_next_proactive_task_id(session_id: str, last_task_id: str) -> str:
    """Allocate the next Xiaoyi task id for proactive pushes.

    Xiaoyi inbound task ids currently follow ``<session_id>&<seq>``.
    Reusing the last completed task id for a proactive cron push causes the
    platform to treat the message as an update to an already-finished turn.
    """

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return ""

    prefix = f"{normalized_session_id}&"
    normalized_last_task_id = str(last_task_id or "").strip()
    if normalized_last_task_id.startswith(prefix):
        suffix = normalized_last_task_id[len(prefix):].strip()
        if suffix.isdigit():
            return f"{normalized_session_id}&{int(suffix) + 1}"

    return f"{normalized_session_id}&1"
