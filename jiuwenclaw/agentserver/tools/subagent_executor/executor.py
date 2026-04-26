# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Fork agent executor for DeepAgent architecture.

Core executor for fork_agent and spawn_subagent execution.
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.core.foundation.llm import Model

from jiuwenclaw.agentserver.tools.subagent_models import (
    ForkAgentResult,
    ForkAgentTaskSpec,
    SubagentResult,
    SubagentTaskSpec,
)
from jiuwenclaw.agentserver.deep_agent.prompt_builder import build_subagent_base_prompt
from jiuwenclaw.agentserver.deep_agent.rails import JiuClawContextEngineeringRail
from jiuwenclaw.utils import get_agent_root_dir, logger
from jiuwenclaw.config import get_config

from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
    get_effective_request_workspace_dir,
    get_subagent_parent_session,
    _get_llm_trace_session_id_var,
)
from jiuwenclaw.agentserver.tools.subagent_executor.session_proxy import SubagentSessionProxy
from jiuwenclaw.agentserver.tools.subagent_executor.rails import (
    ForkMessageInjectionRail,
    SubagentContextRail,
)

if TYPE_CHECKING:
    pass


class ForkAgentExecutor:
    """Fork agent executor for DeepAgent architecture.

    Uses Runner.run_agent(context=...) to pass fork_messages instead of
    modifying invoke method (which is inside DeepAgent SDK).
    """

    def __init__(
        self,
        parent_agent: DeepAgent,
        model: Model,
        default_role_prompts: dict[str, str] | None = None,
    ) -> None:
        """Initialize the subagent executor.

        Args:
            parent_agent: Parent DeepAgent instance (for inheriting tools)
            model: Model instance for creating subagents
            default_role_prompts: Default role prompts (used when role_id not found)
        """
        self._parent_agent = parent_agent
        self._model = model
        self._default_role_prompts = default_role_prompts or {}
        self._active_fork_agents: dict[str, Any] = {}  # task_id -> subagent instance

    def _resolve_subagent_workspace_dir(
        self,
        task_workspace_dir: str | None,
    ) -> tuple[str, str]:
        """Resolve workspace for fork/spawn to match the main agent for the current request.

        Order: explicit task path > per-request (same as RuntimePromptRail) >
        parent DeepAgent workspace > agent root.
        """
        if task_workspace_dir and str(task_workspace_dir).strip():
            return (str(task_workspace_dir).strip(), "task.workspace_dir")

        req_ws = get_effective_request_workspace_dir()
        if isinstance(req_ws, str) and req_ws.strip():
            return (req_ws.strip(), "effective_request_workspace_dir")

        parent_config = getattr(self._parent_agent, "deep_config", None)
        if parent_config and hasattr(parent_config, "workspace"):
            parent_ws = getattr(parent_config.workspace, "root_path", None)
            if parent_ws:
                root = str(parent_ws).strip()
                if root:
                    return (root, "parent_config.workspace.root_path")

        return (get_agent_root_dir(), "get_agent_root_dir()")

    def resolve_permission_approval(self, request_id: str, answers: list) -> bool:
        """Resolve permission approval across all active fork agents.

        Called by parent agent when it cannot find the request_id in its own pending approvals.

        Args:
            request_id: Permission approval request ID
            answers: User's answers from the approval UI

        Returns:
            True if resolved successfully, False otherwise
        """
        for task_id, fork_agent in self._active_fork_agents.items():
            resolve_method = getattr(fork_agent, "_resolve_permission_approval", None)
            if resolve_method is not None:
                try:
                    resolved = resolve_method(request_id, answers)
                    if resolved:
                        logger.info(
                            f"[ForkAgent] Resolved permission approval in "
                            f"task_id={task_id}, request_id={request_id}"
                        )
                        return True
                except Exception as e:
                    logger.warning(
                        f"[ForkAgent] Failed to resolve permission approval "
                        f"in task_id={task_id}: {e}"
                    )
        return False

    def _get_role_definition(self, role_id: str) -> Any | None:
        """Get role definition from default_role_prompts.

        Args:
            role_id: Role ID to lookup

        Returns:
            SubagentRoleDefinition or None (triggers dynamic generation)
        """
        from jiuwenclaw.agentserver.tools.subagent_models import SubagentRoleDefinition

        if role_id in self._default_role_prompts:
            return SubagentRoleDefinition(
                name=role_id,
                system_prompt=self._default_role_prompts[role_id],
            )
        return None

    @staticmethod
    def _generate_dynamic_role_prompt(role_id: str) -> str:
        """Generate dynamic role prompt based on role name.

        Triggered when user specifies a role that's not predefined.
        Examples: "Java架构师", "数据分析师", etc.

        Args:
            role_id: User-specified role name

        Returns:
            Role-specific prompt (will be appended to build_subagent_base_prompt)
        """
        config_base = get_config()
        language = config_base.get("preferred_language", "zh")

        if language == "zh":
            return f"""你是一个 {role_id}。

以该领域的专业知识和最佳实践执行任务。你的职责包括：
- 运用领域特定的知识和最佳实践
- 提供结构化、有理据的分析和建议
- 以该领域专家应有的精确度执行任务

系统化地处理每个任务，交付高质量的结果。
"""
        else:
            return f"""You are a {role_id}.

Act with expertise and professionalism in this domain. Your responsibilities include:
- Applying domain-specific knowledge and best practices
- Providing structured, well-reasoned analysis and recommendations
- Executing tasks with the precision expected of an expert in this field

Approach each task methodically and deliver high-quality results.
"""

    async def execute_fork(
        self,
        task: ForkAgentTaskSpec,
        fork_messages: list[Any],
        parent_session: Session | None = None,
    ) -> ForkAgentResult:
        """Execute a fork agent task with inherited messages.

        Key mechanism:
        - ForkMessageInjectionRail injects fork_messages at before_model_call hook
        - This works around Runner.run_agent ignoring the context parameter

        Args:
            task: Fork agent task specification
            fork_messages: Messages from parent Agent context to inherit
            parent_session: Optional parent session for event forwarding
        """
        if parent_session is None:
            parent_session = get_subagent_parent_session()

        try:
            # 1. Create session proxy FIRST (needed for SubagentContextRail to emit events)
            session_proxy: SubagentSessionProxy | None = None
            if parent_session is not None:
                session_proxy = SubagentSessionProxy(
                    parent_session=parent_session,
                    subagent_id=task.task_id,
                    role_id=task.role_id,
                )

            # 2. Create fork agent with fork_messages injection rail
            fork_agent = await self._create_fork_agent(task, fork_messages, parent_session=session_proxy)

            # 3. Build full prompt
            full_prompt = task.objective
            if task.prompt:
                full_prompt = f"{task.objective}\n\n{task.prompt}"

            logger.info(
                f"[ForkAgent] Starting execution, task_id={task.task_id}, role_id={task.role_id}, "
                f"inherited_messages={len(fork_messages)}"
            )

            # 4. Register active agent for permission approval resolution
            self._active_fork_agents[task.task_id] = fork_agent

            # 5. Set session_id for LLM IO trace logging
            if session_proxy:
                trace_session_id = session_proxy.get_session_id()
            else:
                trace_session_id = task.task_id
            llm_trace_var = _get_llm_trace_session_id_var()
            token_trace_sid = llm_trace_var.set(trace_session_id)

            # 6. Execute fork agent
            session_id = task.session_id or task.task_id
            invoke_inputs = {"query": full_prompt, "conversation_id": session_id}

            try:
                response = await Runner.run_agent(
                    agent=fork_agent,
                    inputs=invoke_inputs,
                    session=session_proxy,
                )
            finally:
                self._active_fork_agents.pop(task.task_id, None)
                llm_trace_var.reset(token_trace_sid)

            logger.info(f"[ForkAgent] Execution completed, task_id={task.task_id}")

            # 7. Extract result and usage
            result_text = ""
            fork_usage = None
            if isinstance(response, dict):
                result_text = response.get("output", "")
                if isinstance(result_text, dict):
                    result_text = result_text.get("output", str(result_text))
                fork_usage = response.get("usage")
            elif hasattr(response, "content"):
                result_text = response.content
            elif hasattr(response, "text"):
                result_text = response.text
            else:
                result_text = str(response)

            if fork_usage:
                logger.info(f"[ForkAgent] task_id={task.task_id} usage: {fork_usage}")

            return ForkAgentResult(
                success=True,
                task_id=task.task_id,
                role_id=task.role_id,
                result=result_text,
                usage=fork_usage,
            )

        except asyncio.TimeoutError:
            logger.warning(
                f"[ForkAgent] Timeout after {task.timeout_seconds} seconds, task_id={task.task_id}"
            )
            return ForkAgentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=f"Timeout after {task.timeout_seconds} seconds",
            )
        except Exception as e:
            logger.exception(f"[ForkAgent] Execution failed: {e}")
            return ForkAgentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=str(e),
            )

    async def execute_spawn(
        self,
        task: SubagentTaskSpec,
        parent_session: Session | None = None,
    ) -> SubagentResult:
        """Execute a spawn subagent task with isolated context.

        Key differences from execute_fork:
        - Uses Runner.run_agent(session=None) for isolated context
        - No fork_messages passed (fresh context)
        - Supports role definition lookup and dynamic role generation

        Args:
            task: SubagentTaskSpec - Spawn task specification
            parent_session: Optional parent session for event forwarding
        """
        if parent_session is None:
            parent_session = get_subagent_parent_session()

        try:
            # 1. Get role definition
            role_def = self._get_role_definition(task.role_id)

            # 2. Determine system_prompt (priority: call param > role def > dynamic generation)
            if task.system_prompt:
                system_prompt = task.system_prompt
            elif role_def and hasattr(role_def, 'system_prompt') and role_def.system_prompt:
                system_prompt = role_def.system_prompt
            else:
                system_prompt = self._generate_dynamic_role_prompt(task.role_id)
                logger.info(f"[SpawnAgent] Generated dynamic role prompt for: {task.role_id}")

            # 3. Create session proxy FIRST (needed for SubagentContextRail to emit events)
            session_proxy: SubagentSessionProxy | None = None
            if parent_session is not None:
                session_proxy = SubagentSessionProxy(
                    parent_session=parent_session,
                    subagent_id=task.task_id,
                    role_id=task.role_id,
                )

            # 4. Create spawn agent (DeepAgent instance)
            spawn_agent = await self._create_spawn_agent(task, system_prompt, parent_session=session_proxy)

            # 5. Build full prompt
            full_prompt = task.objective
            if task.prompt:
                full_prompt = f"{task.objective}\n\n{task.prompt}"

            logger.info(
                f"[SpawnAgent] Starting execution, task_id={task.task_id}, role_id={task.role_id}"
            )

            # 6. Register active agent for permission approval resolution
            self._active_fork_agents[task.task_id] = spawn_agent

            # 7. Set session_id for LLM IO trace logging
            if session_proxy:
                trace_session_id = session_proxy.get_session_id()
            else:
                trace_session_id = task.task_id
            llm_trace_var = _get_llm_trace_session_id_var()
            token_trace_sid = llm_trace_var.set(trace_session_id)

            # 8. Execute with isolated context
            session_id = task.session_id or task.task_id
            invoke_inputs = {"query": full_prompt, "conversation_id": session_id}

            try:
                response = await Runner.run_agent(
                    agent=spawn_agent,
                    inputs=invoke_inputs,
                    session=session_proxy,
                )
            finally:
                self._active_fork_agents.pop(task.task_id, None)
                llm_trace_var.reset(token_trace_sid)

            logger.info(f"[SpawnAgent] Execution completed, task_id={task.task_id}")

            # 9. Extract result and usage
            result_text = ""
            spawn_usage = None
            if isinstance(response, dict):
                result_text = response.get("output", "")
                if isinstance(result_text, dict):
                    result_text = result_text.get("output", str(result_text))
                spawn_usage = response.get("usage")
            elif hasattr(response, "content"):
                result_text = response.content
            elif hasattr(response, "text"):
                result_text = response.text
            else:
                result_text = str(response)

            if spawn_usage:
                logger.info(f"[SpawnAgent] task_id={task.task_id} usage: {spawn_usage}")

            return SubagentResult(
                success=True,
                task_id=task.task_id,
                role_id=task.role_id,
                result=result_text,
                usage=spawn_usage,
            )

        except asyncio.TimeoutError:
            logger.warning(
                f"[SpawnAgent] Timeout after {task.timeout_seconds} seconds, task_id={task.task_id}"
            )
            return SubagentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=f"Timeout after {task.timeout_seconds} seconds",
            )
        except Exception as e:
            logger.exception(f"[SpawnAgent] Execution failed: {e}")
            return SubagentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=str(e),
            )

    async def _create_spawn_agent(
        self,
        task: SubagentTaskSpec,
        system_prompt: str,
        parent_session: Session | None = None,
    ) -> DeepAgent:
        """Create spawn agent (DeepAgent instance) with isolated context.

        Args:
            task: SubagentTaskSpec
            system_prompt: System prompt for the agent
            parent_session: Parent session for event forwarding

        Returns:
            DeepAgent instance for spawn subagent
        """
        ws, ws_source = self._resolve_subagent_workspace_dir(task.workspace_dir)
        logger.debug(
            "[SpawnAgent] workspace_dir=%s source=%s task.workspace_dir=%s",
            ws,
            ws_source,
            task.workspace_dir,
        )

        config_base = get_config()
        language = config_base.get("preferred_language", "zh")

        base_prompt = build_subagent_base_prompt(
            language=language,
            workspace_dir=ws,
            include_time=True,
        )
        augmented_prompt = base_prompt + "\n\n---\n\n# Subagent Role\n\n" + system_prompt

        card = AgentCard(
            name=f"spawn_{task.role_id}",
            id=task.task_id,
        )

        workspace_obj = Workspace(
            root_path=ws,
            language=language,
        )

        spawn_agent = create_deep_agent(
            model=self._model,
            card=card,
            system_prompt=augmented_prompt,
            max_iterations=config_base.get("max_iterations", 15),
            workspace=workspace_obj,
            rails=[
                JiuClawContextEngineeringRail(preset=True),  # 上下文压缩
                SubagentContextRail(subagent_id=task.task_id, parent_session=parent_session),
            ],
            language=language,
            enable_task_loop=False,
        )

        self._inherit_tools_for_spawn(spawn_agent, task.allowed_tools)

        logger.info(f"[SpawnAgent] Created spawn agent instance, task_id={task.task_id}")
        return spawn_agent

    def _inherit_tools_for_spawn(
        self,
        spawn_agent: DeepAgent,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> None:
        """Inherit tools from parent agent for spawn agent.

        IMPORTANT: Does NOT exclude fork_agent, allowing spawn to call fork.
        """
        excluded_tools = {
            "spawn_subagent",
            "todo_create", "todo_complete", "todo_insert", "todo_remove", "todo_list",
            "office_claw_list_skills", "office_claw_load_skill",
        }

        try:
            parent_tools = self._parent_agent.ability_manager.list()
            if not parent_tools:
                logger.debug("[SpawnAgent] Parent agent has no tools to inherit")
                return

            inherited_count = 0
            for tool in parent_tools:
                try:
                    tool_name = getattr(tool, "name", None)
                    if hasattr(tool, "card") and hasattr(tool.card, "name"):
                        tool_name = tool.card.name

                    if tool_name in excluded_tools:
                        logger.debug(f"[SpawnAgent] Skipping excluded tool: {tool_name}")
                        continue

                    if allowed_tools and tool_name not in allowed_tools:
                        logger.debug(f"[SpawnAgent] Skipping tool not in allowed_tools: {tool_name}")
                        continue

                    if hasattr(tool, "card"):
                        spawn_agent.ability_manager.add(tool.card)
                    else:
                        spawn_agent.ability_manager.add(tool)
                    inherited_count += 1
                except Exception as e:
                    logger.debug(f"[SpawnAgent] Failed to inherit tool: {e}")

            logger.info(
                f"[SpawnAgent] Inherited {inherited_count} tools from parent agent (fork_agent allowed)"
            )
        except Exception as e:
            logger.warning(f"[SpawnAgent] Failed to inherit tools: {e}")

    async def _create_fork_agent(
        self,
        task: ForkAgentTaskSpec,
        fork_messages: list[Any],
        parent_session: Session | None = None,
    ) -> DeepAgent:
        """Create fork agent (DeepAgent instance) with inherited messages.

        Args:
            task: Fork agent task specification
            fork_messages: Messages from parent agent to inherit
            parent_session: Parent session for event forwarding

        Returns:
            DeepAgent instance configured with message injection rail
        """
        ws, ws_source = self._resolve_subagent_workspace_dir(task.workspace_dir)
        logger.info("[ForkAgent] Final workspace_dir=%s, source=%s", ws, ws_source)

        config_base = get_config()
        language = config_base.get("preferred_language", "zh")

        base_prompt = build_subagent_base_prompt(
            language=language,
            workspace_dir=ws,
            include_time=True,
        )

        if language == "zh":
            role_prompt = f"""---

# Fork Agent Role

你是一个 AI 助手的 fork 子代理，角色为 {task.role_id}。
你继承了父代理的上下文，专门执行父代理分派的特定任务。
使用继承的上下文和可用工具执行给定任务。
"""
        else:
            role_prompt = f"""---

# Fork Agent Role

You are a fork subagent of an AI assistant, with role {task.role_id}.
You inherit parent agent's context and execute tasks assigned by the parent agent.
Execute the given task using inherited context and available tools.
"""

        augmented_prompt = base_prompt + role_prompt

        card = AgentCard(
            name=f"fork_{task.role_id}",
            id=task.task_id,
        )

        workspace_obj = Workspace(
            root_path=ws,
            language=language,
        )

        fork_agent = create_deep_agent(
            model=self._model,
            card=card,
            system_prompt=augmented_prompt,
            max_iterations=config_base.get("max_iterations", 15),
            workspace=workspace_obj,
            rails=[
                ForkMessageInjectionRail(fork_messages),  # 注入继承的消息
                JiuClawContextEngineeringRail(preset=True),  # 上下文压缩（fork 继承大量消息时尤其重要）
                SubagentContextRail(subagent_id=task.task_id, parent_session=parent_session),
            ],
            language=language,
            enable_task_loop=False,
        )

        self._inherit_tools_for_fork(fork_agent, task.allowed_tools)

        logger.info(f"[ForkAgent] Created fork agent instance, task_id={task.task_id}")
        return fork_agent

    def _inherit_tools_for_fork(
        self,
        fork_agent: DeepAgent,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> None:
        """Inherit tools from parent agent for fork agent.

        Excludes fork_agent to prevent recursive forking.
        """
        excluded_tools = {
            "fork_agent", "spawn_subagent",
            "todo_create", "todo_complete", "todo_insert", "todo_remove", "todo_list",
            "office_claw_list_skills", "office_claw_load_skill",
        }

        try:
            parent_tools = self._parent_agent.ability_manager.list()
            if not parent_tools:
                logger.debug("[ForkAgent] Parent agent has no tools to inherit")
                return

            inherited_count = 0
            for tool in parent_tools:
                try:
                    tool_name = getattr(tool, "name", None)
                    if hasattr(tool, "card") and hasattr(tool.card, "name"):
                        tool_name = tool.card.name

                    if tool_name in excluded_tools:
                        logger.debug(f"[ForkAgent] Skipping excluded tool: {tool_name}")
                        continue

                    if allowed_tools and tool_name not in allowed_tools:
                        logger.debug(f"[ForkAgent] Skipping tool not in allowed_tools: {tool_name}")
                        continue

                    if hasattr(tool, "card"):
                        fork_agent.ability_manager.add(tool.card)
                    else:
                        fork_agent.ability_manager.add(tool)
                    inherited_count += 1
                except Exception as e:
                    logger.debug(f"[ForkAgent] Failed to inherit tool: {e}")

            logger.info(f"[ForkAgent] Inherited {inherited_count} tools from parent agent")
        except Exception as e:
            logger.warning(f"[ForkAgent] Failed to inherit tools: {e}")