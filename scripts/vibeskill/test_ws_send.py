import asyncio
import json
import logging
import sys

import aiohttp


logger = logging.getLogger(__name__)


session_id = sys.argv[1] if len(sys.argv) > 1 else "vibeskill_c03e68cef9fe"


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

                # 接收响应
                for i in range(20):
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=5)
                        logger.info(
                            "recv: %s",
                            msg.data,
                        )
                        if msg.type == aiohttp.WSMsgType.CLOSED:
                            break
                        # AI 响应结束
                        if '"processing": false' in msg.data:
                            logger.info("AI response complete")
                            break
                    except asyncio.TimeoutError:
                        logger.warning("timeout waiting for message")
                        break

    except Exception as e:
        logger.error("error: %s", e)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(message)s",
    )
    asyncio.run(test())
