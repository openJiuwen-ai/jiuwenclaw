# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""``/file-api/*`` 路由（FastAPI 版，行为对齐原 app_web 的 _handle_file_api_get/_post）。

文件上传用 ``UploadFile`` 取代已废弃的 ``cgi.FieldStorage``。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from jiuwenclaw.minio_upload import upload_base64_payload
from jiuwenclaw.utils import (
    get_multi_tenant_user_workspace_dir,
    get_service_root_dir,
)
from jiuwenclaw.webserver.common import WebRuntime, guess_content_type, is_path_under_allowed_root

_MD_EXT = {".md", ".mdx"}


def _is_markdown(p: Path) -> bool:
    return p.suffix.lower() in _MD_EXT


def _normalize_lang_suffix(name: str) -> str:
    """xxxx_zh.md / xxxx_en.md → xxxx.md（与原实现一致）。"""
    stem, _, suffix = name.rpartition(".")
    if suffix.lower() in ("md", "mdx"):
        low = stem.lower()
        if low.endswith("_zh") or low.endswith("_en"):
            stem = stem[:-3]
    return f"{stem}.{suffix}" if stem else name


def _generate_agent_data(_project_root: Path) -> None:
    """生成 agent/jiuwenclaw_workspace/agent-data.json（与原 _generate_agent_data 等价）。"""
    default_workspace = get_multi_tenant_user_workspace_dir("default", "default")
    if default_workspace is None:
        raise FileNotFoundError("default multi-tenant workspace not found")
    agent_root = (default_workspace / "agent").resolve()
    workspace_root = (agent_root / "jiuwenclaw_workspace").resolve()
    output_path = (workspace_root / "agent-data.json").resolve()
    root_folder_key = "__root__"
    if not agent_root.exists():
        raise FileNotFoundError("agent directory not found")
    if not agent_root.is_dir():
        raise NotADirectoryError("agent is not a directory")

    folder_data: dict[str, list[dict[str, Any]]] = {}
    seen_paths: dict[str, set[str]] = {}
    session_mtime_map: dict[str, float] = {}
    for entry in sorted(agent_root.rglob("*")):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        rel = entry.parent.relative_to(agent_root).as_posix()
        folder_key = root_folder_key if rel == "." else rel
        display_name = _normalize_lang_suffix(entry.name)
        display_path = (
            f"agent/{rel}/{display_name}".replace("/.", "/").replace("//", "/")
            if rel != "." else f"agent/{display_name}"
        )
        seen = seen_paths.setdefault(folder_key, set())
        if display_path in seen:
            continue
        seen.add(display_path)
        folder_data.setdefault(folder_key, []).append({
            "name": display_name, "path": display_path,
            "isMarkdown": entry.suffix.lower() in _MD_EXT,
        })
        if rel.startswith("sessions/") and entry.parent.exists():
            session_mtime_map[folder_key] = entry.parent.stat().st_mtime

    def sort_folder_key(item: tuple[str, list]) -> tuple[int, float | str, str]:
        fk = item[0]
        if fk.startswith("sessions/"):
            return (0, -session_mtime_map.get(fk, 0), fk)
        return (1, 0, fk)

    def sort_files(fk: str, files: list) -> list:
        if fk.startswith("sessions/"):
            return sorted(files, key=lambda it: -session_mtime_map.get(fk, 0))
        return sorted(files, key=lambda it: it["path"])

    sorted_data = {
        fk: sort_files(fk, files)
        for fk, files in sorted(folder_data.items(), key=sort_folder_key)
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sorted_data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_file_api_router(rt: WebRuntime, *, upload_only: bool = False) -> APIRouter:
    """构造 /file-api 路由。``upload_only`` 时只挂 POST /file-api/upload-obs（等价 --upload-api-only）。"""
    router = APIRouter()

    def _guard(target: Path) -> bool:
        return is_path_under_allowed_root(target, workspace_root=rt.workspace_root, logs_root=rt.logs_root)

    # ---- POST /file-api/upload-obs（upload-only 与全功能都有）----
    @router.post("/file-api/upload-obs")
    async def upload_obs(request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8") if raw else "{}")
        except json.JSONDecodeError:
            return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "invalid_payload"}, status_code=400)
        try:
            return JSONResponse(upload_base64_payload(payload), status_code=200)
        except Exception as exc:
            rt.logger.exception("[WebServer] MinIO upload failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    if upload_only:
        return router

    # ---- GET /file-api/list-markdown ----
    @router.get("/file-api/list-markdown")
    async def list_markdown(subdir: str = Query("", alias="dir")) -> JSONResponse:
        if not subdir:
            return JSONResponse({"error": "missing_dir"}, status_code=400)
        base_dir = get_multi_tenant_user_workspace_dir("default", "default")
        if base_dir is None:
            return JSONResponse({"error": "default_workspace_not_found"}, status_code=500)
        full_dir = (base_dir / subdir).resolve()
        if not _guard(full_dir):
            return JSONResponse({"error": "forbidden_dir"}, status_code=403)
        if not full_dir.exists() or not full_dir.is_dir():
            return JSONResponse({"files": []})
        files = [
            {"name": e.name, "path": str(e.relative_to(base_dir))}
            for e in sorted(full_dir.iterdir(), key=lambda p: p.name.lower())
            if e.is_file() and _is_markdown(e)
        ]
        return JSONResponse({"files": files})

    # ---- GET /file-api/list-files ----
    @router.get("/file-api/list-files")
    async def list_files(subdir: str = Query("", alias="dir")) -> JSONResponse:
        if not subdir:
            return JSONResponse({"error": "missing_dir"}, status_code=400)
        base_dir = get_multi_tenant_user_workspace_dir("default", "default")
        if base_dir is None:
            return JSONResponse({"error": "default_workspace_not_found"}, status_code=500)
        full_dir = (base_dir / subdir).resolve()
        if not _guard(full_dir):
            return JSONResponse({"error": "forbidden_dir"}, status_code=403)
        if not full_dir.exists() or not full_dir.is_dir():
            return JSONResponse({"files": []})
        entries = sorted(full_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        files = [{
            "name": e.name, "path": str(e.relative_to(base_dir)),
            "isMarkdown": _is_markdown(e) if e.is_file() else False,
            "isDirectory": e.is_dir(),
        } for e in entries]
        return JSONResponse({"files": files})

    # ---- GET /file-api/file-content ----
    @router.get("/file-api/file-content")
    async def get_file_content(path: str = "") -> Response:
        if not path:
            return JSONResponse({"error": "missing_file_path"}, status_code=400)
        norm = path.replace("\\", "/")
        if norm.startswith(".logs/") or norm.startswith("logs/"):
            base_dir = get_service_root_dir()
        else:
            base_dir = get_multi_tenant_user_workspace_dir("default", "default")
            if base_dir is None:
                return JSONResponse({"error": "default_workspace_not_found"}, status_code=500)
        full_path = (base_dir / path).resolve()
        if not _guard(full_path):
            return JSONResponse({"error": "forbidden_path"}, status_code=403)
        if not full_path.exists():
            if norm == "agent/jiuwenclaw_workspace/agent-data.json":
                try:
                    _generate_agent_data(base_dir)
                except Exception as exc:
                    rt.logger.exception("[WebServer] generate agent-data failed")
                    return JSONResponse({"error": "generate_failed", "detail": str(exc)}, status_code=500)
            if not full_path.exists():
                return JSONResponse({"error": "file_not_found", "fullPath": str(full_path)}, status_code=404)
        try:
            data = full_path.read_text(encoding="utf-8")
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return Response(content=data.encode("utf-8"), media_type="text/plain; charset=utf-8")

    # ---- GET /file-api/download ----
    @router.get("/file-api/download")
    async def download(token: str = "") -> Response:
        if not token:
            return JSONResponse({"error": "missing_token"}, status_code=400)
        try:
            from jiuwenclaw.agentserver.tools.web_file_download import validate_file_download_token
        except ImportError:
            return JSONResponse({"error": "download_module_unavailable"}, status_code=500)
        payload = validate_file_download_token(token)
        if payload is None:
            return JSONResponse({"error": "invalid_or_expired_token"}, status_code=403)
        file_path = payload.get("path", "")
        if not file_path or not os.path.isfile(file_path):
            return JSONResponse({"error": "file_not_found"}, status_code=404)
        file_name = os.path.basename(file_path)

        def _iter() -> Iterator[bytes]:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            _iter(),
            media_type=guess_content_type(file_name),
            headers={
                # RFC 5987：UTF-8 编码文件名，修复中文文件名下载（同步自 upstream 18bf4b92）
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name, safe='')}",
                "Content-Length": str(os.path.getsize(file_path)),
                "Cache-Control": "no-store",
            },
        )

    # ---- GET / POST /file-api/ws-debug-config ----
    @router.get("/file-api/ws-debug-config")
    async def get_ws_debug_config() -> JSONResponse:
        return JSONResponse({"wsDisableCompress": bool(rt.ws_disable_compress)})

    @router.post("/file-api/ws-debug-config")
    async def set_ws_debug_config(request: Request) -> JSONResponse:
        try:
            payload = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        val = payload.get("wsDisableCompress")
        if not isinstance(val, bool):
            return JSONResponse({"error": "invalid_ws_disable_compress"}, status_code=400)
        rt.ws_disable_compress = val
        rt.logger.info("[jiuwenclaw-web] ws disable compress updated: %s", val)
        return JSONResponse({"ok": True, "wsDisableCompress": val})

    # ---- POST /file-api/rebuild-agent-data ----
    @router.post("/file-api/rebuild-agent-data")
    async def rebuild_agent_data() -> JSONResponse:
        try:
            base_dir = get_multi_tenant_user_workspace_dir("default", "default")
            if base_dir is None:
                return JSONResponse({"error": "default_workspace_not_found"}, status_code=500)
            _generate_agent_data(base_dir)
        except Exception as exc:
            rt.logger.exception("[WebServer] rebuild agent-data failed")
            return JSONResponse({"error": "rebuild_failed", "detail": str(exc)}, status_code=500)
        return JSONResponse({"ok": True})

    # ---- POST /file-api/file-content（写 markdown）----
    @router.post("/file-api/file-content")
    async def write_file_content(request: Request) -> JSONResponse:
        try:
            payload = json.loads((await request.body()).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        req_path = payload.get("path")
        content = payload.get("content")
        if not isinstance(req_path, str) or not req_path.strip():
            return JSONResponse({"error": "missing_file_path"}, status_code=400)
        if not isinstance(content, str):
            return JSONResponse({"error": "missing_file_content"}, status_code=400)
        base_dir = get_multi_tenant_user_workspace_dir("default", "default")
        if base_dir is None:
            return JSONResponse({"error": "default_workspace_not_found"}, status_code=500)
        full_path = (base_dir / req_path).resolve()
        if not _guard(full_path):
            return JSONResponse({"error": "forbidden_path"}, status_code=403)
        if not _is_markdown(full_path):
            return JSONResponse({"error": "only_markdown_supported"}, status_code=400)
        if not full_path.exists():
            return JSONResponse({"error": "file_not_found"}, status_code=404)
        try:
            full_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"ok": True})

    # ---- POST /file-api/push（Gateway 反向推文件；UploadFile 取代 cgi）----
    @router.post("/file-api/push")
    async def push(
        file: UploadFile = File(...),
        session_id: str = Form("default"),
        filename: str = Form(""),
    ) -> JSONResponse:
        from jiuwenclaw.agentserver.tools.web_file_download import build_file_download_info
        name = filename or file.filename or "unnamed"
        received_dir = Path("./web_received_files")
        received_dir.mkdir(parents=True, exist_ok=True)
        local_path = received_dir / f"{int(time.time())}_{name}"
        try:
            with open(local_path, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            rt.logger.info("[WebServer] 接收文件推送: %s -> %s", name, local_path)
            info = build_file_download_info(file_path=str(local_path), file_name=name, session_id=session_id)
            return JSONResponse({
                "success": True,
                "file_path": str(local_path),
                "download_url": info["download_url"],
                "download_token": info["download_token"],
                "expires_at": info.get("expires_at"),
            })
        except Exception as exc:
            rt.logger.exception("[WebServer] 处理文件推送失败")
            return JSONResponse({"error": "push_failed", "detail": str(exc)}, status_code=500)

    return router
