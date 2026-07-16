# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""GitDiffWebSocketHandler: /ws/git 路由的消息分发与推送(设计文档 §2.6 / §4.2)。

职责:
  - 处理 /ws/git 连接的消息循环
  - 分发 ``diff_watch`` / ``diff_files_watch`` / ``diff_detail_watch`` /
    ``diff_unwatch`` 请求
  - 首次响应通过 ``channel.send_response`` 返回快照
  - 后续变化由 ``GitDiffWatcherRegistry`` 通过 ``channel.send_event`` 推送

事件推送复用 ``WebChannel.send_event(ws, event, payload)``,
``seq``/``stream_id`` 传 ``None``(设计文档 §5.3.11)。

四种事件:
  - ``project.git.diff_changed``: summary fingerprint 变化时推送
  - ``project.git.diff_files_changed``: 文件列表 fingerprint 变化时推送
  - ``project.git.diff_detail_changed``: 已订阅文件 hunk 变化时推送
  - ``project.git.error``: 监控过程中 Git 命令失败时推送
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

#: /ws/git 支持的 source 取值
_VALID_SOURCES: frozenset[str] = frozenset({"current", "last_turn"})

#: /ws/git 支持的 unwatch scope 取值
_VALID_UNWATCH_SCOPES: frozenset[str] = frozenset({"all", "files", "detail"})


class GitDiffWebSocketHandler:
    """/ws/git socket 的消息分发与推送,作为 ``WebChannel`` 内部组件。

    由 ``WebChannel._connection_handler`` 在 path 分发中创建并调用
    ``handle_connection``。
    """

    def __init__(self, channel: Any, registry: Any) -> None:
        self._channel = channel
        self._registry = registry

    async def handle_connection(self, ws: Any, parsed_query: dict[str, str]) -> None:
        """处理 /ws/git 连接的消息循环。

        ``parsed_query`` 为已扁平化的 query dict(query 参数 → str)。
        断连清理由 ``WebChannel._connection_handler`` 的 ``finally`` 块负责
        (``unregister_ws`` + ``cleanup_ws``)。
        """
        try:
            async for raw in ws:
                await self._handle_message(ws, raw)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[GitWS] connection loop ended: %s", exc)

    async def _handle_message(self, ws: Any, raw: str) -> None:
        """解析并分发单条消息。"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._channel.send_response(
                ws, "", ok=False, error="invalid json", code="BAD_REQUEST",
            )
            return
        if not isinstance(data, dict):
            await self._channel.send_response(
                ws, "", ok=False, error="invalid request", code="BAD_REQUEST",
            )
            return

        req_type = data.get("type")
        req_id = data.get("id")
        method = data.get("method")
        params = data.get("params")

        if req_type != "req" or not isinstance(req_id, str) or not isinstance(method, str):
            await self._channel.send_response(
                ws,
                req_id if isinstance(req_id, str) else "",
                ok=False,
                error="invalid request",
                code="BAD_REQUEST",
            )
            return
        if not isinstance(params, dict):
            params = {}

        if method == "project.git.diff_watch":
            await self._handle_diff_watch(ws, req_id, params)
        elif method == "project.git.diff_files_watch":
            await self._handle_diff_files_watch(ws, req_id, params)
        elif method == "project.git.diff_detail_watch":
            await self._handle_diff_detail_watch(ws, req_id, params)
        elif method == "project.git.diff_unwatch":
            await self._handle_diff_unwatch(ws, req_id, params)
        else:
            await self._channel.send_response(
                ws, req_id, ok=False,
                error=f"unknown method: {method}", code="BAD_REQUEST",
            )
            return

    @staticmethod
    def _resolve_git_project(project_id: str):
        """校验并加载可用于 Git 操作的 code 项目。

        委托给共享 helper ``project_git.resolve_git_project``,
        与 ``app_web_handlers.py`` 的 Git RPC handler 共用同一校验逻辑。

        Returns:
            ``(project, error_message, error_code)``: 成功时后两项为 None。
        """
        from jiuwenswarm.server.runtime.session.project_git import resolve_git_project
        return resolve_git_project(project_id, cache_bust=False)

    async def _send_git_error_response(
        self, ws: Any, req_id: str, exc: Exception,
    ) -> None:
        """发送 Git 结构化错误响应(设计文档 §1.4)。

        委托给共享 helper ``project_git.send_git_error_response``。
        """
        from jiuwenswarm.server.runtime.session.project_git import send_git_error_response
        await send_git_error_response(self._channel, ws, req_id, exc)

    async def _handle_diff_watch(
        self, ws: Any, req_id: str, params: dict[str, Any],
    ) -> None:
        """订阅 diff summary 监控(设计文档 §4.2.1)。

        首次响应只返回统计快照(``files`` 固定 ``{}``);后续变化由
        ``GitDiffWatcherRegistry`` 推送 ``project.git.diff_changed`` 事件。
        ``include_last_turn``(默认 true)控制是否监控 last_turn 变化。
        """
        project_id = str(params.get("project_id") or "").strip()
        proj, err, code = self._resolve_git_project(project_id)
        if proj is None:
            await self._channel.send_response(ws, req_id, ok=False, error=err, code=code)
            return

        session_id = str(params.get("session_id") or "").strip()
        scope = str(params.get("scope") or "summary").strip() or "summary"
        if scope != "summary":
            await self._channel.send_response(
                ws, req_id, ok=False,
                error=f"invalid scope: {scope}, only 'summary' is supported",
                code="BAD_REQUEST",
            )
            return

        include_last_turn = self._parse_bool_param(
            params, "include_last_turn", default=True,
        )

        watch = await self._registry.add_watch(
            ws, project_id, session_id, scope="summary",
            include_last_turn=include_last_turn,
        )

        from jiuwenswarm.server.runtime.session.git_diff_status import (
            get_diff_status_service,
        )
        service = get_diff_status_service()
        try:
            status = await asyncio.to_thread(
                service.get_project_diff_status,
                project=proj,
                session_id=session_id or None,
                include_files=False,
                include_hunks=False,
            )
        except Exception as exc:  # noqa: BLE001
            await self._registry.remove_watch(
                watch.watch_id, scope="all", expected_ws=ws,
            )
            await self._send_git_error_response(ws, req_id, exc)
            return

        status_dict = status.to_dict(include_hunks=False)
        # 用首次快照播种指纹，避免轮询首轮 "" → 非空 必然触发冗余 diff_changed
        self._registry.seed_summary_fingerprint(watch.watch_id, status_dict)
        snapshot = self._build_summary_snapshot(
            watch.watch_id, status_dict, include_last_turn=include_last_turn,
        )
        await self._channel.send_response(ws, req_id, ok=True, payload=snapshot)
        self._registry.mark_dirty(project_id, watch_id=watch.watch_id)

    @staticmethod
    def _parse_bool_param(
        params: dict[str, Any], key: str, *, default: bool,
    ) -> bool:
        """解析布尔参数:接受 bool 或字符串 'true'/'false'。"""
        raw = params.get(key)
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1", "yes"):
            return True
        if text in ("false", "0", "no"):
            return False
        return default

    def _build_summary_snapshot(
        self, watch_id: str, status_dict: dict[str, Any],
        *, include_last_turn: bool = True,
    ) -> dict[str, Any]:
        """构造 diff_watch 首次响应 payload(设计文档 §4.2.1)。

        ``include_last_turn=False`` 时 ``last_turn`` 固定为 ``None``。
        """
        repo = status_dict.get("repo") or {}
        current = status_dict.get("current")
        last_turn = status_dict.get("last_turn") if include_last_turn else None
        return {
            "watch_id": watch_id,
            "scope": "summary",
            "snapshot": {
                "project_id": status_dict.get("project_id", ""),
                "session_id": status_dict.get("session_id"),
                "repo": {
                    "branch": repo.get("branch"),
                    "head": repo.get("head"),
                    "transient": repo.get("transient", False),
                },
                "current": self._summary_entry(current) if current else None,
                "last_turn": self._turn_summary_entry(last_turn) if last_turn else None,
                "revision": f"gitdiff:{int(time.time())}:init",
            },
        }

    @staticmethod
    def _summary_entry(current: dict[str, Any]) -> dict[str, Any]:
        """summary 事件/快照中的 current 条目(``files`` 固定 ``{}``)。"""
        return {
            "kind": current.get("kind", "working_tree"),
            "is_dirty": current.get("is_dirty", False),
            "stats": current.get("stats", {}),
            "files": {},
        }

    @staticmethod
    def _turn_summary_entry(last_turn: dict[str, Any]) -> dict[str, Any]:
        """summary 事件/快照中的 last_turn 条目(``files`` 固定 ``{}``)。"""
        return {
            "kind": last_turn.get("kind", "conversation_turn"),
            "turn_index": last_turn.get("turn_index", 0),
            "stats": last_turn.get("stats", {}),
            "files": {},
        }

    async def _handle_diff_files_watch(
        self, ws: Any, req_id: str, params: dict[str, Any],
    ) -> None:
        """订阅变更文件列表(设计文档 §4.2.2)。

        在已有 ``watch_id`` 上开启或切换文件列表监控,立即返回当前文件列表快照
        (不含 hunk);后续文件列表 fingerprint 变化时推送
        ``project.git.diff_files_changed``。
        """
        project_id = str(params.get("project_id") or "").strip()
        watch_id = str(params.get("watch_id") or "").strip()
        source = str(params.get("source") or "").strip()

        if not watch_id:
            await self._channel.send_response(
                ws, req_id, ok=False, error="watch_id is required", code="BAD_REQUEST",
            )
            return
        if source not in _VALID_SOURCES:
            await self._channel.send_response(
                ws, req_id, ok=False,
                error=f"invalid source: {source}, must be 'current' or 'last_turn'",
                code="BAD_REQUEST",
            )
            return

        proj, err, code = self._resolve_git_project(project_id)
        if proj is None:
            await self._channel.send_response(ws, req_id, ok=False, error=err, code=code)
            return

        previous_files_state = await self._registry.snapshot_files_state(
            watch_id, expected_ws=ws, expected_project_id=project_id,
        )
        watch = await self._registry.update_files(
            watch_id, source, expected_ws=ws, expected_project_id=project_id,
        )
        if watch is None:
            await self._channel.send_response(
                ws, req_id, ok=False, error="watch not found", code="NOT_FOUND",
            )
            return

        session_id = str(params.get("session_id") or "").strip() or watch.session_id

        from jiuwenswarm.server.runtime.session.git_diff_status import (
            get_diff_status_service,
        )
        service = get_diff_status_service()
        try:
            status = await asyncio.to_thread(
                service.get_project_diff_status,
                project=proj,
                session_id=session_id or None,
                include_files=True,
                include_hunks=False,
            )
        except Exception as exc:  # noqa: BLE001
            if previous_files_state is not None:
                await self._registry.restore_files_state(
                    watch_id, previous_files_state, expected_ws=ws,
                )
            await self._send_git_error_response(ws, req_id, exc)
            return

        status_dict = status.to_dict(include_hunks=False)
        files_dict = self._extract_files(status_dict, source) or {}

        files_no_hunks = self._strip_hunks(files_dict)

        payload = {
            "watch_id": watch_id,
            "files_scope": {"source": source},
            "revision": f"gitdiff:{int(time.time())}:init",
            "files": files_no_hunks,
        }
        await self._channel.send_response(ws, req_id, ok=True, payload=payload)
        # 种子 files fingerprint,避免轮询首轮 "" → 非空 触发冗余 diff_files_changed;
        # 之后 mark_dirty 唤醒轮询(也重建因结构性错误暂停的 poll task)
        self._registry.seed_files_fingerprint(watch_id, status_dict, source)
        self._registry.mark_dirty(project_id, watch_id=watch_id)

    async def _handle_diff_detail_watch(
        self, ws: Any, req_id: str, params: dict[str, Any],
    ) -> None:
        """订阅具体文件的 diff 内容(设计文档 §4.2.3)。

        在已有 ``watch_id`` 上切换详情监控对象,后端替换 source 并立即返回新快照
        (含 hunk)。只有 ``detail_files`` 中显式订阅的文件 hunk 内容变化时,
        才推送 ``project.git.diff_detail_changed``。
        """
        project_id = str(params.get("project_id") or "").strip()
        watch_id = str(params.get("watch_id") or "").strip()
        source = str(params.get("source") or "").strip()
        files_param = params.get("files")

        if not watch_id:
            await self._channel.send_response(
                ws, req_id, ok=False, error="watch_id is required", code="BAD_REQUEST",
            )
            return
        if source not in _VALID_SOURCES:
            await self._channel.send_response(
                ws, req_id, ok=False,
                error=f"invalid source: {source}, must be 'current' or 'last_turn'",
                code="BAD_REQUEST",
            )
            return

        if not isinstance(files_param, list) or not files_param:
            await self._channel.send_response(
                ws, req_id, ok=False,
                error="files must be a non-empty array of strings",
                code="BAD_REQUEST",
            )
            return
        detail_files: list[str] = []
        for f in files_param:
            if not isinstance(f, str) or not f.strip():
                await self._channel.send_response(
                    ws, req_id, ok=False,
                    error="files must contain only non-empty strings",
                    code="BAD_REQUEST",
                )
                return
            detail_files.append(f.strip())

        proj, err, code = self._resolve_git_project(project_id)
        if proj is None:
            await self._channel.send_response(ws, req_id, ok=False, error=err, code=code)
            return

        previous_detail_state = await self._registry.snapshot_detail_state(
            watch_id, expected_ws=ws, expected_project_id=project_id,
        )
        watch = await self._registry.update_detail(
            watch_id, source, detail_files, expected_ws=ws,
            expected_project_id=project_id,
        )
        if watch is None:
            await self._channel.send_response(
                ws, req_id, ok=False, error="watch not found", code="NOT_FOUND",
            )
            return

        session_id = str(params.get("session_id") or "").strip() or watch.session_id

        from jiuwenswarm.server.runtime.session.git_diff_status import (
            get_diff_status_service,
        )
        service = get_diff_status_service()
        try:
            status = await asyncio.to_thread(
                service.get_project_diff_status,
                project=proj,
                session_id=session_id or None,
                include_files=True,
                include_hunks=True,
            )
        except Exception as exc:  # noqa: BLE001
            if previous_detail_state is not None:
                await self._registry.restore_detail_state(
                    watch_id, previous_detail_state, expected_ws=ws,
                )
            await self._send_git_error_response(ws, req_id, exc)
            return

        status_dict = status.to_dict(include_hunks=True)
        files_dict = self._extract_files(status_dict, source) or {}

        detail_files_map: dict[str, Any] = {}
        for path in detail_files:
            entry = files_dict.get(path)
            if isinstance(entry, dict):
                detail_files_map[path] = entry
            else:
                detail_files_map[path] = None

        payload = {
            "watch_id": watch_id,
            "detail_scope": {"source": source, "files": detail_files},
            "revision": f"gitdiff:{int(time.time())}:init",
            "files": detail_files_map,
        }
        await self._channel.send_response(ws, req_id, ok=True, payload=payload)
        # 种子 detail fingerprint,避免轮询首轮 "" → 非空 触发冗余 diff_detail_changed;
        # 之后 mark_dirty 唤醒轮询(也重建因结构性错误暂停的 poll task)
        self._registry.seed_detail_fingerprint(watch_id, status_dict, source, detail_files)
        self._registry.mark_dirty(project_id, watch_id=watch_id)

    async def _handle_diff_unwatch(
        self, ws: Any, req_id: str, params: dict[str, Any],
    ) -> None:
        """取消监控并释放 watcher 资源(设计文档 §4.2.4)。

        ``scope="all"`` 移除整个 watcher;``scope="files"`` 仅取消文件列表;
        ``scope="detail"`` 仅取消文件内容。后两者保留 summary 订阅。
        watch_id 不存在时幂等成功。
        """
        watch_id = str(params.get("watch_id") or "").strip()
        scope = str(params.get("scope") or "all").strip() or "all"

        if not watch_id:
            await self._channel.send_response(
                ws, req_id, ok=False, error="watch_id is required", code="BAD_REQUEST",
            )
            return
        if scope not in _VALID_UNWATCH_SCOPES:
            await self._channel.send_response(
                ws, req_id, ok=False,
                error=f"invalid scope: {scope}, must be 'all', 'files', or 'detail'",
                code="BAD_REQUEST",
            )
            return

        await self._registry.remove_watch(watch_id, scope=scope, expected_ws=ws)
        await self._channel.send_response(
            ws, req_id, ok=True,
            payload={"watch_id": watch_id, "cancelled": True, "scope": scope},
        )

    @staticmethod
    def _extract_files(
        status_dict: dict[str, Any], source: str,
    ) -> dict[str, Any] | None:
        """从 status_dict 中提取指定 source 的 files 映射。"""
        if source == "current":
            current = status_dict.get("current")
            return (current or {}).get("files") if current else None
        if source == "last_turn":
            last_turn = status_dict.get("last_turn")
            return (last_turn or {}).get("files") if last_turn else None
        return None

    @staticmethod
    def _strip_hunks(files_dict: dict[str, Any]) -> dict[str, Any]:
        """去除文件条目中的 hunk(文件列表事件不推送 hunk)。

        委托给 ``git_diff_status.file_map_to_dict_no_hunks`` 统一实现,
        确保与 watcher 推送事件及 ``DiffFileEntry.to_dict(include_hunks=False)``
        输出一致(设计文档 §3.6)。
        """
        from jiuwenswarm.server.runtime.session.git_diff_status import (
            file_map_to_dict_no_hunks,
        )
        return file_map_to_dict_no_hunks(files_dict)
