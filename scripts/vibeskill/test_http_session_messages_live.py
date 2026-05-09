"""VibeSkill live: 模拟对话并导出 history JSON。

运行方式：
- 脚本模式: python scripts/vibeskill/test_http_session_messages_live.py
- pytest 模式: VIBESKILL_LIVE_TEST=1 pytest -q scripts/vibeskill/test_http_session_messages_live.py
"""
import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
import pytest

logger = logging.getLogger(__name__)
HTTP_BASE = os.environ.get("VIBESKILL_HTTP_BASE", "http://198.18.0.1:19002")
WS_BASE = os.environ.get("VIBESKILL_WS_BASE", "ws://198.18.0.1:19003/api/v1/messages")


def _build_auto_answers(questions: list[dict]) -> list[list[str]]:
    answers: list[list[str]] = []
    for question in questions:
        options = question.get("options", []) if isinstance(question, dict) else []
        if options and isinstance(options[0], dict):
            answers.append([str(options[0].get("label") or "")])
        else:
            answers.append([])
    return answers


async def _drive_dialog_and_fetch_history() -> tuple[str, list[str], dict]:
    async with aiohttp.ClientSession() as session:
        create_url = f"{HTTP_BASE}/api/v1/session"
        async with session.post(create_url, json={"mode": "SkillCreate"}) as response:
            result = await response.json()
            logger.info("创建会话响应: %s", json.dumps(result, ensure_ascii=False))
            session_id = str(result.get("sessionID") or "").strip()

    if not session_id:
        raise AssertionError("创建会话失败：返回中没有 sessionID")

    ws_types: list[str] = []
    ws_url = f"{WS_BASE}?sessionID={session_id}"
    logger.info("连接到 WebSocket: %s", ws_url)

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            hello_msg = await ws.receive(timeout=30)
            if hello_msg.type == aiohttp.WSMsgType.TEXT:
                hello_data = json.loads(hello_msg.data)
                ws_types.append(str(hello_data.get("type") or ""))

            await ws.send_str(json.dumps({
                "type": "message.send",
                "sessionID": session_id,
                "parts": [{"type": "text", "text": "创建一个测试skill，名字叫demo-history-check"}],
                "agent": "coder",
            }, ensure_ascii=False))

            for _ in range(60):
                incoming = await ws.receive(timeout=20)
                if incoming.type != aiohttp.WSMsgType.TEXT:
                    continue

                data = json.loads(incoming.data)
                message_type = str(data.get("type") or "")
                ws_types.append(message_type)
                props = data.get("properties", {}) if isinstance(data.get("properties"), dict) else {}

                if message_type == "question.asked":
                    request_id = str(props.get("id") or "")
                    await ws.send_str(json.dumps({
                        "type": "question.replied",
                        "properties": {
                            "sessionID": session_id,
                            "requestID": request_id,
                            "answers": _build_auto_answers(props.get("questions", [])),
                        },
                    }, ensure_ascii=False))

                if message_type == "review.asked":
                    request_id = str(props.get("id") or "")
                    await ws.send_str(json.dumps({
                        "type": "review.replied",
                        "properties": {"id": request_id, "sessionID": session_id, "accept": True},
                    }, ensure_ascii=False))

                if message_type == "desc_optimize.asked":
                    request_id = str(props.get("id") or "")
                    await ws.send_str(json.dumps({
                        "type": "desc_optimize.replied",
                        "properties": {"id": request_id, "sessionID": session_id, "accept": False},
                    }, ensure_ascii=False))

                if message_type == "test.asked":
                    request_id = str(props.get("id") or "")
                    await ws.send_str(json.dumps({
                        "type": "test.replied",
                        "properties": {"id": request_id, "sessionID": session_id, "accept": True},
                    }, ensure_ascii=False))

                if message_type in ("task.completed", "task.error"):
                    break

    async with aiohttp.ClientSession() as session:
        history_url = f"{HTTP_BASE}/api/v1/session/{session_id}/messages"
        async with session.get(history_url) as response:
            history_json = await response.json()

    return session_id, ws_types, history_json


def _dump_json_artifacts(session_id: str, ws_types: list[str], history_json: dict) -> tuple[Path, Path]:
    output_dir = Path(__file__).parent
    history_path = output_dir / f"messages_{session_id}.json"
    types_path = output_dir / f"messages_{session_id}_types.json"

    history_path.write_text(
        json.dumps(history_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    messages = history_json.get("messages") if isinstance(history_json, dict) else []
    messages = messages if isinstance(messages, list) else []
    history_types = [str(msg.get("type") or "") for msg in messages if isinstance(msg, dict)]

    summary = {
        "session_id": session_id,
        "total": history_json.get("total") if isinstance(history_json, dict) else 0,
        "ws_types": ws_types,
        "types": history_types,
        "has_message_send": "message.send" in history_types,
        "has_question_asked": "question.asked" in history_types,
        "has_question_replied": "question.replied" in history_types,
        "has_message_updated": "message.updated" in history_types,
    }
    types_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return history_path, types_path


def _assert_history_contract(history_json: dict) -> None:
    assert isinstance(history_json, dict)
    messages = history_json.get("messages") or []
    assert isinstance(messages, list)
    types = [str(msg.get("type") or "") for msg in messages if isinstance(msg, dict)]
    assert "message.send" in types
    assert "message.updated" in types
    assert not any(item in ("message.part.updated", "message.part.delta") for item in types)


async def main():
    session_id, ws_types, history_json = await _drive_dialog_and_fetch_history()
    history_path, types_path = _dump_json_artifacts(session_id, ws_types, history_json)
    _assert_history_contract(history_json)
    logger.info("会话ID: %s", session_id)
    logger.info("history JSON: %s", history_path)
    logger.info("types JSON: %s", types_path)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("VIBESKILL_LIVE_TEST") != "1",
    reason="set VIBESKILL_LIVE_TEST=1 to run live integration test",
)
async def test_http_session_messages_live_conversation():
    session_id, ws_types, history_json = await _drive_dialog_and_fetch_history()
    history_path, types_path = _dump_json_artifacts(session_id, ws_types, history_json)

    assert history_path.exists()
    assert types_path.exists()
    assert isinstance(ws_types, list)
    _assert_history_contract(history_json)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(message)s",
    )
    asyncio.run(main())
