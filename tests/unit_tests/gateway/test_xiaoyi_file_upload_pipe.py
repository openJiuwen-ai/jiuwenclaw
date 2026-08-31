# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""XYFileUploadService 上传链路单测：

- np:// 形态：phase1/3（prepare/completeAndQuery）经命名管道 transport 成功，
  且携带 Authorization: Bearer <uploadToken>（密钥包注入）；
- uploadToken 缺失时不带 Authorization（兼容旧版桌面零鉴权代理）；
- http://127.0.0.1 形态回归：aiohttp 路径行为不变（loopback 同样补令牌）；
- phase2（OBS 签名 URL 直传）始终走 http，不注入令牌。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import jiuwenswarm.common.secrets_bootstrap as secrets_bootstrap
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    XYFileUploadService,
)


class _PipeHttpStub:
    """serve_pipe 上的极简 HTTP/1.1 服务：记录请求，按 responder 应答。"""

    def __init__(self, responder) -> None:
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        self._responder = responder
        self.path = rf"\\.\pipe\claw-test-upload-{os.getpid()}-{id(self) & 0xFFFFF:x}"
        self.server = None

    async def __aenter__(self) -> "_PipeHttpStub":
        from jiuwenswarm.common.np_transport import serve_pipe

        self.server = await serve_pipe(self.path, self._handle)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.server.stop()

    async def _handle(self, stream) -> None:
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = await stream.read()
            if not chunk:
                return
            buf.extend(chunk)
        head, _, rest = bytes(buf).partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        body = rest
        remaining = int(headers.get("content-length", "0")) - len(body)
        while remaining > 0:
            chunk = await stream.read(remaining)
            if not chunk:
                break
            body += chunk
            remaining -= len(chunk)
        request_line = lines[0].decode("latin-1")
        self.requests.append((request_line, headers, bytes(body)))
        status, resp_headers, resp_body = self._responder(request_line, headers, bytes(body))
        payload = f"HTTP/1.1 {status} OK\r\n".encode()
        for k, v in resp_headers.items():
            payload += f"{k}: {v}\r\n".encode()
        payload += b"\r\n" + resp_body
        await stream.write(payload)


class _HttpStub:
    """aiohttp 本机 HTTP 服务：prepare / OBS PUT / completeAndQuery 全三段。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        self.runner = None
        self.base_url = ""

    async def __aenter__(self) -> "_HttpStub":
        from aiohttp import web

        async def prepare(request: web.Request) -> web.Response:
            body = await request.read()
            self.requests.append(
                (
                    "POST /osms/v1/file/manager/prepare",
                    {k.lower(): v for k, v in request.headers.items()},
                    body,
                )
            )
            return web.json_response(
                {
                    "code": "0",
                    "objectId": "obj-1",
                    "draftId": "draft-1",
                    "uploadInfos": [
                        {
                            "url": f"{self.base_url}/obs-put",
                            "method": "PUT",
                            "headers": {"x-obs-sig": "s"},
                        }
                    ],
                }
            )

        async def obs_put(request: web.Request) -> web.Response:
            body = await request.read()
            self.requests.append(
                ("PUT /obs-put", {k.lower(): v for k, v in request.headers.items()}, body)
            )
            return web.Response(status=200)

        async def complete(request: web.Request) -> web.Response:
            body = await request.read()
            self.requests.append(
                (
                    "POST /osms/v1/file/manager/completeAndQuery",
                    {k.lower(): v for k, v in request.headers.items()},
                    body,
                )
            )
            return web.json_response(
                {"code": "0", "fileDetailInfo": {"url": "https://files.example.com/obj-1"}}
            )

        app = web.Application()
        app.router.add_post("/osms/v1/file/manager/prepare", prepare)
        app.router.add_put("/obs-put", obs_put)
        app.router.add_post("/osms/v1/file/manager/completeAndQuery", complete)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        server = site._server  # noqa: SLF001 - 取 ephemeral 端口
        port = server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc) -> None:
        await self.runner.cleanup()


def _osms_responder(http_stub: _HttpStub):
    """管道侧 responder：prepare 回指 http_stub 的 PUT 地址，complete 回 code 0。"""

    def responder(request_line: str, headers: dict[str, str], body: bytes):
        if request_line.startswith("POST /osms/v1/file/manager/prepare"):
            payload = {
                "code": "0",
                "objectId": "obj-1",
                "draftId": "draft-1",
                "uploadInfos": [
                    {
                        "url": f"{http_stub.base_url}/obs-put",
                        "method": "PUT",
                        "headers": {"x-obs-sig": "s"},
                    }
                ],
            }
        elif request_line.startswith("POST /osms/v1/file/manager/completeAndQuery"):
            payload = {"code": "0"}
        else:  # pragma: no cover - 不应出现其他路径
            return 404, {"Content-Length": "0"}, b""
        data = json.dumps(payload).encode()
        return 200, {"Content-Type": "application/json", "Content-Length": str(len(data))}, data

    return responder


@pytest.mark.skipif(sys.platform != "win32", reason="命名管道仅 Windows")
class TestXYFileUploadServiceNamedPipe:
    @pytest.mark.asyncio
    async def test_upload_over_pipe_carries_bearer(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-abc"})
        async with _HttpStub() as http_stub:
            async with _PipeHttpStub(_osms_responder(http_stub)) as pipe_stub:
                pipe_name = pipe_stub.path.rsplit("\\", 1)[-1]
                file_path = tmp_path / "hello.txt"
                file_path.write_bytes(b"hello osms")
                async with XYFileUploadService(f"np://{pipe_name}", "", "1001") as svc:
                    object_id = await svc.upload_file(str(file_path))

        assert object_id == "obj-1"
        # phase1/3 均经管道，携带 Bearer + 常规头
        assert [r[0] for r in pipe_stub.requests] == [
            "POST /osms/v1/file/manager/prepare HTTP/1.1",
            "POST /osms/v1/file/manager/completeAndQuery HTTP/1.1",
        ]
        for _line, headers, _body in pipe_stub.requests:
            assert headers.get("authorization") == "Bearer tok-abc"
            assert headers.get("x-uid") == "1001"
            assert headers.get("x-request-from") == "openclaw"
        # phase2 OBS 直传仍走 http（aiohttp），不注入令牌
        assert [r[0] for r in http_stub.requests] == ["PUT /obs-put"]
        put_headers = http_stub.requests[0][1]
        assert "authorization" not in put_headers
        assert put_headers.get("x-obs-sig") == "s"
        assert http_stub.requests[0][2] == b"hello osms"

    @pytest.mark.asyncio
    async def test_upload_over_pipe_without_token_omits_header(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {})
        async with _HttpStub() as http_stub:
            async with _PipeHttpStub(_osms_responder(http_stub)) as pipe_stub:
                pipe_name = pipe_stub.path.rsplit("\\", 1)[-1]
                file_path = tmp_path / "hello.txt"
                file_path.write_bytes(b"hello osms")
                async with XYFileUploadService(f"np://{pipe_name}", "", "1001") as svc:
                    object_id = await svc.upload_file(str(file_path))

        assert object_id == "obj-1"
        assert len(pipe_stub.requests) == 2
        for _line, headers, _body in pipe_stub.requests:
            assert "authorization" not in headers

    @pytest.mark.asyncio
    async def test_upload_over_pipe_prepare_error_returns_none(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-abc"})

        def responder(request_line, headers, body):
            data = json.dumps({"code": "500", "desc": "boom"}).encode()
            return 200, {"Content-Type": "application/json", "Content-Length": str(len(data))}, data

        async with _PipeHttpStub(responder) as pipe_stub:
            pipe_name = pipe_stub.path.rsplit("\\", 1)[-1]
            file_path = tmp_path / "hello.txt"
            file_path.write_bytes(b"hello osms")
            async with XYFileUploadService(f"np://{pipe_name}", "", "1001") as svc:
                assert await svc.upload_file(str(file_path)) is None


class TestXYFileUploadServiceHttpRegression:
    """http(s):// 形态回归：aiohttp 路径行为不变（loopback 本地代理同样补令牌）。"""

    @pytest.mark.asyncio
    async def test_upload_over_http_loopback_with_token(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-abc"})
        async with _HttpStub() as http_stub:
            file_path = tmp_path / "hello.txt"
            file_path.write_bytes(b"hello osms")
            async with XYFileUploadService(http_stub.base_url, "k", "1001") as svc:
                object_id = await svc.upload_file(str(file_path))

        assert object_id == "obj-1"
        assert [r[0] for r in http_stub.requests] == [
            "POST /osms/v1/file/manager/prepare",
            "PUT /obs-put",
            "POST /osms/v1/file/manager/completeAndQuery",
        ]
        prepare_headers = http_stub.requests[0][1]
        assert prepare_headers.get("authorization") == "Bearer tok-abc"
        assert prepare_headers.get("x-api-key") == "k"
        assert prepare_headers.get("x-uid") == "1001"
        # phase2 不注入令牌（仅用 prepare 下发的 upload headers）
        put_headers = http_stub.requests[1][1]
        assert "authorization" not in put_headers
        assert put_headers.get("x-obs-sig") == "s"
        assert http_stub.requests[1][2] == b"hello osms"

    @pytest.mark.asyncio
    async def test_upload_over_http_loopback_without_token(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {})
        async with _HttpStub() as http_stub:
            file_path = tmp_path / "hello.txt"
            file_path.write_bytes(b"hello osms")
            async with XYFileUploadService(http_stub.base_url, "", "1001") as svc:
                object_id = await svc.upload_file(str(file_path))

        assert object_id == "obj-1"
        for _line, headers, _body in http_stub.requests:
            assert "authorization" not in headers


@pytest.mark.skipif(sys.platform != "win32", reason="命名管道仅 Windows")
class TestUploadLocalFilePublicUrlNamedPipe:
    """file_upload_helpers（send_html_card 等工具的共用 seam）np:// 形态。"""

    @pytest.mark.asyncio
    async def test_upload_over_pipe_carries_bearer(self, monkeypatch, tmp_path) -> None:
        import aiohttp

        from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.file_upload_helpers import (
            XiaoyiObsUploadConfig,
            upload_local_file_public_url,
        )

        monkeypatch.setattr(secrets_bootstrap, "_SECRETS", {"uploadToken": "tok-xyz"})

        async with _HttpStub() as http_stub:

            def responder(request_line, headers, body):
                if request_line.startswith("POST /osms/v1/file/manager/prepare"):
                    payload = {
                        "code": "0",
                        "objectId": "obj-9",
                        "draftId": "draft-9",
                        "uploadInfos": [
                            {
                                "url": f"{http_stub.base_url}/obs-put",
                                "method": "PUT",
                                "headers": {},
                            }
                        ],
                    }
                else:
                    payload = {
                        "code": "0",
                        "fileDetailInfo": {"url": "https://files.example.com/obj-9"},
                    }
                data = json.dumps(payload).encode()
                return (
                    200,
                    {"Content-Type": "application/json", "Content-Length": str(len(data))},
                    data,
                )

            async with _PipeHttpStub(responder) as pipe_stub:
                pipe_name = pipe_stub.path.rsplit("\\", 1)[-1]
                file_path = tmp_path / "card.html"
                file_path.write_bytes(b"<html>hi</html>")
                cfg = XiaoyiObsUploadConfig(
                    base_url=f"np://{pipe_name}", api_key="", uid="1001"
                )
                async with aiohttp.ClientSession() as session:
                    url = await upload_local_file_public_url(session, cfg, str(file_path))

        assert url == "https://files.example.com/obj-9"
        assert [r[0] for r in pipe_stub.requests] == [
            "POST /osms/v1/file/manager/prepare HTTP/1.1",
            "POST /osms/v1/file/manager/completeAndQuery HTTP/1.1",
        ]
        for _line, headers, _body in pipe_stub.requests:
            assert headers.get("authorization") == "Bearer tok-xyz"
        # phase2 OBS 直传走传入的 aiohttp session，不注入令牌
        assert [r[0] for r in http_stub.requests] == ["PUT /obs-put"]
        assert "authorization" not in http_stub.requests[0][1]


class TestImageReadingSseParsing:
    """image_reading 重构出的 SSE 解析（aiohttp/httpx 两路共用）行为锁定。"""

    @pytest.mark.asyncio
    async def test_extract_caption_from_sse(self) -> None:
        from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.image_reading_tool import (
            _extract_caption_from_sse,
        )

        def sse_frame(caption: str) -> bytes:
            payload = {
                "abilityInfos": [
                    {
                        "actionExecutorResult": {
                            "reply": {"streamInfo": {"streamContent": caption}}
                        }
                    }
                ]
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n".encode()

        chunks = [
            sse_frame("一只"),
            b"data: [DONE]\n",
            # 跨 chunk 拆分的行
            sse_frame("一只猫")[:10],
            sse_frame("一只猫")[10:],
        ]

        async def aiter():
            for c in chunks:
                yield c

        assert await _extract_caption_from_sse(aiter()) == "一只猫"

    @pytest.mark.asyncio
    async def test_extract_caption_empty(self) -> None:
        from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.image_reading_tool import (
            _extract_caption_from_sse,
        )

        async def aiter():
            yield b"data: [DONE]\n"

        assert await _extract_caption_from_sse(aiter()) == ""
