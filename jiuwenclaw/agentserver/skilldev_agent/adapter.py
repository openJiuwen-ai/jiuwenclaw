# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Dedicated DeepAgent adapter for Skill generation."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperation, SysOperationCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails import SecurityRail
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.heartbeat_rail import HeartbeatRail
from openjiuwen.harness.rails.skill_use_rail import SkillUseRail
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.tools.todo import TodoItem, TodoStatus
from openjiuwen.harness.workspace.workspace import Workspace

from jiuwenclaw.agentserver.deep_agent.ask_user_question_registry import (
    ASK_REQUEST_PREFIX,
    AskUserQuestionRegistry,
    ask_user_question_request_scope,
)
from jiuwenclaw.agentserver.deep_agent.rails import JiuClawStreamEventRail
from jiuwenclaw.agentserver.skilldev.common_utils import safe_extract_zip
from jiuwenclaw.agentserver.skilldev.utils.download_file_from_url import download_file
from jiuwenclaw.agentserver.stream_utils import tool_calls_payload_to_json_list
from jiuwenclaw.agentserver.tools.subagent_executor import init_subagent_executor
from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import set_effective_request_workspace_dir
from jiuwenclaw.config import get_config, get_default_models
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.utils import get_agent_workspace_dir

from jiuwenclaw.agentserver.skilldev_agent.prompts import SKILLDEV_AGENT_SYSTEM_PROMPT
from jiuwenclaw.agentserver.skilldev_agent.subagents import build_skilldev_subagents
from jiuwenclaw.agentserver.skilldev_agent.subagents.skill_naming import (
    is_first_task_input,
    resolve_skill_name_for_first_input,
)
from jiuwenclaw.agentserver.skilldev_agent.tools import build_skilldev_tools
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.external_tool_registry import (
    format_tool_usage_hint,
    iter_tool_definitions_from_json,
    resolve_tool_spec_identity,
    write_tool_spec_file,
    write_tool_usage_catalog,
)
from jiuwenclaw.agentserver.skilldev_agent.utils.direct_import import (
    collect_skill_packages,
    extract_import_url,
    extract_packages_to_skill_dir,
    find_skill_root,
)
from jiuwenclaw.agentserver.skilldev_agent.rails.context_engineering_rail import SkillDevContextEngineeringRail
from jiuwenclaw.agentserver.skilldev.session_history.service import SkillDevSessionHistoryService
from jiuwenclaw.agentserver.skilldev_agent.utils.session_recorder import AgentSessionRecorder
from jiuwenclaw.agentserver.skilldev_agent.utils.skill_search import search_skills
from jiuwenclaw.agentserver.skilldev.stages.validate_stage import parse_skill_frontmatter

logger = logging.getLogger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills"


class SkillDevDeepAdapter:
    """AgentAdapter implementation dedicated to Skill generation."""

    def __init__(
        self,
        workspace_dir: str | None = None,
        agent_id: str | None = None,
        service_id: str | None = None,
    ) -> None:
        self._workspace_dir = str(Path(workspace_dir) if workspace_dir else get_agent_workspace_dir())
        self._base_workspace_dir = self._workspace_dir
        self._agent_id = agent_id
        self._service_id = service_id
        self._instance = None
        self._model: Model | None = None
        self._model_cache: dict[str, Model] = {}
        self._default_model_name = ""
        self._skill_manager = None
        self._stream_event_rail: JiuClawStreamEventRail | None = None
        self._task_planning_rail: TaskPlanningRail | None = None
        self._last_config: dict[str, Any] | None = None
        self._skills_dir: str = str(BUILTIN_SKILLS_DIR)
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._stale_instance_ids: set[str] = set()
        self._task_id: str | None = None
        self._session_history: SkillDevSessionHistoryService | None = None

    def get_instance(self):
        return self._instance

    def set_skill_manager(self, manager) -> None:
        self._skill_manager = manager

    def set_session_history(self, service: SkillDevSessionHistoryService | None) -> None:
        self._session_history = service

    def _session_recorder(self) -> AgentSessionRecorder | None:
        if self._session_history is None:
            return None
        return AgentSessionRecorder(self._session_history)

    @staticmethod
    def _track_chunk(
        recorder: AgentSessionRecorder | None,
        task_id: str,
        chunk: AgentResponseChunk,
    ) -> AgentResponseChunk:
        if recorder is not None:
            recorder.record_chunk(task_id=task_id, chunk=chunk)
        return chunk

    def _cleanup_stale_instances(self) -> None:
        """Remove TaskTool registrations for instances no longer in use.

        Called after a task completes, so the running task's own TaskTool
        is never removed while still needed.
        """
        if not self._stale_instance_ids:
            return
        from openjiuwen.core.runner.runner import Runner

        for card_id in list(self._stale_instance_ids):
            task_tool_id = f"task_tool_{card_id}"
            try:
                Runner.resource_mgr.remove_tool(task_tool_id)
            except Exception:
                pass
            self._stale_instance_ids.discard(card_id)

    async def update_workspace(self, workspace_dir: str | Path) -> None:
        """Switch to a task-scoped workspace and rebuild the agent if needed."""
        resolved = str(Path(workspace_dir))
        if resolved == self._workspace_dir and self._instance is not None:
            return
        self._workspace_dir = resolved
        await self.create_instance(self._last_config)

    async def create_instance(self, config: dict[str, Any] | None = None, *, mode: str = "claw") -> None:
        """Create the dedicated SkillDev DeepAgent."""
        from jiuwenclaw.agentserver.checkpoint_setup import ensure_persistent_checkpointer

        await ensure_persistent_checkpointer(self._service_id, self._agent_id)

        if self._instance is not None:
            old_card_id = getattr(getattr(self._instance, 'card', None), 'id', None)
            if old_card_id:
                self._stale_instance_ids.add(old_card_id)

        config_base = config or get_config()
        self._last_config = config_base
        react_config = dict(config_base.get("react", {}))
        self._model = self._create_model(config_base)
        self._skills_dir = react_config.get("skilldev_skills_dir", str(BUILTIN_SKILLS_DIR))
        sys_operation = self._create_or_update_sys_operation()
        tools = build_skilldev_tools(
            sys_operation=sys_operation,
            language=react_config.get("language", "cn"),
            agent_id=f"skilldev-agent-{self._task_id or 'default'}",
        )

        tool_cards = self._register_tools(tools)

        self._stream_event_rail = JiuClawStreamEventRail()
        self._task_planning_rail = TaskPlanningRail()
        rails = [
            # SkillDevContextEngineeringRail(),
            SecurityRail(),
            SkillUseRail(
                skills_dir=self._skills_dir,
                skill_mode="all",
                include_tools=False,
                include_skill_body_tools=True,
            ),
            self._stream_event_rail,
            self._task_planning_rail,
        ]

        Path(self._workspace_dir).mkdir(parents=True, exist_ok=True)
        subagents = build_skilldev_subagents(
            self._model,
            language=react_config.get("language", "cn"),
            sys_operation=sys_operation,
            agent_id=f"skilldev-agent-{self._task_id or 'default'}",
        )
        self._instance = create_deep_agent(
            model=self._model,
            card=AgentCard(
                name="skilldev-agent",
                id=f"skilldev-agent-{self._task_id or 'default'}",
                description="专用 Skill 生成 Agent",
            ),
            system_prompt=SKILLDEV_AGENT_SYSTEM_PROMPT.format(
                workspace=self._workspace_dir,
                os_type=sys.platform,
                skills_dir=self._skills_dir,
            ),
            tools=tool_cards,
            subagents=subagents,
            rails=rails,
            enable_task_loop=react_config.get("enable_task_loop", True),
            max_iterations=react_config.get("max_iterations", 200),
            workspace=Workspace(
                root_path=self._workspace_dir,
                language=react_config.get("language", "cn"),
                directories=[],
            ),
            auto_create_workspace=False,
            sys_operation=sys_operation,
            language=react_config.get("language", "cn"),
            completion_timeout=react_config.get("completion_timeout", 3600.0),
        )
        self._init_subagent_tools()
        logger.info("[session=%s] [SkillDevDeepAdapter] initialized workspace=%s", self._task_id, self._workspace_dir)

    async def reload_agent_config(
        self,
        config_base: dict[str, Any] | None = None,
        env_overrides: dict[str, Any] | None = None,
    ) -> None:
        await self.create_instance(config_base or get_config())

    async def process_message_impl(self, request: AgentRequest, inputs: dict[str, Any]) -> AgentResponse:
        """Non-streaming requests are not used for SkillDev Agent."""
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=False,
            payload={"event_type": "skilldev.error", "error": "skilldev adapter only supports streaming requests"},
            metadata=request.metadata,
        )

    async def process_message_stream_impl(
        self,
        request: AgentRequest,
        inputs: dict[str, Any],
        *,
        interactive_ask: bool = True,
    ) -> AsyncIterator[AgentResponseChunk]:
        if self._instance is None:
            await self.create_instance(self._last_config)

        session_id = str(inputs.get("conversation_id") or request.session_id or "skilldev")
        self._init_subagent_context()
        if self._stream_event_rail is not None:
            self._stream_event_rail.reset_abort()

        rid = request.request_id
        cid = request.channel_id
        has_streamed_content = False
        accumulated_text = ""

        def _add_task_id(payload: dict[str, Any]) -> dict[str, Any]:
            task_id = self._get_task_id()
            if task_id:
                payload["task_id"] = task_id
            return payload

        def _maybe_file_ready(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
            if not parsed or parsed.get("event_type") != "skilldev.tool_result":
                return None
            if parsed.get("tool_name") != "upload_file" or not parsed.get("success", False):
                return None
            raw = parsed.get("raw_output")
            if not isinstance(raw, dict):
                return None
            url = raw.get("url") or raw.get("obsUrl")
            if not url:
                return None
            return _add_task_id(
                {
                    "event_type": "skilldev.file_ready",
                    "file": {
                        "url": url,
                        "name": raw.get("name"),
                        "size_bytes": raw.get("sizeBytes"),
                        "mime": raw.get("mime"),
                    },
                }
            )

        def _make_chunk(payload: dict[str, Any] | None, *, is_complete: bool = False) -> AgentResponseChunk:
            return AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload=payload,
                is_complete=is_complete,
            )

        try:
            async with ask_user_question_request_scope(
                interactive_ask=interactive_ask,
                session_id=session_id,
                stream_request_id=rid or "",
                channel_id=cid or "",
                event_type_prefix="skilldev",
            ):
                async for chunk in Runner.run_agent_streaming(self._instance, inputs):
                    if self._is_stream_aborted():
                        break
                    if not (hasattr(chunk, "type") and hasattr(chunk, "payload")):
                        parsed = self._parse_stream_chunk(chunk, has_streamed_content=has_streamed_content)
                        if parsed is not None:
                            if self._is_stream_aborted():
                                break
                            if accumulated_text:
                                yield _make_chunk(
                                    _add_task_id({"event_type": "skilldev.agent_output", "delta": accumulated_text})
                                )
                                accumulated_text = ""
                            if parsed.get("event_type") == "skilldev.agent_output":
                                has_streamed_content = True
                            yield _make_chunk(parsed)
                        continue

                    chunk_type = chunk.type
                    payload = chunk.payload
                    if self._is_stream_aborted():
                        break

                    if chunk_type == "llm_usage":
                        yield _make_chunk(
                            {
                                "event_type": "chat.usage_metadata",
                                "metadata": payload,
                                "session_id": session_id,
                            }
                        )
                        continue

                    if chunk_type == "llm_reasoning":
                        content = (
                            (payload.get("content", "") or payload.get("output", ""))
                            if isinstance(payload, dict)
                            else str(payload)
                        )
                        if content:
                            yield _make_chunk(
                                _add_task_id({"event_type": "skilldev.agent_thinking", "delta": content})
                            )
                        continue

                    if chunk_type in {"llm_output", "content_chunk"}:
                        has_streamed_content = True
                        content = (
                            payload.get("content", "")
                            if isinstance(payload, dict)
                            else str(payload)
                        )
                        if content:
                            yield _make_chunk(
                                _add_task_id({"event_type": "skilldev.agent_output", "delta": content})
                            )
                        continue

                    if chunk_type == "answer":
                        if accumulated_text:
                            yield _make_chunk(
                                _add_task_id({"event_type": "skilldev.agent_output", "delta": accumulated_text})
                            )
                            accumulated_text = ""
                        parsed = self._parse_stream_chunk(chunk, has_streamed_content=has_streamed_content)
                        if parsed is not None:
                            yield _make_chunk(parsed)
                        continue

                    if accumulated_text:
                        yield _make_chunk(
                            _add_task_id({"event_type": "skilldev.agent_output", "delta": accumulated_text})
                        )
                        accumulated_text = ""
                    parsed = self._parse_stream_chunk(chunk, has_streamed_content=has_streamed_content)
                    if parsed is not None:
                        if parsed.get("event_type") == "skilldev.agent_output":
                            has_streamed_content = True
                        yield _make_chunk(parsed)
                        file_ready = _maybe_file_ready(parsed)
                        if file_ready is not None:
                            yield _make_chunk(file_ready)

                if accumulated_text:
                    yield _make_chunk(
                        _add_task_id({"event_type": "skilldev.agent_output", "delta": accumulated_text})
                    )
        except asyncio.CancelledError:
            logger.info(
                "[session=%s] [SkillDevDeepAdapter] stream task cancelled: request_id=%s",
                session_id,
                rid,
            )
            raise
        except Exception as exc:
            logger.exception("[session=%s] [SkillDevDeepAdapter] stream task failed: %s", session_id, exc)
            yield _make_chunk({"event_type": "skilldev.error", "error": str(exc)})

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload=None,
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.chat entry point (workspace init + resource write + stream)
    # ------------------------------------------------------------------

    async def handle_skilldev_chat_stream(self, request: AgentRequest) -> AsyncIterator[AgentResponseChunk]:
        """Top-level handler for ``skilldev.chat`` requests.

        Manages per-task workspace isolation, uploaded resource writing,
        serialization lock, and delegates to ``process_message_stream_impl``.
        """
        params = request.params if isinstance(request.params, dict) else {}
        task_id = self._get_or_create_task_id(request, params)
        self._task_id = task_id
        lock = self._task_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            try:
                async for chunk in self._handle_chat_locked(request, params, task_id):
                    yield chunk
            finally:
                self._cleanup_stale_instances()

    async def _handle_chat_locked(
        self,
        request: AgentRequest,
        params: dict[str, Any],
        task_id: str,
    ) -> AsyncIterator[AgentResponseChunk]:
        task_workspace = Path(self._base_workspace_dir) / "skilldev" / task_id
        recorder = self._session_recorder()
        raw_session_id = str(request.session_id or task_id)
        conversation_id = self._make_todo_session_id(raw_session_id)
        persist_error: str | None = None
        try:
            if recorder is not None:
                recorder.begin_round(
                    task_id=task_id,
                    params=params,
                    session_id=raw_session_id,
                    is_first=is_first_task_input(task_workspace),
                )
            async for chunk in self._iter_chat_locked(request, params, task_id, task_workspace):
                yield self._track_chunk(recorder, task_id, chunk)
        except Exception as exc:
            persist_error = str(exc)
            raise
        finally:
            if recorder is not None:
                recorder.finalize(
                    task_id=task_id,
                    task_workspace=task_workspace,
                    conversation_id=conversation_id,
                    error=persist_error,
                )

    async def _iter_chat_locked(
        self,
        request: AgentRequest,
        params: dict[str, Any],
        task_id: str,
        task_workspace: Path,
    ) -> AsyncIterator[AgentResponseChunk]:
        rid = request.request_id
        cid = request.channel_id
        query = str(params.get("message") or params.get("query") or "")

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={"event_type": "skilldev.started", "task_id": task_id},
            is_complete=False,
        )

        import_type = str(
            params.get("import_type") or params.get("importType") or "vibeImport"
        ).strip()
        if import_type == "directImport" and is_first_task_input(task_workspace):
            async for chunk in self._handle_direct_import(
                request=request,
                params=params,
                task_id=task_id,
                task_workspace=task_workspace,
                rid=rid,
                cid=cid,
            ):
                yield chunk
            return

        skill_name: str | None = None
        if is_first_task_input(task_workspace):
            if self._model is None:
                self._model = self._create_model(self._last_config or get_config())
            skill_name = await resolve_skill_name_for_first_input(
                self._model,
                workspace=task_workspace,
                user_query=query,
                task_id=task_id,
            )
        if skill_name:
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "skilldev.skill_name_ready",
                    "task_id": task_id,
                    "skill_name": skill_name,
                },
                is_complete=False,
            )

        # 如果需要进行skill检索，则检索skill并返回
        if params.get("enable_skill_search"):
            skills, total = search_skills(params.get("query"))
            if skills:
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload={
                        "event_type": "skilldev.search_results",
                        "skillList": skills,
                        "num": len(skills),
                        "total": total,
                    },
                    is_complete=False,
                )
                return
            logger.info("[session=%s] [SkillDevDeepAdapter] 没有搜索到技能", task_id)

        # 初始化工作区，写入上传资源，写入搜索到的技能
        self._init_workspace_dirs(task_workspace)
        await self._write_uploaded_resources(task_workspace, params)
        if params.get("skill_searched"):
            await self._write_skill_searched(task_workspace, params.get("skill_searched"))
        await self.update_workspace(task_workspace)

        resource_hint = self._build_resource_hint(task_workspace, params, task_id)
        raw_session_id = str(request.session_id or task_id)
        combined_query = query
        if skill_name:
            combined_query = (
                f"{query}\n\n"
                f"当前 Skill 标识名（已确定，后续 SKILL.md frontmatter 的 name 须与此一致）：{skill_name}"
            )
        if resource_hint:
            combined_query = f"{combined_query}\n\n{resource_hint}"

        name_lock_hint = self._build_name_lock_hint(
            params, task_workspace, skill_name, task_id
        )
        if name_lock_hint:
            combined_query = f"{combined_query}\n\n{name_lock_hint}"

        inputs = {
            "conversation_id": self._make_todo_session_id(raw_session_id),
            "query": combined_query,
        }
        async for chunk in self.process_message_stream_impl(request, inputs):
            yield chunk

        async for chunk in self._finalize_skilldev_run(
            task_workspace=task_workspace,
            task_id=task_id,
            rid=rid,
            cid=cid,
        ):
            yield chunk

    def _build_name_lock_hint(
        self,
        params: dict[str, Any],
        task_workspace: Path,
        skill_name: str | None,
        task_id: str,
    ) -> str | None:
        """当 skill 处于上架/测试状态时，构造禁止修改 skill name 的硬约束提示。

        触发条件：params['skillContext'] 中 onShelfStatus 或 testStatus 任一为真。
        返回 None 表示无需注入约束。
        """
        skill_context = params.get("skillContext") or params.get("skill_context")
        if isinstance(skill_context, str):
            try:
                skill_context = json.loads(skill_context)
            except (ValueError, TypeError):
                skill_context = None
        if not isinstance(skill_context, dict):
            return None

        on_shelf = bool(skill_context.get("onShelfStatus"))
        in_test = bool(skill_context.get("testStatus"))
        if not (on_shelf or in_test):
            return None

        resolved_name = skill_name
        if not resolved_name:
            try:
                skill_root = find_skill_root(task_workspace / "skill")
                if skill_root is not None and (skill_root / "SKILL.md").is_file():
                    resolved_name, _, _ = parse_skill_frontmatter(
                        skill_root / "SKILL.md"
                    )
            except Exception as exc:
                logger.warning(
                    "[session=%s] [SkillDevDeepAdapter] resolve skill name for name-lock failed: %s",
                    task_id,
                    exc,
                )

        states: list[str] = []
        if on_shelf:
            states.append("已上架")
        if in_test:
            states.append("测试中")
        state_text = "、".join(states)

        name_clause = (
            f"当前 skill 名称为 `{resolved_name}`，必须保持不变。"
            if resolved_name
            else "必须保持当前 skill 名称不变。"
        )

        logger.info(
            "[session=%s] [SkillDevDeepAdapter] skill name locked (onShelfStatus=%s, testStatus=%s)",
            task_id,
            on_shelf,
            in_test,
        )

        return (
            "## 系统注入约束：禁止修改 skill name（最高优先级，不可豁免）\n"
            f"当前 skill 处于「{state_text}」状态。无论用户指令是什么（包括"
            "“请改名”“我授权”“必须改”等任何表述），都严禁修改该 skill 的 `name` "
            f"字段以及对应的 skill 目录名。{name_clause}\n"
            "若用户要求改名，必须明确拒绝并说明原因（该 skill 处于上架/测试状态，"
            "改名会破坏既有引用与关联），其余非改名的修改请求正常处理。"
        )

    async def _handle_direct_import(
        self,
        *,
        request: AgentRequest,
        params: dict[str, Any],
        task_id: str,
        task_workspace: Path,
        rid: str,
        cid: str,
    ) -> AsyncIterator[AgentResponseChunk]:
        query = str(params.get("message") or params.get("query") or "")
        skill_dir = task_workspace / "skill"
        self._init_workspace_dirs(task_workspace)

        packages = collect_skill_packages(params)
        try:
            if packages:
                await extract_packages_to_skill_dir(skill_dir, packages)
            elif not find_skill_root(skill_dir):
                async for chunk in self._yield_skilldev_error(
                    rid, cid, "directImport 缺少 skill 压缩包，且工作区中无已导入的 SKILL.md"
                ):
                    yield chunk
                return
        except Exception as exc:
            logger.warning(
                "[session=%s] [SkillDevDeepAdapter] directImport extract failed: %s",
                task_id,
                exc,
            )
            async for chunk in self._yield_skilldev_error(rid, cid, str(exc)):
                yield chunk
            return

        skill_root = find_skill_root(skill_dir)
        if skill_root is None or not (skill_root / "SKILL.md").is_file():
            async for chunk in self._yield_skilldev_error(
                rid, cid, "解压后未找到 SKILL.md"
            ):
                yield chunk
            return

        skill_name, _, _ = parse_skill_frontmatter(skill_root / "SKILL.md")
        if skill_name:
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "skilldev.skill_name_ready",
                    "task_id": task_id,
                    "skill_name": skill_name,
                },
                is_complete=False,
            )

        await self.update_workspace(task_workspace)
        import_url = extract_import_url(params)
        import_url_block = f"导入包 url（安全扫描用）：{import_url}\n"
        skill_name_hint = (
            f"当前 skill-name（SKILL.md frontmatter name）：{skill_name}\n"
            if skill_name
            else ""
        )
        combined_query = (
            "请使用 skill-verifier 技能对当前工作区的已导入 skill 进行规范校验与安全扫描，已导入的skill位于工作区的skill目录下。\n"
            f"{skill_name_hint}"
            "## 执行步骤\n"
            "1. 运行完整闸门：cd \"<skill-verifier-dir>\" && python3 -m scripts.gate <workspace>\n"
            "2. 将闸门各阶段的结果（validate / package / upload / safety_scan）如实反馈给用户。\n"
            "3. 闸门失败不阻塞交付——将失败详情告知用户，由用户决定后续操作。\n"
            "4. 不要自动修复闸门报告的问题。\n\n"
            "用户原始请求：\n"
            f"{query}"
        )
        raw_session_id = str(request.session_id or task_id)
        inputs = {
            "conversation_id": self._make_todo_session_id(raw_session_id),
            "query": combined_query,
        }
        async for chunk in self.process_message_stream_impl(
            request,
            inputs,
            interactive_ask=True,
        ):
            yield chunk

        async for chunk in self._finalize_direct_import_run(
            task_workspace=task_workspace,
            task_id=task_id,
            rid=rid,
            cid=cid,
        ):
            yield chunk

    async def _finalize_direct_import_run(
        self,
        *,
        task_workspace: Path,
        task_id: str,
        rid: str,
        cid: str,
    ) -> AsyncIterator[AgentResponseChunk]:
        output_dir = task_workspace / "output"
        skill_files = [
            f for f in output_dir.iterdir()
            if f.is_file() and f.suffix in (".skill", ".zip")
        ] if output_dir.exists() else []

        if skill_files:
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "skilldev.agent_output",
                    "delta": "Skill 已修复并打包完成",
                    "task_id": task_id,
                },
                is_complete=False,
            )
            async for chunk in self._yield_packaged_skill_completion(
                task_id=task_id,
                rid=rid,
                cid=cid,
                packaged_files=skill_files,
            ):
                yield chunk
            return

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={
                "event_type": "skilldev.agent_completed",
                "task_id": task_id,
            },
            is_complete=False,
        )

    async def _finalize_skilldev_run(
        self,
        *,
        task_workspace: Path,
        task_id: str,
        rid: str,
        cid: str,
    ) -> AsyncIterator[AgentResponseChunk]:
        static_result, static_report = self._get_static_review_report(task_workspace)
        benchmark, report, iteration = self._get_review_benchmark(task_workspace)
        has_static = static_result is not None
        has_dynamic = benchmark is not None and report is not None and iteration >= 0

        # Frontend review events: combined > static-only > dynamic-only.
        if has_static and has_dynamic:
            logger.info("[session=%s] [SkillDevDeepAdapter] 静态和动态评估结果审阅", task_id)
            merged_report = static_report or ""
            if report:
                merged_report = f"{merged_report}\n\n---\n\n{report}" if merged_report else report
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "skilldev.confirm_request",
                    "confirm_type": "combined_review",
                    "title": "评估结果审阅",
                    "message": "请审阅静态评估和动态评测结果并决定下一步。",
                    "data": {
                        "static_benchmark": static_result,
                        "dyn_benchmark": benchmark,
                        "report": merged_report,
                        "iteration": iteration,
                    },
                    "actions": [
                        {"id": "accept", "label": "通过，继续", "style": "primary"},
                        {"id": "improve", "label": "根据建议优化", "style": "secondary"},
                    ],
                    "task_id": task_id,
                },
                is_complete=False,
            )
            return

        if has_static:
            verdict = str(static_result.get("verdict") or "").upper()
            actions = [
                {"id": "accept", "label": "质量达标，继续", "style": "primary"},
                {"id": "improve", "label": "根据建议优化", "style": "secondary"},
            ]
            if verdict == "FAIL":
                actions = [
                    {"id": "improve", "label": "根据建议优化", "style": "primary"},
                    {"id": "force_dynamic", "label": "仍然运行动态评估", "style": "secondary"},
                ]
            logger.info("[session=%s] [SkillDevDeepAdapter] 静态评估结果审阅: %s", task_id, verdict)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "skilldev.confirm_request",
                    "confirm_type": "static_review",
                    "title": "静态评估结果审阅",
                    "message": "请审阅静态评估结果并决定下一步。",
                    "data": {
                        "benchmark": static_result,
                        "report": static_report,
                    },
                    "actions": actions,
                    "task_id": task_id,
                },
                is_complete=False,
            )
            return

        if has_dynamic:
            logger.info("[session=%s] [SkillDevDeepAdapter] 评测结果审阅: %s", task_id, iteration)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "skilldev.confirm_request",
                    "confirm_type": "review",
                    "title": "动态评测结果审阅",
                    "message": "请审阅评测结果并决定下一步。",
                    "data": {
                        "benchmark": benchmark,
                        "report": report,
                        "iteration": iteration,
                    },
                    "actions": [
                        {"id": "accept", "label": "通过，继续", "style": "primary"},
                        {"id": "improve", "label": "继续改进", "style": "secondary"},
                    ],
                    "task_id": task_id,
                },
                is_complete=False,
            )
            return

        output_dir = task_workspace / "output"
        skill_files = [
            f for f in output_dir.iterdir()
            if f.is_file() and f.suffix in (".skill", ".zip")
        ] if output_dir.exists() else []

        if skill_files:
            async for chunk in self._yield_packaged_skill_completion(
                task_id=task_id,
                rid=rid,
                cid=cid,
                packaged_files=skill_files,
            ):
                yield chunk
            return

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={
                "event_type": "skilldev.agent_completed",
                "task_id": task_id,
            },
            is_complete=False,
        )

    async def _yield_packaged_skill_completion(
        self,
        *,
        task_id: str,
        rid: str,
        cid: str,
        packaged_files: list[Path],
    ) -> AsyncIterator[AgentResponseChunk]:
        for sf in packaged_files:
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "skilldev.artifact_ready",
                    "task_id": task_id,
                    "artifact": {
                        "id": "skill_package",
                        "name": sf.name,
                        "type": "skill_package",
                        "size_bytes": sf.stat().st_size,
                        "browsable": True,
                        "downloadable": True,
                    },
                },
                is_complete=False,
            )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={
                "event_type": "skilldev.completed",
                "task_id": task_id,
            },
            is_complete=True,
        )

    async def _yield_skilldev_error(
        self,
        rid: str,
        cid: str,
        error: str,
    ) -> AsyncIterator[AgentResponseChunk]:
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={"event_type": "skilldev.error", "error": error},
            is_complete=True,
        )

    @staticmethod
    def _get_or_create_task_id(request: AgentRequest, params: dict[str, Any]) -> str:
        explicit = params.get("task_id") or params.get("taskId")
        if explicit:
            return str(explicit)
        if request.session_id:
            return str(request.session_id)
        return uuid.uuid4().hex

    @staticmethod
    def _get_static_review_report(
        task_workspace: Path,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Load static_report.json / static_report.md from evals/static.

        Returns (static_result_dict, report_markdown).
        """
        static_dir = task_workspace / "evals" / "static"
        report_json = static_dir / "static_report.json"
        if not report_json.is_file():
            return None, None

        try:
            static_result = json.loads(report_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[SkillDevDeepAdapter] static report load failed: %s", exc)
            return None, None

        if not isinstance(static_result, dict) or static_result.get("reviewed", False):
            return None, None

        updated = {**static_result, "reviewed": True}
        try:
            report_json.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[SkillDevDeepAdapter] static report mark reviewed failed: %s", exc)
            return None, None

        report_md = static_dir / "static_report.md"
        report = report_md.read_text(encoding="utf-8") if report_md.is_file() else ""
        return static_result, report

    @staticmethod
    def _get_review_benchmark(
        task_workspace: Path,
    ) -> tuple[dict[str, Any] | None, str | None, int]:
        """Load the latest benchmark.json / benchmark.md from the evals dir.

        Returns (benchmark_dict, report_markdown, iteration_number).
        """
        evals_dir = task_workspace / "evals"

        iter_dirs = [
            d for d in evals_dir.iterdir()
            if d.is_dir() and d.name.startswith("iteration-")
        ] if evals_dir.is_dir() else []

        if iter_dirs:
            def _iter_num(d: Path) -> int:
                try:
                    return int(d.name.split("-", 1)[1])
                except (IndexError, ValueError):
                    return -1

            latest = max(iter_dirs, key=_iter_num)
            iteration = _iter_num(latest)
            target_dir = latest
        else:
            iteration = 0
            target_dir = evals_dir

        benchmark: dict[str, Any] | None = None
        report: str | None = None

        bm_json = target_dir / "benchmark.json"
        if bm_json.is_file():
            benchmark = json.loads(bm_json.read_text(encoding="utf-8"))
            try:
                benchmark["run_summary"]["with_skill"]["pass_rate"]["mean"]
                benchmark["run_summary"]["without_skill"]["pass_rate"]["mean"]
                benchmark["run_summary"]["delta"]["pass_rate"]
                has_required_summary = True
            except (KeyError, TypeError):
                has_required_summary = False
            if not has_required_summary or benchmark.get("reviewed"):
                return None, None, -1
            updated = {**benchmark, "reviewed": True}
            bm_json.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        bm_md = target_dir / "benchmark.md"
        if bm_md.is_file():
            report = bm_md.read_text(encoding="utf-8")

        return benchmark, report, iteration

    @staticmethod
    def _init_workspace_dirs(task_workspace: Path) -> None:
        for rel in ("skill", "evals", "output"):
            (task_workspace / rel).mkdir(parents=True, exist_ok=True)

    @staticmethod
    async def _write_uploaded_resources(task_workspace: Path, params: dict[str, Any]) -> None:
        await SkillDevDeepAdapter._write_resource_group(
            params.get("files") or [],
            task_workspace / "resources" / "ref-files",
            extract_zip_to_subdir=True,
        )
        await SkillDevDeepAdapter._write_resource_group(
            params.get("skill_packages") or params.get("skillPackages") or [],
            task_workspace / "resources" / "ref-skills",
            extract_zip_to_subdir=True,
            allowed_suffixes=(".zip", ".skill"),
        )
        await SkillDevDeepAdapter._write_tool_spec_files(
            params.get("tool_spec_files") or params.get("toolSpecFiles") or [],
            task_workspace / "resources" / "available-tools",
        )
        agent_definitions = params.get("agent_definitions") or params.get("agentDefinitions")
        if agent_definitions:
            agents_dir = task_workspace / "resources" / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            (agents_dir / "available_agents.json").write_text(
                json.dumps(agent_definitions, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        cli_definitions = params.get("cli_definitions") or params.get("cliDefinitions")
        if cli_definitions:
            clis_dir = task_workspace / "resources" / "clis"
            clis_dir.mkdir(parents=True, exist_ok=True)
            (clis_dir / "available_clis.json").write_text(
                json.dumps(cli_definitions, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    @staticmethod
    async def _write_tool_spec_files(resources: list[dict[str, Any]], dest_dir: Path) -> None:
        """Write uploaded tool specs as ``<pluginId>__<toolName>.json`` (pass-through)."""
        if not resources:
            return
        dest_dir.mkdir(parents=True, exist_ok=True)
        for res in resources:
            content_b64 = res.get("base64Data") or res.get("base64") or ""
            if content_b64:
                try:
                    raw_bytes = base64.b64decode(content_b64)
                    parsed = json.loads(raw_bytes.decode("utf-8"))
                except Exception as exc:
                    fname = res.get("filename", "?")
                    raise ValueError(f"工具定义文件 [{fname}] 解析失败: {exc}") from exc
                for tool_def in iter_tool_definitions_from_json(parsed):
                    write_tool_spec_file(dest_dir, tool_def)
            else:
                plugin_id, tool_name = resolve_tool_spec_identity(res)
                if plugin_id and tool_name:
                    write_tool_spec_file(dest_dir, res)
                else:
                    logger.warning(
                        "[SkillDevDeepAdapter] skip tool_spec entry without base64 or "
                        "pluginId/bundleName+toolName: %s",
                        res.get("filename", res),
                    )
        # write_tool_usage_catalog(dest_dir)

    @staticmethod
    async def _write_skill_searched(task_workspace: Path, skill_searched: dict[str, Any]) -> None:
        """Download a skill selected from search results into ref-skills."""
        skill_name = skill_searched.get("skillId") or skill_searched.get("skillName") or "unknown"
        url = skill_searched.get("url", "")
        if not url:
            logger.warning("[SkillDevDeepAdapter] skill_searched missing url: %s", skill_searched)
            return

        dest_dir = task_workspace / "resources" / "ref-skills"
        dest_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(url).suffix.lower() or ".skill"
        if ".skill" in suffix:
            suffix = ".skill"
        elif ".zip" in suffix:
            suffix = ".zip"
        file_path = dest_dir / f"{skill_name}{suffix}"
        await download_file(url, str(file_path))
        if suffix in (".zip", ".skill"):
            safe_extract_zip(file_path, dest_dir, extract_to_stem_dir=False)

    @staticmethod
    async def _write_resource_group(
        resources: list[dict[str, Any]],
        dest_dir: Path,
        *,
        extract_zip_to_subdir: bool,
        allowed_suffixes: tuple[str, ...] | None = None,
    ) -> None:
        if not resources:
            return
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 适配小艺：通过 URL 下载文件
        if resources[0].get("url", ""):
            for res in resources:
                name = str(res.get("filename") or res.get("name") or "unknown")
                suffix = Path(name).suffix.lower()
                if allowed_suffixes and suffix not in allowed_suffixes:
                    raise ValueError(f"不支持的文件类型: {name}")
                download_url = str(res.get("url", ""))
                if not download_url:
                    continue
                file_path = dest_dir / name
                await download_file(download_url, str(file_path))
                if suffix in (".zip", ".skill"):
                    safe_extract_zip(file_path, dest_dir, extract_to_stem_dir=False)
            return

        for res in resources:
            name = str(res.get("filename") or res.get("name") or "unknown")
            suffix = Path(name).suffix.lower()
            if allowed_suffixes and suffix not in allowed_suffixes:
                raise ValueError(f"不支持的文件类型: {name}")
            content_b64 = res.get("base64Data") or res.get("base64") or ""
            if not content_b64:
                continue
            file_path = dest_dir / name
            file_path.write_bytes(base64.b64decode(content_b64))
            if suffix in (".zip", ".skill"):
                safe_extract_zip(file_path, dest_dir, extract_to_stem_dir=extract_zip_to_subdir)

    @staticmethod
    def _build_resource_hint(task_workspace: Path, params: dict[str, Any], task_id: str) -> str:
        files = params.get("files") or []
        skills = params.get("skill_packages") or params.get("skillPackages") or []
        tools = params.get("tool_spec_files") or params.get("toolSpecFiles") or []
        agents = params.get("agent_definitions") or params.get("agentDefinitions")
        clis = params.get("cli_definitions") or params.get("cliDefinitions")
        resource_lines: list[str] = []
        if files:
            resource_lines.append(f"- 普通参考文件：{task_workspace / 'resources' / 'ref-files'}")
        if skills:
            resource_lines.append(f"- 参考 Skill 包：{task_workspace / 'resources' / 'ref-skills'}")
        if tools:
            resource_lines.append(f"- 可用工具说明：{task_workspace / 'resources' / 'available-tools'}")
        if agents:
            resource_lines.append(f"- 可用 Agent 定义：{task_workspace / 'resources' / 'agents' / 'available_agents.json'}")
        if clis:
            resource_lines.append(f"- 可用 CLI 定义：{task_workspace / 'resources' / 'clis' / 'available_clis.json'}")

        skill_searched = params.get("skill_searched")
        if not resource_lines and not skill_searched:
            return ""

        parts: list[str] = []
        if resource_lines:
            header = (
                f"任务 ID：{task_id}\n"
                f"当前 SkillDev 工作区：{task_workspace}\n"
                "用户上传资源已写入：\n"
            )
            parts.append(header + "\n".join(resource_lines))
            # if tools:
            #     parts.append(format_tool_usage_hint())
        if skill_searched:
            skill_name = skill_searched.get("skillId") or skill_searched.get("skillName") or "未知"
            ref_skills_dir = task_workspace / "resources" / "ref-skills"
            parts.append(
                f"用户明确指明要参考 {ref_skills_dir} 中的{skill_name}技能，"
                "请在生成前仔细阅读该技能，并结合用户需求，生成新的Skill"
            )
        parts.append("请在生成 Skill 前按需检查上述资源。")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # interrupt / answer / heartbeat / is_working
    # ------------------------------------------------------------------

    def _cancel_ask_user_for_request(self, request: AgentRequest) -> None:
        """取消挂起的 ask_user_question（注册时使用 todo_ 前缀 session）。"""
        registry = AskUserQuestionRegistry.get_instance()
        sid = str(request.session_id or "").strip()
        if not sid:
            return
        registry.cancel_for_session(sid)
        registry.cancel_for_session(self._make_todo_session_id(sid))

    def _is_stream_aborted(self) -> bool:
        rail = self._stream_event_rail
        return rail is not None and bool(getattr(rail, "abort_requested", False))

    async def process_interrupt(self, request: AgentRequest) -> AgentResponse:
        intent = request.params.get("intent", "cancel") if isinstance(request.params, dict) else "cancel"
        logger.info(
            "[session=%s] [SkillDevDeepAdapter] process_interrupt: intent=%s",
            request.session_id,
            intent,
        )
        updated_todos = None
        if intent == "pause" and self._stream_event_rail is not None:
            self._stream_event_rail.pause()
            message = "任务已暂停"
        elif intent == "resume" and self._stream_event_rail is not None:
            self._stream_event_rail.resume()
            message = "任务已恢复"
        elif intent == "supplement":
            if self._stream_event_rail is not None:
                self._stream_event_rail.abort()
            if self._instance is not None:
                await self._instance.abort()
            self._cancel_ask_user_for_request(request)
            message = "任务已切换"
        else:
            if self._stream_event_rail is not None:
                self._stream_event_rail.abort()
            if self._instance is not None:
                await self._instance.abort()
            self._cancel_ask_user_for_request(request)
            if request.session_id:
                updated_todos = await self._cancel_pending_todos(
                    self._make_todo_session_id(str(request.session_id))
                )
            message = "任务已取消"
        logger.info(
            "[session=%s] [SkillDevDeepAdapter] process_interrupt done: intent=%s message=%s",
            request.session_id,
            intent,
            message,
        )
        payload = {
            "event_type": "skilldev.interrupt_result",
            "intent": intent,
            "success": True,
            "message": message,
        }
        if intent not in ("pause", "resume", "supplement") and updated_todos is not None:
            payload["todos"] = updated_todos
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    async def handle_user_answer(self, request: AgentRequest) -> AgentResponse:
        params = request.params if isinstance(request.params, dict) else {}
        request_id = str(params.get("request_id", ""))
        answers = params.get("answers", [])
        source = str(params.get("source", ""))
        task_id = str(
            params.get("task_id")
            or params.get("taskId")
            or request.session_id
            or ""
        ).strip()
        recorder = self._session_recorder()
        if recorder is not None and task_id:
            recorder.record_user_answer(
                task_id=task_id,
                payload={
                    "request_id": request_id,
                    "answers": answers,
                    "task_id": task_id,
                    "session_id": str(request.session_id or task_id),
                },
            )
            task_workspace = Path(self._base_workspace_dir) / "skilldev" / task_id
            raw_session_id = str(request.session_id or task_id)
            recorder.finalize(
                task_id=task_id,
                task_workspace=task_workspace,
                conversation_id=self._make_todo_session_id(raw_session_id),
            )
        resolved = False
        if source == "ask_tool" or request_id.startswith(ASK_REQUEST_PREFIX):
            resolved = AskUserQuestionRegistry.get_instance().resolve(request_id, answers)
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"accepted": True, "resolved": resolved},
            metadata=request.metadata,
        )

    async def handle_heartbeat(self, request: AgentRequest) -> AgentResponse | None:
        return None

    @staticmethod
    def is_working(session_tasks: dict, session_queues: dict) -> bool:
        return any(not task.done() for task in session_tasks.values())

    @staticmethod
    def _get_task_id() -> str | None:
        return None

    @staticmethod
    def _make_todo_session_id(raw_id: str) -> str:
        """Derive a todo-safe session ID that won't appear in workspace paths.

        The SDK's TodoTool.invoke uses ``session_id not in self._file``
        (substring match) to decide whether to call set_file.  When the
        workspace directory already contains the raw session_id (as is the
        case for SkillDev task workspaces), the check is a false positive
        and set_file is never called.  Adding a ``todo_`` prefix ensures
        the derived ID never matches the workspace path component.
        """
        return f"todo_{raw_id}"

    def _create_or_update_sys_operation(self) -> SysOperation:
        sysop_id = f"skilldev_agent_{self._agent_id or 'default'}_{self._task_id or 'default'}"
        sys_operation = Runner.resource_mgr.get_sys_operation(sysop_id)
        if sys_operation is not None:
            return sys_operation

        sysop_card = SysOperationCard(
            id=sysop_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                sandbox_root=[str(self._workspace_dir), str(self._skills_dir)],
                restrict_to_sandbox=True,
                shell_allowlist=[
                    # basic shell
                    "echo", "printf", "true", "false", "test",
                    "cd", "pwd", "pushd", "popd", "export", "unset", "source",
                    # filesystem
                    "ls", "dir", "tree", "cat", "type", "head", "tail", "less", "more",
                    "mkdir", "md", "rm", "rd", "rmdir", "cp", "copy", "mv", "move",
                    "touch", "ln", "chmod", "chown", "stat", "file", "realpath",
                    "basename", "dirname",
                    # search
                    "find", "grep", "egrep", "fgrep", "rg", "ag", "ack",
                    "which", "whereis", "locate",
                    # text processing
                    "sed", "awk", "gawk", "cut", "sort", "uniq", "tr", "tee",
                    "wc", "diff", "patch", "xargs", "jq", "yq",
                    # archive
                    "tar", "zip", "unzip", "gzip", "gunzip",
                    # network
                    "curl", "wget", "ping",
                    # python / node
                    "python", "python3", "pip", "pip3", "uv", "pytest",
                    "node", "npm", "npx", "yarn", "pnpm",
                    # dev tools
                    "git", "make", "cmake", "cargo", "go", "java", "javac",
                    "docker", "docker-compose",
                    # system info
                    "ps", "df", "du", "env", "id", "whoami", "hostname",
                    "uname", "date", "lsof",
                    # hash / checksum
                    "md5sum", "sha256sum", "sha1sum", "shasum",
                ],
            ),
        )
        result = Runner.resource_mgr.add_sys_operation(sysop_card)
        if result.is_err():
            raise RuntimeError(f"add sys_operation failed: {result.msg()}")
        sys_operation = Runner.resource_mgr.get_sys_operation(sysop_id)
        if sys_operation is None:
            raise RuntimeError(f"sys_operation not found after registration: {sysop_id}")
        return sys_operation

    async def _cancel_pending_todos(self, session_id: str) -> list[dict[str, Any]] | None:
        """Mark unfinished SkillDev todos as cancelled and return frontend payload."""
        try:
            if not session_id or self._instance is None:
                return None

            from openjiuwen.harness.tools.todo import TodoListTool

            todo_tool: TodoListTool | None = None
            for ability in self._instance.ability_manager.list():
                tool_instance = Runner.resource_mgr.get_tool(ability.id)
                if isinstance(tool_instance, TodoListTool):
                    todo_tool = tool_instance
                    break

            if todo_tool is None:
                return None

            todo_tool.set_file(session_id)
            todos: list[TodoItem] = await todo_tool.load_todos()
            if not todos:
                return None

            changed = False
            for todo in todos:
                if todo.status in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS):
                    todo.status = TodoStatus.CANCELLED
                    changed = True

            if changed:
                await todo_tool.save_todos(todos)

            return [
                {
                    "id": todo.id,
                    "label": todo.content or todo.activeForm,
                    "status": todo.status.value,
                }
                for todo in todos
            ]
        except Exception as exc:
            logger.warning("[session=%s] [SkillDevDeepAdapter] cancel pending todos failed: %s", session_id, exc)
            return None

    @staticmethod
    def _register_tools(tools: list) -> list:
        """Pre-register Tool instances in Runner.resource_mgr and return ToolCards.

        Following the same pattern as JiuWenClawDeepAdapter._get_tool_cards:
        tools are registered here so that create_deep_agent receives only
        ToolCards, avoiding the strict identity check in _register_tool_instances.
        """
        from openjiuwen.core.foundation.tool import ToolCard as ToolCardType

        tool_cards = []
        for tool in tools:
            if isinstance(tool, ToolCardType):
                tool_cards.append(tool)
                continue
            card = getattr(tool, "card", None)
            if card is None:
                tool_cards.append(tool)
                continue
            existing = Runner.resource_mgr.get_tool(card.id)
            if existing is None:
                Runner.resource_mgr.add_tool(tool)
            elif existing is not tool:
                try:
                    Runner.resource_mgr.remove_tool(card.id)
                except Exception:
                    logger.info("Failed to remove existing tool %s, re-registering anyway", card.id)
                Runner.resource_mgr.add_tool(tool)
            tool_cards.append(card)
        return tool_cards

    def _init_subagent_tools(self) -> None:
        if self._instance is None or self._model is None:
            return
        try:
            # fork_agent/spawn_subagent are already part of the agent tool list;
            # only the executor context needs explicit initialization here.
            init_subagent_executor(self._instance, model=self._model, default_role_prompts=None)
        except Exception as exc:
            logger.warning("[session=%s] [SkillDevDeepAdapter] init subagent tools failed: %s", self._task_id, exc)

    def _init_subagent_context(self) -> None:
        set_effective_request_workspace_dir(self._workspace_dir)

    def _create_model(self, config: dict[str, Any]) -> Model:
        self._model_cache.clear()
        for entry in get_default_models(config):
            mcc = dict(entry.get("model_client_config") or {})
            # 注入session id
            mcc.setdefault("session", self._service_id)
            if not mcc.get("model_name"):
                continue
            mcc["claw_config"] = config
            self._model_cache[mcc["model_name"]] = self._build_model_from_entry(
                mcc,
                entry.get("model_config_obj") or {},
            )
        if not self._model_cache:
            default_model_config = config.get("models", {}).get("default", {})
            react_config = config.get("react", {})
            mcc = dict(default_model_config.get("model_client_config") or react_config.get("model_client_config") or {})
            model_name = mcc.get("model_name") or react_config.get("model_name") or "gpt-4"
            mcc.setdefault("model_name", model_name)
            mcc["claw_config"] = config
            mco = default_model_config.get("model_config_obj") or react_config.get("model_config_obj") or {}
            self._model_cache[model_name] = self._build_model_from_entry(mcc, mco)
        self._default_model_name = next(iter(self._model_cache))
        return self._model_cache[self._default_model_name]

    @staticmethod
    def _build_model_from_entry(model_client_config: dict[str, Any], model_config_obj: dict[str, Any]) -> Model:
        mcc = dict(model_client_config)
        name = str(mcc.pop("model_name", "") or "gpt-4")
        request_config = ModelRequestConfig(
            model=name,
            temperature=model_config_obj.get("temperature", 0.95),
        )
        return Model(
            model_client_config=ModelClientConfig(**mcc),
            model_config=request_config,
        )

    def _parse_stream_chunk(self, chunk: Any, *, has_streamed_content: bool = False) -> dict[str, Any] | None:
        if hasattr(chunk, "type") and hasattr(chunk, "payload"):
            chunk_type = chunk.type
            payload = chunk.payload
            task_id = self._get_task_id()

            def with_task_id(result: dict[str, Any]) -> dict[str, Any]:
                if task_id:
                    result["task_id"] = task_id
                return result

            if chunk_type == "controller_output" and payload is not None:
                inner_t = getattr(payload, "type", None)
                inner_val = getattr(inner_t, "value", inner_t) if inner_t is not None else None
                if inner_val == "task_completion":
                    return None
                if inner_val == "task_failed":
                    error = next(
                        (item.text for item in getattr(payload, "data", []) if hasattr(item, "text")),
                        "任务执行失败",
                    )
                    return {"event_type": "skilldev.error", "error": error}
                return None

            if chunk_type in {"llm_output", "content_chunk"}:
                content = payload.get("content", "") if isinstance(payload, dict) else str(payload)
                return with_task_id({"event_type": "skilldev.agent_output", "delta": content}) if content else None
            if chunk_type == "llm_reasoning":
                content = (
                    (payload.get("content", "") or payload.get("output", ""))
                    if isinstance(payload, dict)
                    else str(payload)
                )
                return with_task_id({"event_type": "skilldev.agent_thinking", "delta": content}) if content else None
            if chunk_type == "answer":
                content = self._extract_answer_content(payload)
                if has_streamed_content:
                    return {"event_type": "skilldev.answer_completed"}
                return {"event_type": "skilldev.agent_output", "delta": content} if content else None
            if chunk_type == "llm_usage":
                return {"event_type": "chat.usage_metadata", "metadata": payload}
            if chunk_type == "tool_calls.delta":
                # Disabled: frontend does not consume streaming tool_call deltas yet.
                # To re-enable, remove the early return below.
                return None
                if isinstance(payload, dict):
                    tc_list = tool_calls_payload_to_json_list(payload.get("tool_calls", []))
                    result = {
                        "event_type": "skilldev.tool_calls.delta",
                        "tool_calls": tc_list,
                    }
                    if "source" in payload:
                        result["source"] = payload.get("source")
                    return with_task_id(result)
                return with_task_id(
                    {
                        "event_type": "skilldev.tool_calls.delta",
                        "tool_calls": tool_calls_payload_to_json_list(payload),
                    }
                )
            if chunk_type == "tool_call":
                tool_info = payload.get("tool_call", payload) if isinstance(payload, dict) else payload
                tc_payload: dict[str, Any] = {"event_type": "skilldev.tool_call"}
                if isinstance(tool_info, dict):
                    tc_payload["tool_call_id"] = tool_info.get("id") or tool_info.get("tool_call_id")
                    tc_payload["tool_name"] = tool_info.get("name") or tool_info.get("tool_name")
                    tool_name_map = {"task_tool": "sub_agent", "skill_tool": "skill_load", "skill_complete": "skill_unload", "free_search": "web_search"}
                    tc_payload["tool_name"] = tool_name_map.get(tc_payload["tool_name"], tc_payload["tool_name"])
                    tc_payload["arguments"] = tool_info.get("arguments") or tool_info.get("args")
                else:
                    tc_payload["tool_call"] = tool_info
                return with_task_id(tc_payload)
            if chunk_type == "tool_update":
                update = payload.get("tool_update", payload) if isinstance(payload, dict) else {"content": str(payload)}
                merged = update if isinstance(update, dict) else {"content": str(update)}
                return with_task_id({"event_type": "skilldev.tool_update", **merged})
            if chunk_type == "tool_result":
                if isinstance(payload, dict):
                    result_info = payload.get("tool_result", payload)
                    result_payload: dict[str, Any] = {
                        "result": result_info.get("result", str(result_info))
                        if isinstance(result_info, dict)
                        else str(result_info),
                    }
                    if isinstance(result_info, dict):
                        result_payload["tool_name"] = result_info.get("tool_name") or result_info.get("name")
                        tool_name_map = {"task_tool": "sub_agent", "skill_tool": "skill_load", "skill_complete": "skill_unload", "free_search": "web_search"}
                        result_payload["tool_name"] = tool_name_map.get(result_payload["tool_name"], result_payload["tool_name"])
                        result_payload["tool_call_id"] = (
                            result_info.get("tool_call_id") or result_info.get("toolCallId")
                        )
                        result_payload["success"] = result_info.get("success", True)
                        raw_output = result_info.get("raw_output")
                        if raw_output is None:
                            raw_output = result_info.get("rawOutput")
                        if raw_output is not None:
                            result_payload["raw_output"] = raw_output
                else:
                    result_payload = {"result": str(payload)}
                return with_task_id({"event_type": "skilldev.tool_result", **result_payload})
            if chunk_type == "chat.ask_user_question":
                if self._is_stream_aborted():
                    return None
                return {"event_type": "skilldev.ask_user_question", **(payload if isinstance(payload, dict) else {})}
            if chunk_type == "__interaction__":
                if self._is_stream_aborted():
                    return None
                from jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers import (
                    convert_interactions_to_ask_user_question,
                )

                return convert_interactions_to_ask_user_question([payload])
            if chunk_type == "todo.updated":
                raw_todos = payload.get("todos", []) if isinstance(payload, dict) else []
                todos = [
                    {
                        "id": t.get("id", ""),
                        "label": t.get("content", "") or t.get("activeForm"),
                        "status": t.get("status", "pending"),
                    }
                    for t in raw_todos
                    if isinstance(t, dict)
                ]
                return {"event_type": "skilldev.todos_update", "todos": todos}
            if chunk_type == "context.compressed":
                if isinstance(payload, dict):
                    return {
                        "event_type": "context.compressed",
                        "rate": payload.get("rate", 0),
                        "before_compressed": payload.get("before_compressed"),
                        "after_compressed": payload.get("after_compressed"),
                    }
                return {"event_type": "context.compressed", "rate": 0}
            if chunk_type == "task.start":
                if isinstance(payload, dict):
                    return {
                        "event_type": "task.start",
                        "task_id": payload.get("task_id"),
                        "task_content": payload.get("task_content"),
                        "task_index": payload.get("task_index"),
                        "total_tasks": payload.get("total_tasks"),
                        "parent_request_id": payload.get("parent_request_id"),
                        "timestamp": payload.get("timestamp"),
                    }
                return None
            if chunk_type == "task.complete":
                if isinstance(payload, dict):
                    return {
                        "event_type": "task.complete",
                        "task_id": payload.get("task_id"),
                        "task_content": payload.get("task_content"),
                        "status": payload.get("status"),
                        "duration_ms": payload.get("duration_ms"),
                        "error": payload.get("error"),
                        "timestamp": payload.get("timestamp"),
                    }
                return None
            if chunk_type == "error":
                error = payload.get("error", str(payload)) if isinstance(payload, dict) else str(payload)
                return {"event_type": "skilldev.error", "error": error}
            if isinstance(payload, dict):
                if "traceId" in payload or "invokeId" in payload:
                    return None
                content = payload.get("content") or payload.get("output")
                if not content:
                    return None
                return with_task_id({"event_type": "skilldev.agent_output", "delta": str(content)})
            return None

        if isinstance(chunk, dict):
            if "traceId" in chunk or "invokeId" in chunk:
                return None
            if chunk.get("result_type") == "error":
                return {"event_type": "skilldev.error", "error": chunk.get("output", "未知错误")}
            output = chunk.get("output")
            if output:
                result: dict[str, Any] = {"event_type": "skilldev.agent_output", "delta": str(output)}
                task_id = self._get_task_id()
                if task_id:
                    result["task_id"] = task_id
                return result
            return None

        return {"event_type": "skilldev.agent_output", "delta": str(chunk)} if chunk else None

    @staticmethod
    def _extract_answer_content(payload: Any) -> str:
        if isinstance(payload, dict):
            if payload.get("result_type") == "error":
                return str(payload.get("output", "未知错误"))
            output = payload.get("output", "")
            if isinstance(output, dict):
                if output.get("result_type") == "error":
                    return str(output.get("output", "未知错误"))
                return str(output.get("output", ""))
            return str(output)
        return str(payload)
