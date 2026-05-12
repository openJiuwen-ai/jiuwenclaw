import asyncio
import json
import logging
import sys

import aiohttp


logger = logging.getLogger(__name__)


session_id = sys.argv[1] if len(sys.argv) > 1 else "vibeskill_c03e68cef9fe"


def _build_auto_answers(questions: list[dict]) -> list[list[str]]:
    """为 question.asked 自动选择每题第一个选项标签。"""
    answers: list[list[str]] = []
    for q in questions:
        options = q.get("options", []) if isinstance(q, dict) else []
        first_label = ""
        if options and isinstance(options[0], dict):
            first_label = str(options[0].get("label") or "")
        answers.append([first_label] if first_label else [])
    return answers


async def test():
    uri = f"ws://127.0.0.1:19003/api/v1/messages?sessionID={session_id}"
    logger.info("Connecting to %s", uri)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(uri) as ws:
                logger.info("connected, sessionId: %s", session_id)

                # 接收 server.connected
                msg = await ws.receive()
                logger.info("ack: %s", msg.data)

                # 发送 message.send
                send_msg = {
                    "type": "message.send",
                    "sessionID": session_id,
                    "parts": [{"type": "text", "text": "创建一个故事生成的skill"}],
                    "model": {"providerID": "llm_OpenAI", "modelID": "deepseek-v3-250324"},
                    "agent": "coder",
                }
                await ws.send_str(json.dumps(send_msg))
                logger.info("sent message.send")

                # 接收响应：idle 仅表示当前阶段空闲，不作为结束条件
                consecutive_timeouts = 0
                package_in_progress_seen = False
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=120)
                        consecutive_timeouts = 0
                        logger.info(
                            "recv: %s",
                            msg.data,
                        )
                        if msg.type == aiohttp.WSMsgType.CLOSED:
                            break
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue

                        data = json.loads(msg.data)
                        msg_type = str(data.get("type") or "")
                        props = data.get("properties", {}) if isinstance(data.get("properties"), dict) else {}

                        if msg_type == "todo.updated":
                            todos = props.get("todos", [])
                            if isinstance(todos, list):
                                for todo in todos:
                                    if not isinstance(todo, dict):
                                        continue
                                    todo_id = str(todo.get("id") or "").strip()
                                    todo_status = str(todo.get("status") or "").strip()
                                    if todo_id == "package" and todo_status == "in_progress":
                                        package_in_progress_seen = True
                                        break
                            continue

                        if msg_type == "session.status":
                            status = props.get("status", {}) if isinstance(props.get("status"), dict) else {}
                            status_type = str(status.get("type") or "").strip()
                            if status_type == "completed":
                                logger.info("received session.status completed, stop")
                                break

                        # SkillDev 澄清问题：自动回填答案，继续流程
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
                            logger.info("sent question.replied requestID=%s answers=%s", qid, answers)
                            continue

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
                            logger.info("sent review.replied id=%s accept=true", rid)
                            continue

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
                            logger.info("sent desc_optimize.replied id=%s accept=false", did)
                            continue

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
                            logger.info("sent test.replied id=%s accept=true", tid)
                            continue

                    except asyncio.TimeoutError:
                        consecutive_timeouts += 1
                        logger.warning(
                            "timeout waiting for message (%d/3), keep waiting",
                            consecutive_timeouts,
                        )
                        if consecutive_timeouts >= 3:
                            logger.warning("no message for a long time, stop listening")
                            break
                        
    except Exception as e:
        logger.error("error: %s", e)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(message)s",
    )
    asyncio.run(test())
