# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillDevService — 无状态请求处理器.

设计要点：
- 无状态：不持有 Pipeline 对象，不做 Pipeline 生命周期管理
- 每次请求：StateStore 加载状态 → 创建 Pipeline → 执行 → checkpoint → 释放
- 路由层（Gateway）保证同一 task_id 的请求路由到同一实例，Service 无需关心

对外只暴露一个入口：handle(request) → AsyncIterator[AgentResponseChunk]

前端只需以下 method：
- skilldev.start     → 发起新任务
- skilldev.respond   → 统一确认（后端根据 task_id 当前阶段自动路由）
- skilldev.status    → 查状态 / 列任务
- skilldev.parse_skill → 导入本地 skill 压缩包并解压到工作区 skill/
- skilldev.download  → 下载产物
- skilldev.cancel    → 取消任务
- skilldev.file.list → 获取文件树
- skilldev.file.read → 读取文件内容
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from jiuwenclaw.schema.agent import AgentRequest, AgentResponseChunk
from jiuwenclaw.schema.message import ReqMethod
from jiuwenclaw.agentserver.skilldev.deps import SkillDevDeps
from jiuwenclaw.agentserver.skilldev.pipeline import SkillDevPipeline
from jiuwenclaw.agentserver.skilldev.schema import (
    SUSPENSION_POINTS,
    SkillDevEvent,
    SkillDevState,
    SkillDevStage,
)
from jiuwenclaw.agentserver.skilldev.common_utils import safe_extract_zip
from jiuwenclaw.agentserver.skilldev.stages.validate_stage import parse_skill_frontmatter
from jiuwenclaw.agentserver.skilldev.utils.upload_file_obs import UploadFileOSMS
from jiuwenclaw.agentserver.skilldev.utils.download_file_from_url import download_file


logger = logging.getLogger(__name__)

# method → handler 映射，避免 if/elif 链
_METHOD_DISPATCH = {
    ReqMethod.SKILLDEV_START: "_handle_start",
    ReqMethod.SKILLDEV_RESPOND: "_handle_respond",
    ReqMethod.SKILLDEV_STATUS: "_handle_status",
    ReqMethod.SKILLDEV_PARSE_SKILL: "_handle_parse_skill",
    ReqMethod.SKILLDEV_DOWNLOAD: "_handle_download",
    ReqMethod.SKILLDEV_CANCEL: "_handle_cancel",
    ReqMethod.SKILLDEV_FILE_LIST: "_handle_file_list",
    ReqMethod.SKILLDEV_FILE_READ: "_handle_file_read",
}


class SkillDevService:
    """SkillDev 模式的服务入口（无状态）."""

    def __init__(self, deps: SkillDevDeps) -> None:
        self._deps = deps

    # ------------------------------------------------------------------
    # 统一入口
    # ------------------------------------------------------------------

    async def handle(self, request: AgentRequest) -> AsyncIterator[AgentResponseChunk]:
        """根据 ReqMethod 分发到具体处理函数."""
        handler_name = _METHOD_DISPATCH.get(request.req_method)
        if handler_name is None:
            yield self._error_chunk(
                request.request_id,
                request.channel_id,
                f"未知 method: {request.req_method}",
            )
            return

        handler = getattr(self, handler_name)
        result = handler(request.params, request.request_id, request.channel_id, request.session_id)

        if inspect.isasyncgen(result):
            async for chunk in result:
                yield chunk
        elif inspect.isawaitable(result):
            yield await result
        else:
            yield result

    # ------------------------------------------------------------------
    # skilldev.start — 发起新任务
    # ------------------------------------------------------------------

    async def _handle_start(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AsyncIterator[AgentResponseChunk]:
        task_id = str(params.get("task_id") or session_id or "").strip()
        if not task_id:
            yield self._error_chunk(request_id, channel_id, "缺少 task_id 或 session_id 参数")
            return
        
        # if params.get("model"):
        #     self._deps.model_client_config["model_name"] = params.get("model").get("modelID")
        #     self._deps.model_client_config["client_provider"] = params.get("model").get("providerID")
        #     self._deps.model_name = params.get("model").get("modelID")

        files_list = params.get("files", [])
        skill_packages_list = params.get("skill_packages", [])
        tool_spec_files_list = params.get("tool_spec_files", [])

        state = SkillDevState(
            task_id=task_id,
            input={
                "query": params.get("query", ""),
                "files": files_list,
                "skill_packages": skill_packages_list,
                "tool_spec_files": tool_spec_files_list,
            },
        )
        pipeline = SkillDevPipeline(task_id=task_id, state=state, deps=self._deps)

        # 注册取消事件，供 _handle_cancel 在运行期间设置
        cancel_event = asyncio.Event()
        self._deps.cancel_events[task_id] = cancel_event

        try:
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={"event_type": "skilldev.started", "task_id": task_id},
                is_complete=False,
            )

            async for event in pipeline.run():
                if state.stage == SkillDevStage.ERROR:
                    yield self._error_chunk(request_id, channel_id, state.error or "未知错误")
                    return
                yield self._event_to_chunk(event, request_id, channel_id)

            is_done = state.stage == SkillDevStage.COMPLETED
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={
                    "event_type": "skilldev.completed" if is_done else "skilldev.suspended",
                    "task_id": task_id,
                    "stage": state.stage.value,
                },
                is_complete=True,
            )
        finally:
            self._deps.cancel_events.pop(task_id, None)

    # ------------------------------------------------------------------
    # skilldev.parse_skill — 导入并解压本地 skill 包到工作区
    # 仅允许在任务开始前调用（存在 state.json 时拒绝）
    # ------------------------------------------------------------------
    async def _handle_parse_skill(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AsyncIterator[AgentResponseChunk]:
        task_id = str(
            params.get("task_id") or params.get("session_id") or session_id or ""
        ).strip()
        logger.info(
            "[session=%s] [SkillDevService] _handle_parse_skill called: request_id=%s channel_id=%s",
            task_id,
            request_id,
            channel_id,
        )
        if not task_id:
            yield self._error_chunk(request_id, channel_id, "缺少 task_id 或 session_id 参数")
            return

        if self._deps.state_store.load_state_sync(task_id) is not None:
            yield self._error_chunk(
                request_id,
                channel_id,
                f"任务 {task_id} 已开始，禁止导入新的 skill 包",
            )
            return

        file_obj = params.get("skill_package")
        if not isinstance(file_obj, dict):
            yield self._error_chunk(request_id, channel_id, "缺少 skill_package 参数")
            return

        # 小艺适配
        if file_obj.get("url", ""):
            filename = file_obj.get("filename")
            download_url = file_obj.get("url")
            suffix = Path(filename).suffix.lower()
            if suffix not in (".zip", ".skill"):
                yield self._error_chunk(request_id, channel_id, "仅支持 .zip 或 .skill 格式")
                return

            workspace = await self._deps.workspace_provider.ensure_local(task_id)

            skill_dir = workspace / "skill"
            download_path = workspace / f"imported{suffix}"
            try:
                _ = await download_file(download_url, str(download_path))
            except Exception as exc:
                logger.warning(
                    "[session=%s] [SkillDevService] skill 包下载失败: err=%s",
                    task_id,
                    exc,
                )
                yield self._error_chunk(request_id, channel_id, f"skill 包下载失败: {exc}")
                return
            
        else:
            filename = str(file_obj.get("filename") or "").strip()
            content_b64 = str(file_obj.get("base64Data") or "").strip()
            if not filename or not content_b64:
                yield self._error_chunk(
                    request_id,
                    channel_id,
                    "skill_package 参数缺少 filename 或 base64Data",
                )
                return
            suffix = Path(filename).suffix.lower()
            if suffix not in (".zip", ".skill"):
                yield self._error_chunk(request_id, channel_id, "仅支持 .zip 或 .skill 格式")
                return

            workspace = await self._deps.workspace_provider.ensure_local(task_id)

            skill_dir = workspace / "skill"
            download_path = workspace / f"imported{suffix}"
            
            raw = base64.b64decode(content_b64)
            download_path.write_bytes(raw)

        try:
            safe_extract_zip(download_path, skill_dir, extract_to_stem_dir=False)
            skill_md = skill_dir / "SKILL.md"
            skill_name, _, _ = parse_skill_frontmatter(skill_md)
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={"event_type": "skilldev.skill_name_ready", "skill_name": skill_name},
                is_complete=False,
            )
        except Exception as exc:
            logger.warning(
                "[session=%s] [SkillDevService] skill 包解压缩失败: err=%s",
                task_id,
                exc,
            )
            yield self._error_chunk(request_id, channel_id, f"skill 包解压缩失败: {exc}")
            return
        finally:
            if download_path.exists():
                download_path.unlink(missing_ok=True)

        yield AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={
                "ok": True,
                "task_id": task_id,
                "message": f"Skill 包导入成功: {filename}",
            },
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.respond — 统一确认入口
    # 前端只管发 {task_id, action, ...}，后端根据当前阶段自动路由
    # ------------------------------------------------------------------

    async def _handle_respond(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AsyncIterator[AgentResponseChunk]:
        task_id = params.get("task_id")
        if not task_id:
            yield self._error_chunk(request_id, channel_id, "缺少 task_id 参数")
            return

        state = await self._deps.state_store.load_state(task_id)
        if state is None:
            yield self._error_chunk(request_id, channel_id, f"任务 {task_id} 不存在")
            return

        if state.stage not in SUSPENSION_POINTS:
            yield self._error_chunk(
                request_id,
                channel_id,
                f"任务 {task_id} 当前阶段 {state.stage.value} 不是挂起点，无法 respond",
            )
            return

        pipeline = SkillDevPipeline(task_id=task_id, state=state, deps=self._deps)

        # 注册取消事件，供 _handle_cancel 在运行期间设置
        cancel_event = asyncio.Event()
        self._deps.cancel_events[task_id] = cancel_event

        try:
            async for event in pipeline.resume(data=params):
                if state.stage == SkillDevStage.ERROR:
                    yield self._error_chunk(request_id, channel_id, state.error or "未知错误")
                    return
                yield self._event_to_chunk(event, request_id, channel_id)

            is_done = state.stage == SkillDevStage.COMPLETED
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={
                    "event_type": "skilldev.completed" if is_done else "skilldev.suspended",
                    "task_id": task_id,
                    "stage": state.stage.value,
                },
                is_complete=True,
            )
        finally:
            self._deps.cancel_events.pop(task_id, None)

    # ------------------------------------------------------------------
    # skilldev.status — 查状态 / 列任务
    # 传 task_id → 返回单个任务状态；不传 → 返回任务列表
    # ------------------------------------------------------------------

    def _handle_status(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        if not task_id:
            task_ids = self._deps.state_store.list_tasks()
            return AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={"ok": True, "tasks": task_ids},
                is_complete=True,
            )

        state = self._deps.state_store.load_state_sync(task_id)
        payload = (
            state.to_status_dict() if state else {"error": f"任务 {task_id} 不存在"}
        )
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": state is not None, **payload},
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.download — 下载产物
    # ------------------------------------------------------------------

    async def _handle_download(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        if not task_id:
            return self._error_chunk(request_id, channel_id, "缺少 task_id 参数")

        state = self._deps.state_store.load_state_sync(task_id)
        if state is None or not state.zip_path:
            return self._error_chunk(
                request_id, channel_id, f"任务 {task_id} 尚未完成打包"
            )

        zip_path = Path(state.zip_path)
        if not zip_path.exists():
            return self._error_chunk(request_id, channel_id, "产物文件不存在")
        try:
            upload_file_obs = UploadFileOSMS()
            download_url = await upload_file_obs.upload_file(str(zip_path))
        except Exception as exc:
            logger.warning(
                "[session=%s] [SkillDevService] skill 包上传到OBS服务器失败: err=%s",
                task_id,
                exc,
            )
            return self._error_chunk(request_id, channel_id, f"skill 包上传到OBS服务器失败: {exc}")

        export_id = f"exp_{secrets.token_hex(3)}"
        export_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={
                "filename": zip_path.name,
                "url": download_url,
                "mimeType": "application/zip",
                "exportId": export_id,
                "exportedAt": export_time,
            },
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.cancel — 取消任务
    # ------------------------------------------------------------------
    def _handle_cancel(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id", "")
        event = self._deps.cancel_events.get(task_id)
        if event:
            event.set()
            msg = "取消信号已发送，pipeline 将在下一阶段边界终止"
            logger.info("[session=%s] [SkillDevService] 取消信号已发送", task_id)
        else:
            msg = "任务未在运行中，无需取消"
            logger.info("[session=%s] [SkillDevService] 取消请求：任务未在运行", task_id)
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": True, "message": msg},
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.file.list — 获取工作区文件树（供产物弹窗浏览）
    # ------------------------------------------------------------------

    def _handle_file_list(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        if not task_id:
            return self._error_chunk(request_id, channel_id, "缺少 task_id 参数")

        workspace = self._deps.workspace_provider.get_local_path(task_id)
        skill_dir = workspace / "skill"
        if not skill_dir.exists():
            return AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={"ok": True, "tree": []},
                is_complete=True,
            )

        tree = self._build_file_tree(skill_dir, skill_dir)
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": True, "tree": tree},
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.file.read — 读取工作区文件内容
    # ------------------------------------------------------------------
    def _handle_file_read(
        self, params: dict, request_id: str, channel_id: str, session_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        file_path = params.get("path", "")
        if not task_id or not file_path:
            return self._error_chunk(
                request_id, channel_id, "缺少 task_id 或 path 参数"
            )

        workspace = self._deps.workspace_provider.get_local_path(task_id)
        skill_dir = workspace / "skill"
        full_path = (skill_dir / file_path).resolve()

        if not str(full_path).startswith(str(skill_dir.resolve())):
            return self._error_chunk(
                request_id, channel_id, "路径非法：不能访问工作区外的文件"
            )

        if not full_path.exists() or not full_path.is_file():
            return self._error_chunk(request_id, channel_id, f"文件不存在: {file_path}")

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = f"[二进制文件，大小 {full_path.stat().st_size} bytes]"

        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": True, "path": file_path, "content": content},
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------

    @staticmethod
    def _event_to_chunk(
        event: SkillDevEvent, request_id: str, channel_id: str
    ) -> AgentResponseChunk:
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"event_type": event.event_type.value, **event.payload},
            is_complete=False,
        )

    @staticmethod
    def _error_chunk(
        request_id: str, channel_id: str, message: str
    ) -> AgentResponseChunk:
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"event_type": "skilldev.error", "error": message},
            is_complete=True,
        )

    @staticmethod
    def _build_file_tree(directory: Path, root: Path) -> list[dict]:
        """递归构建文件树."""
        result: list[dict] = []
        try:
            entries = sorted(
                directory.iterdir(), key=lambda p: (not p.is_dir(), p.name)
            )
        except PermissionError:
            return result

        for entry in entries:
            if entry.name.startswith("."):
                continue
            rel = str(entry.relative_to(root)).replace("\\", "/")
            if entry.is_dir():
                children = SkillDevService._build_file_tree(entry, root)
                result.append({"path": rel + "/", "type": "dir", "children": children})
            else:
                result.append(
                    {"path": rel, "type": "file", "size": entry.stat().st_size}
                )
        return result
