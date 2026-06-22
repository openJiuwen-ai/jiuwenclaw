# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Memory Wiki Manager - LLM sub-agent driven memory indexing and querying."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails import SecurityRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.workspace.workspace import Workspace

from jiuwenclaw.agentserver.utils import DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL
from jiuwenclaw.config import get_config
from jiuwenclaw.utils import logger

from .config import MemorySettings
from .internal import hash_text, is_daily_memory_file, list_daily_memory_files
from .wiki_prompts import (
    SCHEMA_AGENT_MD,
    INDEX_MD_TEMPLATE,
    LOG_MD_TEMPLATE,
    build_query_prompt,
    DEFAULT_WIKI_AGENT_SYSTEM_PROMPT,
    DEFAULT_WIKI_AGENT_DESCRIPTION,
)


def _get_default_model() -> Model:
    config = get_config()
    default_model_conf = config.get("models", {}).get("default", {})
    # Defensive copies: these dicts are mutated below, so we must not
    # modify the shared get_config() cache.
    client_config = dict(default_model_conf.get("model_client_config", {}))
    req_config = dict(default_model_conf.get("model_config_obj", {}))

    if client_config and client_config.get("custom_headers") == "":
        del client_config["custom_headers"]

    model_name = client_config.get("model_name", "default")
    if "model" not in req_config:
        req_config["model"] = model_name

    return Model(
        model_client_config=(
            ModelClientConfig(**client_config)
            if client_config
            else ModelClientConfig(model_name=model_name)
        ),
        model_config=(
            ModelRequestConfig(**req_config) if req_config else ModelRequestConfig(model=model_name)
        ),
    )


def _is_daily_memory_basename(filename: str) -> bool:
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}\.md$', filename))


class MemoryWikiManager:
    """LLM sub-agent driven memory index manager.

    Only indexes daily memory files (YYYY-MM-DD.md) in memory/ directory.
    MEMORY.md and USER.md are skipped since they are loaded directly into context.
    Supports incremental indexing: when a file grows, only new lines are indexed.
    Index operations run asynchronously in a background worker to avoid blocking
    the agent conversation.
    """

    _CACHE: Dict[str, "MemoryWikiManager"] = {}

    def __init__(
        self,
        agent_id: str,
        workspace_dir: str,
        settings: MemorySettings,
        *,
        language: Optional[str] = None,
        max_iterations: int = 10,
        query_timeout_s: int = 60,
    ) -> None:
        self.agent_id = agent_id
        self.workspace_dir = workspace_dir
        self.memory_dir = os.path.join(workspace_dir, "memory")
        self.daily_memory_dir = os.path.join(self.memory_dir, "daily_memory")
        self.wiki_dir = os.path.join(self.memory_dir, ".wiki_index")
        self.wiki_wiki_dir = os.path.join(self.wiki_dir, "wiki")
        self.wiki_sources_dir = os.path.join(self.wiki_dir, "sources")
        self.wiki_schema_dir = os.path.join(self.wiki_dir, "schema")

        self._settings = settings
        self._language = language
        self._max_iterations = max_iterations
        self._query_timeout_s = query_timeout_s

        self._wiki_agent: Optional[DeepAgent] = None
        self._session: Optional[Session] = None
        self._file_hashes: Dict[str, str] = {}
        self._file_indexed_lines: Dict[str, int] = {}
        self._closed = False

        self._index_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._index_worker_task: Optional[asyncio.Task] = None
        self._indexing_lock = asyncio.Lock()

    @staticmethod
    def _normalize_rel_path(rel_path: str) -> str:
        """Normalize relative path to use forward slashes for cross-platform consistency."""
        return rel_path.replace("\\", "/")

    @classmethod
    async def get(
        cls,
        agent_id: str,
        workspace_dir: str,
        settings: MemorySettings,
        **kwargs: Any,
    ) -> Optional["MemoryWikiManager"]:
        cache_key = f"{agent_id}:{workspace_dir}"
        if cache_key in cls._CACHE:
            return cls._CACHE[cache_key]

        instance = cls(
            agent_id=agent_id,
            workspace_dir=workspace_dir,
            settings=settings,
            **kwargs,
        )
        try:
            await instance._initialize()
        except Exception as e:
            logger.error("MemoryWikiManager init failed: %s", e)
            return None

        cls._CACHE[cache_key] = instance
        return instance

    @classmethod
    def clear_cache(cls) -> None:
        for inst in cls._CACHE.values():
            try:
                asyncio.get_event_loop().create_task(inst.close())
            except Exception as e:
                logger.warning("Failed to close cached MemoryWikiManager: %s", e)
        cls._CACHE.clear()

    async def _initialize(self) -> None:
        self._ensure_dirs()
        self._init_wiki_files()
        self._load_indexed_lines_state()
        self._validate_index_consistency()
        self._diag_log("_initialize: before _create_wiki_agent")
        await self._create_wiki_agent()
        self._diag_log(f"_initialize: _create_wiki_agent done, agent={self._wiki_agent is not None}")
        self._session = Session(
            session_id=hashlib.sha256(
                f"memory_wiki:{self.agent_id}".encode()
            ).hexdigest()[:16]
        )

        self._start_index_worker()

        self._enqueue_full_index()

        logger.info(
            "MemoryWikiManager initialized for: %s (agent=%s)",
            self.workspace_dir, self.agent_id,
        )

    def _start_index_worker(self) -> None:
        if self._index_worker_task is not None:
            return
        self._index_worker_task = asyncio.create_task(self._index_worker_loop())
        self._diag_log("_start_index_worker: background worker started")

    async def _index_worker_loop(self) -> None:
        self._diag_log("_index_worker_loop: started")
        while not self._closed:
            try:
                task = await asyncio.wait_for(
                    self._index_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            try:
                await self._execute_index_task(task)
            except Exception as e:
                self._diag_log(f"_index_worker_loop: task error: {e}")
                logger.error("index worker task error: %s", e, exc_info=True)
            finally:
                self._index_queue.task_done()

        self._diag_log("_index_worker_loop: stopped")

    async def _execute_index_task(self, task: Dict[str, Any]) -> None:
        task_type = task.get("type", "")
        rel_path = task.get("rel_path", "")
        label = f"async_{task_type} {rel_path}"

        async with self._indexing_lock:
            self._diag_log(f"_execute_index_task: {label} started")

            if task_type == "full_index":
                await self._do_full_index(task)
            elif task_type == "incremental_index":
                await self._do_incremental_index(task)
            elif task_type == "new_file_index":
                await self._do_new_file_index(task)
            else:
                logger.warning("Unknown index task type: %s", task_type)
                return

            self._save_indexed_lines_state()
            self._diag_log(f"_execute_index_task: {label} completed")

    async def _do_full_index(self, task: Dict[str, Any]) -> None:
        files_to_index = task.get("files", [])
        if not files_to_index:
            return

        if not self._wiki_agent:
            logger.warning("_do_full_index: wiki_agent is None")
            return

        logger.info(
            "_do_full_index: starting async index of %d files",
            len(files_to_index),
        )

        pages_before = self._count_wiki_pages()
        any_success = False

        for file_info in files_to_index:
            if self._closed:
                break

            rel_path = file_info["rel_path"]
            basename = file_info["basename"]

            abs_path = os.path.normpath(os.path.join(self.workspace_dir, rel_path))
            if not os.path.exists(abs_path):
                logger.warning("_do_full_index: file disappeared: %s", rel_path)
                continue

            all_lines = self._read_file_lines(abs_path)
            total_lines = len(all_lines)
            content = "".join(all_lines)
            content_hash = hash_text(content)

            if not self._copy_to_sources(basename, content):
                continue

            prompt = (
                f"Read the rules in your `schema/AGENT.md`."
                f" Process the new raw source document '{basename}' inside `sources/` into the wiki."
                f" CRITICAL: You MUST read `wiki/index.md` and other relevant `.md` files to discover existing topics."
                f" Actively interconnect them by adding deep Markdown cross-links."
                f" FINALLY, you MUST append a detailed summary of what knowledge you extracted into `wiki/log.md`!"
            )

            success = await self._invoke_index_agent(
                prompt, f"full_index {basename}"
            )
            if success:
                self._file_hashes[rel_path] = content_hash
                self._file_indexed_lines[rel_path] = total_lines
                logger.info("full_index completed for %s", basename)
                any_success = True
            else:
                logger.warning("full_index failed for %s, will retry next time", basename)

        if any_success and not self._has_index_changed(pages_before):
            logger.warning(
                "_do_full_index: sub-agent returned success but no wiki pages were created, "
                "clearing recorded hashes to force re-index on next startup"
            )
            for file_info in files_to_index:
                rp = file_info["rel_path"]
                self._file_hashes.pop(rp, None)
                self._file_indexed_lines.pop(rp, None)

    async def _do_incremental_index(self, task: Dict[str, Any]) -> None:
        rel_path = task.get("rel_path", "")
        basename = os.path.basename(rel_path)

        abs_path = os.path.normpath(os.path.join(self.workspace_dir, rel_path))
        if not os.path.exists(abs_path):
            logger.warning("_do_incremental_index: file disappeared: %s", rel_path)
            return

        all_lines = self._read_file_lines(abs_path)
        total_lines = len(all_lines)
        content = "".join(all_lines)
        content_hash = hash_text(content)

        if self._file_hashes.get(rel_path) == content_hash:
            logger.info("_do_incremental_index: file unchanged after dequeue, skipping %s", rel_path)
            return

        if not self._copy_to_sources(basename, content):
            return

        prev_indexed = self._file_indexed_lines.get(rel_path, 0)
        is_actually_incremental = prev_indexed > 0 and total_lines > prev_indexed

        logger.info(
            "_do_incremental_index: %s (actually_incremental=%s, prev_lines=%d, total_lines=%d)",
            rel_path, is_actually_incremental, prev_indexed, total_lines,
        )

        pages_before = self._count_wiki_pages()

        if is_actually_incremental:
            prompt = (
                f"Read the rules in your `schema/AGENT.md`."
                f" Process the new raw source document '{basename}' inside `sources/` into the wiki."
                f" This is an incremental update: lines {prev_indexed + 1} to {total_lines} are new."
                f" CRITICAL: You MUST read `wiki/index.md` and other relevant `.md` files to discover existing topics."
                f" Check existing entity pages and only append new information."
                f" Actively interconnect them by adding deep Markdown cross-links."
                f" FINALLY, you MUST append a detailed summary of what knowledge you extracted into `wiki/log.md`!"
            )
        else:
            prompt = (
                f"Read the rules in your `schema/AGENT.md`."
                f" Process the new raw source document '{basename}' inside `sources/` into the wiki."
                f" CRITICAL: You MUST read `wiki/index.md` and other relevant `.md` files to discover existing topics."
                f" Actively interconnect them by adding deep Markdown cross-links."
                f" FINALLY, you MUST append a detailed summary of what knowledge you extracted into `wiki/log.md`!"
            )

        success = await self._invoke_index_agent(
            prompt, f"incremental_index {basename}"
        )
        if success:
            self._file_hashes[rel_path] = content_hash
            self._file_indexed_lines[rel_path] = total_lines
            logger.info("incremental_index completed for %s", basename)
        else:
            logger.warning("incremental_index failed for %s", basename)

        if success and not self._has_index_changed(pages_before):
            logger.warning(
                "_do_incremental_index: sub-agent returned success but no wiki pages changed for %s",
                basename,
            )

    async def _do_new_file_index(self, task: Dict[str, Any]) -> None:
        rel_path = task.get("rel_path", "")
        basename = os.path.basename(rel_path)

        abs_path = os.path.normpath(os.path.join(self.workspace_dir, rel_path))
        if not os.path.exists(abs_path):
            logger.warning("_do_new_file_index: file disappeared: %s", rel_path)
            return

        all_lines = self._read_file_lines(abs_path)
        total_lines = len(all_lines)
        content = "".join(all_lines)
        content_hash = hash_text(content)

        if self._file_hashes.get(rel_path) == content_hash:
            logger.info("_do_new_file_index: file unchanged after dequeue, skipping %s", rel_path)
            return

        if not self._copy_to_sources(basename, content):
            return

        pages_before = self._count_wiki_pages()

        prompt = (
            f"Read the rules in your `schema/AGENT.md`."
            f" Process the new raw source document '{basename}' inside `sources/` into the wiki."
            f" CRITICAL: You MUST read `wiki/index.md` and other relevant `.md` files to discover existing topics."
            f" Actively interconnect them by adding deep Markdown cross-links."
            f" FINALLY, you MUST append a detailed summary of what knowledge you extracted into `wiki/log.md`!"
        )

        success = await self._invoke_index_agent(
            prompt, f"new_file_index {basename}"
        )
        if success:
            self._file_hashes[rel_path] = content_hash
            self._file_indexed_lines[rel_path] = total_lines
            logger.info("new_file_index completed for %s", basename)
        else:
            logger.warning("new_file_index failed for %s", basename)

        if success and not self._has_index_changed(pages_before):
            logger.warning(
                "_do_new_file_index: sub-agent returned success but no wiki pages changed for %s",
                basename,
            )

    def _enqueue_full_index(self) -> None:
        all_files = list_daily_memory_files(self.workspace_dir)
        if not all_files:
            logger.info("_enqueue_full_index: no daily memory files found")
            return

        files_to_index: List[Dict[str, Any]] = []

        for abs_path in all_files:
            rel_path = self._normalize_rel_path(os.path.relpath(abs_path, self.workspace_dir))

            all_lines = self._read_file_lines(abs_path)
            total_lines = len(all_lines)
            content = "".join(all_lines)

            content_hash = hash_text(content)
            if self._file_hashes.get(rel_path) == content_hash:
                logger.info("_enqueue_full_index: skipping unchanged file %s", rel_path)
                continue

            files_to_index.append({
                "rel_path": rel_path,
                "basename": os.path.basename(rel_path),
                "total_lines": total_lines,
                "content_hash": content_hash,
            })

        if not files_to_index:
            logger.info("_enqueue_full_index: all files unchanged, nothing to index")
            return

        logger.info(
            "_enqueue_full_index: enqueuing %d files (out of %d total)",
            len(files_to_index), len(all_files),
        )

        self._index_queue.put_nowait({
            "type": "full_index",
            "files": files_to_index,
        })
        self._diag_log(f"_enqueue_full_index: enqueued {len(files_to_index)} files")

    def _ensure_dirs(self) -> None:
        for d in [
            self.memory_dir,
            self.daily_memory_dir,
            self.wiki_dir,
            self.wiki_wiki_dir,
            self.wiki_sources_dir,
            self.wiki_schema_dir,
        ]:
            os.makedirs(d, exist_ok=True)

    def _init_wiki_files(self) -> None:
        schema_file = os.path.join(self.wiki_schema_dir, "AGENT.md")
        if os.path.exists(schema_file):
            try:
                with open(schema_file, "r", encoding="utf-8") as f:
                    if f.read() != SCHEMA_AGENT_MD:
                        with open(schema_file, "w", encoding="utf-8") as f:
                            f.write(SCHEMA_AGENT_MD)
            except OSError:
                pass
        else:
            with open(schema_file, "w", encoding="utf-8") as f:
                f.write(SCHEMA_AGENT_MD)

        index_file = os.path.join(self.wiki_wiki_dir, "index.md")
        if not os.path.exists(index_file):
            with open(index_file, "w", encoding="utf-8") as f:
                f.write(INDEX_MD_TEMPLATE)
        else:
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    content = f.read()
                if "记忆索引" in content:
                    with open(index_file, "w", encoding="utf-8") as f:
                        f.write(INDEX_MD_TEMPLATE)
            except OSError:
                pass

        log_file = os.path.join(self.wiki_wiki_dir, "log.md")
        if not os.path.exists(log_file):
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(LOG_MD_TEMPLATE)
        else:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    content = f.read()
                if "记忆索引操作日志" in content:
                    with open(log_file, "w", encoding="utf-8") as f:
                        f.write(LOG_MD_TEMPLATE)
            except OSError:
                pass

    def _state_file_path(self) -> str:
        return os.path.join(self.wiki_dir, "_index_state.json")

    def _load_indexed_lines_state(self) -> None:
        state_path = self._state_file_path()
        if not os.path.exists(state_path):
            return
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict):
                raw_indexed_lines = state.get("indexed_lines", {})
                raw_file_hashes = state.get("file_hashes", {})
                self._file_indexed_lines = {
                    self._normalize_rel_path(k): v for k, v in raw_indexed_lines.items()
                }
                self._file_hashes = {
                    self._normalize_rel_path(k): v for k, v in raw_file_hashes.items()
                }
                logger.info(
                    "Loaded index state: %d files tracked",
                    len(self._file_indexed_lines),
                )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load index state: %s", e)

    def _save_indexed_lines_state(self) -> None:
        state_path = self._state_file_path()
        try:
            state = {
                "indexed_lines": self._file_indexed_lines,
                "file_hashes": self._file_hashes,
            }
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("Failed to save index state: %s", e)

    def _validate_index_consistency(self) -> None:
        if not self._file_hashes:
            return
        wiki_pages = self._count_wiki_pages()
        if wiki_pages == 0 and self._file_hashes:
            stale_count = len(self._file_hashes)
            logger.warning(
                "Index consistency check: %d files have recorded hashes but wiki has 0 pages. "
                "Clearing stale index state to force re-index.",
                stale_count,
            )
            self._file_hashes.clear()
            self._file_indexed_lines.clear()

    async def _create_wiki_agent(self) -> None:
        resolved_language = resolve_language(self._language)

        card = AgentCard(
            name="memory_wiki_agent",
            description=DEFAULT_WIKI_AGENT_DESCRIPTION.get(
                resolved_language, DEFAULT_WIKI_AGENT_DESCRIPTION["cn"]
            ),
        )
        system_prompt = DEFAULT_WIKI_AGENT_SYSTEM_PROMPT.get(
            resolved_language, DEFAULT_WIKI_AGENT_SYSTEM_PROMPT["cn"]
        )

        workspace = Workspace(
            root_path=self.wiki_dir,
            directories=[
                {
                    "name": "sources",
                    "description": "Original daily memory files (YYYY-MM-DD.md, immutable)",
                    "path": "sources",
                    "children": [],
                },
                {
                    "name": "wiki",
                    "description": "Structured entity and topic index pages",
                    "path": "wiki",
                    "children": [],
                },
                {
                    "name": "schema",
                    "description": "Schema and rule definitions",
                    "path": "schema",
                    "children": [],
                },
            ],
            language=resolved_language,
        )

        model = _get_default_model()

        self._wiki_agent = create_deep_agent(
            model=model,
            card=card,
            system_prompt=system_prompt,
            tools=[],
            mcps=None,
            subagents=None,
            rails=[SecurityRail()],
            enable_task_loop=True,
            max_iterations=self._max_iterations,
            workspace=workspace,
            skills=None,
            backend=None,
            sys_operation=None,
            language=resolved_language,
            auto_create_workspace=False,
            enable_read_image_multimodal=DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL,
        )

        await self._wiki_agent.ensure_initialized()
        self._register_workspace_tools()

    def _register_workspace_tools(self) -> None:
        if not self._wiki_agent:
            return
        from openjiuwen.core.runner import Runner

        card = self._wiki_agent.card
        sysop_id = f"{card.name}_{card.id}"
        sysop = Runner.resource_mgr.get_sys_operation(sysop_id)
        if not sysop:
            logger.warning("_register_workspace_tools: no SysOperation found for %s", sysop_id)
            return

        tool_cards = Runner.resource_mgr.get_sys_op_tool_cards(sysop_id)
        if not tool_cards:
            logger.warning("_register_workspace_tools: no tool cards found for %s", sysop_id)
            return

        registered = 0
        for tool_card in tool_cards:
            if tool_card.name in (
                "read_file", "write_file", "edit_file",
                "list_files", "list_directories", "search_files",
            ):
                if self._wiki_agent.ability_manager.get(tool_card.name) is None:
                    self._wiki_agent.ability_manager.add(tool_card)
                    registered += 1
                    logger.info(
                        "Registered workspace tool: %s (id=%s)",
                        tool_card.name, tool_card.id,
                    )
        logger.info("_register_workspace_tools: registered %d tools", registered)

    async def notify_change(self, file_path: str) -> Dict[str, Any]:
        if self._closed:
            return {"success": False, "error": "Manager is closed"}

        abs_path = file_path if os.path.isabs(file_path) else os.path.join(self.workspace_dir, file_path)
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        basename = os.path.basename(abs_path)
        if not _is_daily_memory_basename(basename):
            return {"success": False, "error": f"Not a daily memory file: {basename}"}

        rel_path = self._normalize_rel_path(os.path.relpath(abs_path, self.workspace_dir))

        all_lines = self._read_file_lines(abs_path)
        total_lines = len(all_lines)
        content = "".join(all_lines)
        content_hash = hash_text(content)

        if self._file_hashes.get(rel_path) == content_hash:
            logger.info("notify_change: file unchanged, skipping %s", rel_path)
            return {"success": True, "path": rel_path, "status": "unchanged"}

        prev_indexed = self._file_indexed_lines.get(rel_path, 0)
        is_incremental = prev_indexed > 0 and total_lines > prev_indexed

        task_type = "incremental_index" if is_incremental else "new_file_index"

        logger.info(
            "notify_change: enqueuing %s task for %s (incremental=%s, prev_lines=%d, total_lines=%d)",
            task_type, rel_path, is_incremental, prev_indexed, total_lines,
        )

        self._drain_pending_for_file(rel_path)

        self._index_queue.put_nowait({
            "type": task_type,
            "rel_path": rel_path,
            "basename": basename,
        })

        self._diag_log(f"notify_change: enqueued {task_type} for {rel_path}")

        return {"success": True, "path": rel_path, "status": "queued"}

    def _drain_pending_for_file(self, rel_path: str) -> None:
        remaining: List[Dict[str, Any]] = []
        drained = 0
        while True:
            try:
                item = self._index_queue.get_nowait()
                if item.get("rel_path") == rel_path and item.get("type") in (
                    "incremental_index", "new_file_index",
                ):
                    drained += 1
                else:
                    remaining.append(item)
            except asyncio.QueueEmpty:
                break

        for item in remaining:
            self._index_queue.put_nowait(item)

        if drained > 0:
            logger.info(
                "_drain_pending_for_file: drained %d pending tasks for %s",
                drained, rel_path,
            )

    def _diag_log(self, message: str) -> None:
        import datetime
        diag_path = os.path.join(self.wiki_dir, "_diag.log")
        try:
            ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with open(diag_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {message}\n")
        except OSError:
            pass
        logger.info("DIAG: %s", message)

    @staticmethod
    def _read_file_lines(abs_path: str) -> List[str]:
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        except OSError:
            return []

    def _copy_to_sources(self, basename: str, content: str) -> bool:
        src_dest = os.path.join(self.wiki_sources_dir, basename)
        try:
            with open(src_dest, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError as e:
            logger.error("Failed to copy source file %s: %s", basename, e)
            return False

    async def _invoke_index_agent(self, prompt: str, label: str) -> bool:
        if not self._wiki_agent:
            self._diag_log(f"{label}: wiki_agent is None, skipping")
            logger.warning("%s: wiki_agent is None, skipping", label)
            return False
        try:
            self._diag_log(f"{label}: invoking sub-agent (prompt_len={len(prompt)})")
            logger.info("%s: invoking sub-agent with prompt len=%d", label, len(prompt))
            result = await self._wiki_agent.invoke(
                {"query": prompt}, session=self._session
            )
            output_str = str(result.get("output", ""))
            self._diag_log(
                f"{label}: result keys={list(result.keys())}, "
                f"output_len={len(output_str)}, "
                f"output_preview={output_str[:300] if output_str else '(empty)'}"
            )
            logger.info(
                "%s: sub-agent result keys=%s, output_len=%d, output_preview=%s",
                label,
                list(result.keys()),
                len(output_str),
                output_str[:500] if output_str else "(empty)",
            )
            if "error" in result:
                self._diag_log(f"{label}: error={result['error']}")
                logger.warning("%s sub-agent error: %s", label, result["error"])
                return False
            if output_str.startswith("[ERROR"):
                self._diag_log(f"{label}: output_error={output_str[:200]}")
                logger.warning("%s sub-agent output error: %s", label, output_str[:200])
                return False
            self._diag_log(f"{label}: success=True")
            return True
        except Exception as e:
            self._diag_log(f"{label}: exception={e}")
            logger.error("%s exception: %s", label, e, exc_info=True)
            return False

    def _count_wiki_pages(self) -> int:
        if not os.path.isdir(self.wiki_wiki_dir):
            return 0
        return sum(
            1 for f in os.listdir(self.wiki_wiki_dir)
            if f.endswith(".md") and f not in ("index.md", "log.md")
        )

    def _has_index_changed(self, before_pages: int) -> bool:
        after_pages = self._count_wiki_pages()
        if after_pages > before_pages:
            logger.info(
                "wiki pages changed: %d -> %d", before_pages, after_pages,
            )
            return True
        logger.warning(
            "wiki pages unchanged after indexing (still %d pages)", after_pages,
        )
        return False

    async def search(
        self, query: str, opts: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not self._wiki_agent:
            return []

        opts = opts or {}
        max_results = opts.get("maxResults", 10)
        min_score = opts.get("minScore", 0.3)

        prompt = build_query_prompt(query, max_results=max_results)

        try:
            result = await asyncio.wait_for(
                self._wiki_agent.invoke({"query": prompt}, session=self._session),
                timeout=self._query_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("search timed out (%ds): %s", self._query_timeout_s, query)
            return []
        except Exception as e:
            logger.error("search exception: %s", e)
            return []

        output = result.get("output", "")
        if not output:
            logger.info("search: sub-agent returned empty output for query=%s", query)
            return []

        logger.info(
            "search: query=%s, output_len=%d, output_preview=%s",
            query, len(str(output)), str(output)[:500],
        )

        parsed = self._parse_search_result(str(output), max_results)
        filtered = [r for r in parsed if r.get("score", 0) >= min_score]
        logger.info(
            "search: parsed=%d results, filtered=%d results (minScore=%.2f)",
            len(parsed), len(filtered), min_score,
        )
        return filtered

    @staticmethod
    def _normalize_result_path(raw_path: str) -> Optional[str]:
        if not raw_path:
            return None
        p = raw_path.replace("\\", "/")
        if p.startswith("memory/daily_memory/"):
            return p
        if p.startswith("sources/"):
            basename = os.path.basename(p)
            if _is_daily_memory_basename(basename):
                return f"memory/daily_memory/{basename}"
        return None

    def _parse_search_result(
        self, output: str, max_results: int = 10
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        json_match = re.search(r'\[[\s\S]*\]', output)
        if json_match:
            try:
                items = json.loads(json_match.group())
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        raw_path = str(item.get("path", ""))
                        path = self._normalize_result_path(raw_path)
                        if not path:
                            continue
                        start = int(item.get("startLine", 0))
                        end = int(item.get("endLine", start))
                        snippet = str(item.get("snippet", ""))
                        score = float(item.get("score", 0.5))

                        results.append({
                            "id": f"{path}:{start}:{end}",
                            "path": path,
                            "source": "memory",
                            "startLine": start,
                            "endLine": end,
                            "snippet": snippet,
                            "score": min(1.0, max(0.0, score)),
                        })
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse search JSON: %s", e)

        if not results:
            for pattern_str, prefix in [
                (r'(memory/daily_memory/\d{4}-\d{2}-\d{2}\.md)', "memory/daily_memory/"),
                (r'(sources/(\d{4}-\d{2}-\d{2}\.md))', "sources/"),
            ]:
                path_match = re.search(pattern_str, output, re.IGNORECASE)
                if path_match:
                    raw = path_match.group(1)
                    path = self._normalize_result_path(raw) or raw
                    results.append({
                        "id": f"{path}:0:0",
                        "path": path,
                        "source": "memory",
                        "startLine": 0,
                        "endLine": 0,
                        "snippet": output[:200],
                        "score": 0.5,
                    })
                    break

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:max_results]

    async def read_file(
        self,
        rel_path: str,
        from_line: Optional[int] = None,
        lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_rel_path = self._normalize_rel_path(rel_path)
        if normalized_rel_path.startswith("memory/daily_memory/"):
            abs_path = os.path.normpath(os.path.join(self.workspace_dir, normalized_rel_path))
        elif normalized_rel_path.startswith("memory/"):
            abs_path = os.path.normpath(os.path.join(self.workspace_dir, normalized_rel_path))
        else:
            abs_path = os.path.join(self.workspace_dir, "memory", "daily_memory", rel_path)

        if not os.path.isfile(abs_path):
            return {"path": rel_path, "text": "", "totalLines": 0}

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except OSError:
            return {"path": rel_path, "text": "", "totalLines": 0}

        total = len(all_lines)
        start = (from_line or 1) - 1
        start = max(0, min(start, total))
        end = start + lines if lines else total
        end = min(end, total)

        selected = all_lines[start:end]
        return {
            "path": rel_path,
            "text": "".join(selected),
            "totalLines": total,
            "startLine": start + 1,
            "endLine": end,
        }

    @property
    def queue_size(self) -> int:
        return self._index_queue.qsize()

    def status(self) -> Dict[str, Any]:
        return {
            "mode": "wiki",
            "agent_id": self.agent_id,
            "workspace_dir": self.workspace_dir,
            "indexed_files": len(self._file_hashes),
            "tracked_daily_files": len(self._file_indexed_lines),
            "wiki_dir": self.wiki_dir,
            "pending_index_tasks": self.queue_size,
            "closed": self._closed,
        }

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._index_worker_task is not None:
            self._index_worker_task.cancel()
            try:
                await self._index_worker_task
            except asyncio.CancelledError:
                pass
            self._index_worker_task = None

        self._save_indexed_lines_state()

        logger.info("MemoryWikiManager closed for: %s", self.workspace_dir)
