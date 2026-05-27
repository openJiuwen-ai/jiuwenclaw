# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mock OpenAI-compatible LLM server for enterprise system tests.

Streaming mode emits one token every N seconds (default 2s): mock token1 .. mock tokenN.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_STREAM_TOKEN_COUNT = 20
DEFAULT_STREAM_TOKEN_INTERVAL_S = 2.0


async def _read_until(reader: asyncio.StreamReader, marker: bytes, *, limit: int = 1024 * 1024) -> bytes:
    buf = bytearray()
    while marker not in buf:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise ValueError("HTTP header too large")
    return bytes(buf)


async def _read_chunked_body(reader: asyncio.StreamReader) -> bytes:
    body = bytearray()
    while True:
        size_line = await reader.readline()
        if not size_line:
            break
        size_text = size_line.decode("ascii", errors="replace").strip().split(";", 1)[0]
        if not size_text:
            continue
        size = int(size_text, 16)
        if size == 0:
            await reader.readline()
            break
        body.extend(await reader.readexactly(size))
        await reader.readline()
    return bytes(body)


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], dict[str, Any]]:
    """Read a full HTTP/1.1 request (supports Content-Length and chunked body)."""
    header_blob = await _read_until(reader, b"\r\n\r\n")
    header_text, _, rest = header_blob.partition(b"\r\n\r\n")
    lines = header_text.decode("utf-8", errors="replace").split("\r\n")
    request_line = lines[0] if lines else ""
    parts = request_line.split(" ")
    method = parts[0] if parts else "GET"
    path = parts[1] if len(parts) > 1 else "/"

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    body = bytearray(rest)
    content_length = headers.get("content-length")
    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if content_length:
        need = int(content_length) - len(body)
        while need > 0:
            chunk = await reader.read(need)
            if not chunk:
                break
            body.extend(chunk)
            need -= len(chunk)
    elif "chunked" in transfer_encoding:
        if body:
            temp_reader = asyncio.StreamReader()
            temp_reader.feed_data(bytes(body))
            temp_reader.feed_eof()
            body = bytearray(await _read_chunked_body(temp_reader))
        else:
            body = bytearray(await _read_chunked_body(reader))

    payload: dict[str, Any] = {}
    body_text = bytes(body).decode("utf-8", errors="replace").strip()
    if body_text:
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON body (len=%s)", len(body_text))

    return method, path, headers, payload


def _wants_stream(headers: dict[str, str], payload: dict[str, Any]) -> bool:
    if payload.get("stream") is True:
        return True
    accept = headers.get("accept", "")
    return "text/event-stream" in accept.lower()


def _http_response(status: int, body: str, *, content_type: str = "application/json") -> bytes:
    encoded = body.encode("utf-8")
    return (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(encoded)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("utf-8") + encoded


def _sse_event(data: dict[str, Any] | str) -> bytes:
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data, ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


async def _stream_chat_completion(
    writer: asyncio.StreamWriter,
    model: str,
    *,
    token_count: int,
    token_interval_s: float,
) -> None:
    """Write SSE stream: mock token1 .. mock tokenN."""
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/event-stream\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    writer.write(headers.encode("utf-8"))
    await writer.drain()

    for i in range(1, token_count + 1):
        token = f"mock token{i}"
        chunk = {
            "id": "mock-chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None,
                }
            ],
        }
        writer.write(_sse_event(chunk))
        await writer.drain()
        logger.info("Streamed token: %s", token)
        if i < token_count and token_interval_s > 0:
            await asyncio.sleep(token_interval_s)

    final_chunk = {
        "id": "mock-chatcmpl-stream",
        "object": "chat.completion.chunk",
        "created": 1234567890,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    writer.write(_sse_event(final_chunk))
    writer.write(_sse_event("[DONE]"))
    await writer.drain()


async def _handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    token_count: int,
    token_interval_s: float,
) -> None:
    try:
        method, path, headers, payload = await _read_http_request(reader)
        stream = _wants_stream(headers, payload)
        logger.info(
            "Request: %s %s stream=%s body_bytes=%s accept=%s",
            method,
            path,
            payload.get("stream"),
            headers.get("content-length", "?"),
            headers.get("accept", ""),
        )

        if method == "GET" and path in ("/health", "/v1/models"):
            body = json.dumps({"status": "ok", "object": "list", "data": []})
            writer.write(_http_response(200, body))
            await writer.drain()
            return

        if method == "POST" and path.rstrip("/") == "/v1/chat/completions":
            model = str(payload.get("model") or "mock-model")
            if stream:
                await _stream_chat_completion(
                    writer,
                    model,
                    token_count=token_count,
                    token_interval_s=token_interval_s,
                )
                return

            content = " ".join(f"mock token{i}" for i in range(1, token_count + 1))
            logger.info("Non-stream response content: %s", content[:120])
            response = {
                "id": "mock-chatcmpl-123",
                "object": "chat.completion",
                "created": 1234567890,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            }
            body = json.dumps(response, ensure_ascii=False)
            writer.write(_http_response(200, body))
            await writer.drain()
            return

        writer.write(_http_response(404, json.dumps({"error": "not found"})))
        await writer.drain()
    except Exception as exc:
        logger.exception("Error handling request: %s", exc)
        writer.write(_http_response(500, json.dumps({"error": str(exc)})))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def main(port: int, *, token_count: int, token_interval_s: float) -> None:
    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_request(
            reader,
            writer,
            token_count=token_count,
            token_interval_s=token_interval_s,
        )

    server = await asyncio.start_server(_handler, "127.0.0.1", port)
    addr = server.sockets[0].getsockname()
    logger.info(
        "Mock LLM server listening on http://%s:%d (tokens=%d interval=%ss)",
        addr[0],
        addr[1],
        token_count,
        token_interval_s,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock OpenAI LLM server for enterprise E2E tests")
    parser.add_argument("--port", type=int, default=19999, help="HTTP port")
    parser.add_argument(
        "--stream-token-count",
        type=int,
        default=DEFAULT_STREAM_TOKEN_COUNT,
        help="Number of SSE tokens in streaming mode",
    )
    parser.add_argument(
        "--stream-token-interval",
        type=float,
        default=DEFAULT_STREAM_TOKEN_INTERVAL_S,
        help="Seconds between SSE tokens in streaming mode",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            args.port,
            token_count=max(1, args.stream_token_count),
            token_interval_s=max(0.0, args.stream_token_interval),
        )
    )
