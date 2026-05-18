"""SkillCreate 端到端 WebSocket 联调：message.send → skilldev.chat，自动应答确认事件。

用法（需已创建 SkillCreate session，通常由 test_vibeskill_whole.sh 调用）::

    uv run python scripts/vibeskill/test_ws_whole.py <sessionID>

结束条件：收到 ``session.status`` 且 ``status.type == completed``（对应 skilldev.completed）。
**当且仅当**收到 ``task.completed`` 时调用 ``POST /session/{id}/export``。
``question.replied`` 走 ``skilldev.user_answer``；review/desc_optimize/test 走 ``skilldev.respond``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import aiohttp


logger = logging.getLogger(__name__)

session_id = sys.argv[1] if len(sys.argv) > 1 else ""
HTTP_BASE = os.environ.get("VIBESKILL_HTTP_BASE", "http://127.0.0.1:19002")


_SKIP_OPTION_LABELS = frozenset({"继续迭代", "继续改进", "是，进行评估测试"})
_PREFER_OPTION_SUBSTRINGS = (
    "否",
    "不进行评估",
    "不进行",
    "跳过",
    "下一步",
    "进入下一",
    "move on",
    "完成",
    "结束",
    "不再",
    "接受",
    "确认",
    "打包",
    "导出",
)


def _should_skip_auto_option(label: str) -> bool:
    text = label.strip()
    if not text:
        return True
    if text in _SKIP_OPTION_LABELS:
        return True
    lower = text.lower()
    if "继续迭代" in text or "继续改进" in text:
        return True
    if "continue" in lower and ("improv" in lower or "iterat" in lower):
        return True
    if "评估测试" in text or "进行评估" in text:
        # Keep「否 / 不…」decline options; skip affirmative eval choices.
        if text.startswith("否") or text.startswith("不") or "跳过" in text:
            return False
        return True
    return False


def _pick_option_label(options: list) -> str:
    """Pick an option for automated E2E; skip「继续迭代」and eval-test consent."""
    labels: list[str] = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        label = str(opt.get("label") or "").strip()
        if label:
            labels.append(label)
    if not labels:
        return ""

    for prefer in _PREFER_OPTION_SUBSTRINGS:
        for label in labels:
            if _should_skip_auto_option(label):
                continue
            if prefer.lower() in label.lower():
                return label

    for label in labels:
        if not _should_skip_auto_option(label):
            return label

    # All options are skip-only (e.g. single「继续迭代」): use last option if any.
    return labels[-1]


def _build_auto_answers(questions: list[dict]) -> list[list[str]]:
    """为 question.asked 自动选择选项（跳过「继续迭代」「进行评估测试」等）。"""
    answers: list[list[str]] = []
    for q in questions:
        options = q.get("options", []) if isinstance(q, dict) else []
        label = _pick_option_label(options if isinstance(options, list) else [])
        answers.append([label] if label else [])
    return answers


async def _send_auto_replies(ws: aiohttp.ClientWebSocketResponse, msg_type: str, props: dict) -> bool:
    """对 question/review/desc_optimize/test.asked 自动回填；已发送则返回 True。"""
    if msg_type == "question.asked":
        qid = str(props.get("id") or "")
        questions = props.get("questions", [])
        answers = _build_auto_answers(questions if isinstance(questions, list) else [])
        reply_msg = {
            "type": "question.replied",
            "properties": {
                "sessionID": session_id,
                "requestID": qid,
                "answers": answers,
            },
        }
        await ws.send_str(json.dumps(reply_msg, ensure_ascii=False))
        logger.info(
            "sent question.replied (skilldev.user_answer) requestID=%s answers=%s",
            qid,
            answers,
        )
        return True

    if msg_type == "review.asked":
        rid = str(props.get("id") or "")
        reply_msg = {
            "type": "review.replied",
            "properties": {
                "id": rid,
                "sessionID": session_id,
                "accept": True,
            },
        }
        await ws.send_str(json.dumps(reply_msg, ensure_ascii=False))
        logger.info("sent review.replied (skilldev.respond) id=%s accept=true", rid)
        return True

    if msg_type == "desc_optimize.asked":
        did = str(props.get("id") or "")
        reply_msg = {
            "type": "desc_optimize.replied",
            "properties": {
                "id": did,
                "sessionID": session_id,
                "accept": False,
            },
        }
        await ws.send_str(json.dumps(reply_msg, ensure_ascii=False))
        logger.info("sent desc_optimize.replied (skilldev.respond) id=%s accept=false", did)
        return True

    if msg_type == "test.asked":
        tid = str(props.get("id") or "")
        reply_msg = {
            "type": "test.replied",
            "properties": {
                "id": tid,
                "sessionID": session_id,
                "accept": True,
            },
        }
        await ws.send_str(json.dumps(reply_msg, ensure_ascii=False))
        logger.info("sent test.replied (skilldev.respond) id=%s accept=true", tid)
        return True

    return False


async def _export_skill(http_session: aiohttp.ClientSession) -> dict:
    """收到 task.completed 后导出 skill 产物。"""
    url = f"{HTTP_BASE}/api/v1/session/{session_id}/export"
    async with http_session.post(url, json={}) as resp:
        body = await resp.json()
        if resp.status not in (200, 201):
            raise AssertionError(f"export failed HTTP {resp.status}: {body}")
        logger.info("export response: %s", json.dumps(body, ensure_ascii=False))
        return body if isinstance(body, dict) else {}


async def test() -> None:
    if not str(session_id or "").strip():
        raise SystemExit("usage: test_ws_whole.py <sessionID>")

    uri = f"ws://127.0.0.1:19003/api/v1/messages?sessionID={session_id}"
    logger.info("Connecting to %s", uri)

    pipeline_completed = False
    task_completed_seen = False
    export_done = False
    recv_timeout = 300.0
    max_consecutive_timeouts = 12
    consecutive_timeouts = 0

    async with aiohttp.ClientSession() as http_session:
        async with http_session.ws_connect(uri) as ws:
            logger.info("connected, sessionId: %s", session_id)

            msg = await ws.receive()
            logger.info("ack: %s", msg.data)

            send_msg = {
                "type": "message.send",
                "sessionID": session_id,
                "parts": [{"type": "text", "text": "创建一个大数乘法的skill"}],
                "model": {"providerID": "llm_OpenAI", "modelID": "deepseek-v3-250324"},
                "agent": "coder",
            }
            await ws.send_str(json.dumps(send_msg, ensure_ascii=False))
            logger.info("sent message.send (maps to skilldev.chat)")

            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=recv_timeout)
                    consecutive_timeouts = 0
                except asyncio.TimeoutError:
                    consecutive_timeouts += 1
                    logger.warning(
                        "timeout waiting for message (%d/%d)",
                        consecutive_timeouts,
                        max_consecutive_timeouts,
                    )
                    if consecutive_timeouts >= max_consecutive_timeouts:
                        raise AssertionError(
                            "stopped: too many consecutive receive timeouts "
                            f"(pipeline_completed={pipeline_completed})"
                        )

                if msg.type == aiohttp.WSMsgType.CLOSED:
                    raise AssertionError("WebSocket closed before session.status completed")

                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue

                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.debug("skip non-json frame")
                    continue

                msg_type = str(data.get("type") or "")
                props = data.get("properties") if isinstance(data.get("properties"), dict) else {}

                if msg_type == "todo.updated":
                    continue

                if msg_type == "task.error":
                    err = str(props.get("error") or "unknown")
                    raise AssertionError(f"task.error: {err}")

                if msg_type == "task.completed":
                    task_completed_seen = True
                    logger.info("received task.completed")
                    if not export_done:
                        await _export_skill(http_session)
                        export_done = True
                    continue

                if msg_type == "session.status":
                    status = props.get("status") if isinstance(props.get("status"), dict) else {}
                    status_type = str(status.get("type") or "").strip()
                    if status_type == "busy":
                        logger.info("session.status busy")
                        continue
                    if status_type == "completed":
                        pipeline_completed = True
                        logger.info("received session.status completed, stop")
                        break

                if await _send_auto_replies(ws, msg_type, props):
                    continue

    if not task_completed_seen:
        raise AssertionError("did not receive task.completed")
    if not export_done:
        raise AssertionError("export was not triggered after task.completed")
    if not pipeline_completed:
        raise AssertionError("did not receive session.status completed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    try:
        asyncio.run(test())
        logger.info("PASS: SkillCreate pipeline completed")
    except AssertionError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.exception("unexpected error: %s", exc)
        sys.exit(1)
