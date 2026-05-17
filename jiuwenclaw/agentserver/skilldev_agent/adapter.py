# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Dedicated DeepAgent adapter for Skill generation."""

from __future__ import annotations

import asyncio
import base64
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
from jiuwenclaw.agentserver.stream_utils import tool_calls_payload_to_json_list
from jiuwenclaw.agentserver.tools.subagent_executor import init_subagent_executor
from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import set_effective_request_workspace_dir
from jiuwenclaw.config import get_config, get_default_models
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.utils import get_agent_workspace_dir

from jiuwenclaw.agentserver.skilldev_agent.prompts import SKILLDEV_AGENT_SYSTEM_PROMPT
from jiuwenclaw.agentserver.skilldev_agent.tools import build_skilldev_tools
from jiuwenclaw.agentserver.skilldev_agent.rails.context_engineering_rail import SkillDevContextEngineeringRail

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

    def get_instance(self):
        return self._instance

    def set_skill_manager(self, manager) -> None:
        self._skill_manager = manager

    async def update_workspace(self, workspace_dir: str | Path) -> None:
        """Switch to a task-scoped workspace and rebuild the agent if needed."""
        resolved = str(Path(workspace_dir))
        if resolved == self._workspace_dir and self._instance is not None:
            return
        self._workspace_dir = resolved
        await self.create_instance(self._last_config)

    async def create_instance(self, config: dict[str, Any] | None = None, *, mode: str = "claw") -> None:
        """Create the dedicated SkillDev DeepAgent."""
        config_base = config or get_config()
        self._last_config = config_base
        react_config = dict(config_base.get("react", {}))
        self._model = self._create_model(config_base)
        self._skills_dir = react_config.get("skilldev_skills_dir", str(BUILTIN_SKILLS_DIR))
        sys_operation = self._create_or_update_sys_operation()
        tools = build_skilldev_tools(
            sys_operation=sys_operation,
            language=react_config.get("language", "cn"),
            agent_id=self._agent_id,
        )

        tool_cards = self._register_tools(tools)

        self._stream_event_rail = JiuClawStreamEventRail()
        self._task_planning_rail = TaskPlanningRail()
        rails = [
            # SkillDevContextEngineeringRail(),
            FileSystemRail(),
            SecurityRail(),
            HeartbeatRail(),
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
        self._instance = create_deep_agent(
            model=self._model,
            card=AgentCard(
                name="skilldev-agent",
                id="skilldev-agent",
                description="专用 Skill 生成 Agent",
            ),
            system_prompt=SKILLDEV_AGENT_SYSTEM_PROMPT.format(workspace=self._workspace_dir, os_type=sys.platform),
            tools=tool_cards,
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
        logger.info("[SkillDevDeepAdapter] initialized workspace=%s", self._workspace_dir)

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

        def _make_chunk(payload: dict[str, Any] | None, *, is_complete: bool = False) -> AgentResponseChunk:
            return AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload=payload,
                is_complete=is_complete,
            )

        try:
            async with ask_user_question_request_scope(
                interactive_ask=True,
                session_id=session_id,
                stream_request_id=rid or "",
                channel_id=cid or "",
                event_type_prefix="skilldev",
            ):
                async for chunk in Runner.run_agent_streaming(self._instance, inputs):
                    if not (hasattr(chunk, "type") and hasattr(chunk, "payload")):
                        parsed = self._parse_stream_chunk(chunk, has_streamed_content=has_streamed_content)
                        if parsed is not None:
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

                if accumulated_text:
                    yield _make_chunk(
                        _add_task_id({"event_type": "skilldev.agent_output", "delta": accumulated_text})
                    )
        except asyncio.CancelledError:
            logger.info(
                "[SkillDevDeepAdapter] stream task cancelled: request_id=%s session_id=%s",
                rid,
                session_id,
            )
            raise
        except Exception as exc:
            logger.exception("[SkillDevDeepAdapter] stream task failed: %s", exc)
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
        lock = self._task_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            async for chunk in self._handle_chat_locked(request, params, task_id):
                yield chunk

    async def _handle_chat_locked(
        self,
        request: AgentRequest,
        params: dict[str, Any],
        task_id: str,
    ) -> AsyncIterator[AgentResponseChunk]:
        task_workspace = Path(self._base_workspace_dir) / "skilldev" / task_id
        self._init_workspace_dirs(task_workspace)
        await self._write_uploaded_resources(task_workspace, params)
        await self.update_workspace(task_workspace)

        rid = request.request_id
        cid = request.channel_id

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={"event_type": "skilldev.started", "task_id": task_id},
            is_complete=False,
        )

        query = str(params.get("message") or params.get("query") or "")
        resource_hint = self._build_resource_hint(task_workspace, params, task_id)
        raw_session_id = str(request.session_id or task_id)
        inputs = {
            "conversation_id": self._make_todo_session_id(raw_session_id),
            "query": f"{query}\n\n{resource_hint}" if resource_hint else query,
        }
        async for chunk in self.process_message_stream_impl(request, inputs):
            yield chunk

    @staticmethod
    def _get_or_create_task_id(request: AgentRequest, params: dict[str, Any]) -> str:
        explicit = params.get("task_id") or params.get("taskId")
        if explicit:
            return str(explicit)
        if request.session_id:
            return str(request.session_id)
        return uuid.uuid4().hex

    @staticmethod
    def _init_workspace_dirs(task_workspace: Path) -> None:
        for rel in (
            "skill",
            "resources/ref-files",
            "resources/ref-skills",
            "resources/available-tools",
            "evals",
            "output",
        ):
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
        await SkillDevDeepAdapter._write_resource_group(
            params.get("tool_spec_files") or params.get("toolSpecFiles") or [],
            task_workspace / "resources" / "available-tools",
            extract_zip_to_subdir=False,
        )

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
        if not any((files, skills, tools)):
            return ""
        return (
            f"任务 ID：{task_id}\n"
            f"当前 SkillDev 工作区：{task_workspace}\n"
            "用户上传资源已写入：\n"
            f"- 普通参考文件：{task_workspace / 'resources' / 'ref-files'}\n"
            f"- 参考 Skill 包：{task_workspace / 'resources' / 'ref-skills'}\n"
            f"- 可用工具说明：{task_workspace / 'resources' / 'available-tools'}\n"
            "请在生成 Skill 前按需检查这些目录。"
        )

    # ------------------------------------------------------------------
    # interrupt / answer / heartbeat / is_working
    # ------------------------------------------------------------------

    async def process_interrupt(self, request: AgentRequest) -> AgentResponse:
        intent = request.params.get("intent", "cancel") if isinstance(request.params, dict) else "cancel"
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
            AskUserQuestionRegistry.get_instance().cancel_for_session(str(request.session_id or ""))
            message = "任务已切换"
        else:
            if self._stream_event_rail is not None:
                self._stream_event_rail.abort()
            if self._instance is not None:
                await self._instance.abort()
            AskUserQuestionRegistry.get_instance().cancel_for_session(str(request.session_id or ""))
            if request.session_id:
                updated_todos = await self._cancel_pending_todos(
                    self._make_todo_session_id(str(request.session_id))
                )
            message = "任务已取消"
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
        sysop_id = f"skilldev_agent_{self._agent_id or 'default'}"
        if Runner.resource_mgr.get_sys_operation(sysop_id) is not None:
            try:
                Runner.resource_mgr.remove_sys_operation(sysop_id)
            except Exception as exc:
                logger.warning("[SkillDevDeepAdapter] remove sys_operation failed: %s", exc)

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
            logger.warning("[SkillDevDeepAdapter] cancel pending todos failed: %s", exc)
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
                    logger.debug("Failed to remove existing tool %s, re-registering anyway", card.id)
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
            logger.warning("[SkillDevDeepAdapter] init subagent tools failed: %s", exc)

    def _init_subagent_context(self) -> None:
        set_effective_request_workspace_dir(self._workspace_dir)

    def _create_model(self, config: dict[str, Any]) -> Model:
        self._model_cache.clear()
        for entry in get_default_models(config):
            mcc = dict(entry.get("model_client_config") or {})
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
                    return {"event_type": "skilldev.completed"}
                return {"event_type": "skilldev.agent_output", "delta": content} if content else None
            if chunk_type == "llm_usage":
                return {"event_type": "chat.usage_metadata", "metadata": payload}
            if chunk_type == "tool_calls.delta":
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
                return {"event_type": "skilldev.ask_user_question", **(payload if isinstance(payload, dict) else {})}
            if chunk_type == "__interaction__":
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
