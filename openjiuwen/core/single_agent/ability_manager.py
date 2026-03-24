# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""AbilityManager Class Definition
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import List, Any, Union, Optional, Tuple, Dict
from pydantic import BaseModel

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import AgentError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage, ToolCall
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    ToolCallInputs,
    rail,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.workflow import WorkflowCard

# Ability type definition
Ability = Union[ToolCard, WorkflowCard, AgentCard, McpServerConfig]


@dataclass
class AddAbilityResult:
    """Ability add result."""
    name: str
    added: bool
    reason: str = ""


class AbilityExecutionError(AgentError):
    """Unified exception for ability execution failures."""

    def __init__(
            self,
            status: StatusCode,
            *,
            msg: Optional[str] = None,
            details: Optional[Any] = None,
            cause: Optional[BaseException] = None,
            tool_message: Optional[ToolMessage] = None,
            **kwargs: Any,
    ):
        super().__init__(
            status=status,
            msg=msg,
            details=details,
            cause=cause,
            **kwargs,
        )
        self.tool_message = tool_message


class AbilityManager:
    """Agent Ability Manager

    Responsibilities:
    - Store available ability Cards for Agent (metadata only, no instances)
    - Provide add/remove/query interfaces for abilities
    - Convert Cards to ToolInfo for LLM usage
    - Execute ability calls (get instances from ResourceManager)
    """

    def __init__(self):
        self._tools: Dict[str, ToolCard] = {}
        self._workflows: Dict[str, WorkflowCard] = {}
        self._agents: Dict[str, AgentCard] = {}
        self._mcp_servers: Dict[str, McpServerConfig] = {}
        self._context_engine = None

    def set_context_engine(self, context_engine) -> None:
        self._context_engine = context_engine

    @staticmethod
    def _normalize_tool_calls(
            tool_call: Union[ToolCall, List[ToolCall]],
    ) -> List[ToolCall]:
        tool_calls: List[ToolCall] = []
        if isinstance(tool_call, list):
            tool_calls.extend(tool_call)
        elif isinstance(tool_call, ToolCall):
            tool_calls.append(tool_call)
        else:
            logger.warning(
                f"execute ability input tool call is invalid, {type(tool_call)}!"
            )
        return tool_calls

    @staticmethod
    def _build_execution_error(
            tool_call: ToolCall,
            message: str,
    ) -> AbilityExecutionError:
        return AbilityExecutionError(
            status=StatusCode.AGENT_TOOL_EXECUTION_ERROR,
            msg=message,
            error_msg=message,
            tool_message=ToolMessage(
                content=message,
                tool_call_id=tool_call.id,
            ),
        )

    def add(
            self,
            ability: Union[Ability, List[Ability]]
    ) -> Union[AddAbilityResult, List[AddAbilityResult]]:
        """Add an ability

        Args:
            ability: Ability Card to add

        Returns:
            AddAbilityResult for single ability input,
            or List[AddAbilityResult] for list input
        """

        def add_single_ability(_ability: Ability) -> AddAbilityResult:
            if isinstance(_ability, ToolCard):
                existing = self._tools.get(_ability.name)
                if existing is not None:
                    logger.warning(
                        f"Duplicate tool ability detected: "
                        f"name='{_ability.name}', "
                        f"existing_id='{existing.id}', "
                        f"new_id='{_ability.id}'. "
                        f"Keep existing ability and skip new one."
                    )
                    return AddAbilityResult(
                        name=_ability.name,
                        added=False,
                        reason="duplicate_tool",
                    )
                self._tools[_ability.name] = _ability
                return AddAbilityResult(
                    name=_ability.name,
                    added=True,
                    reason="added_tool",
                )

            elif isinstance(_ability, WorkflowCard):
                existing = self._workflows.get(_ability.name)
                if existing is not None:
                    logger.warning(
                        f"Duplicate workflow ability detected: "
                        f"name='{_ability.name}', "
                        f"existing_id='{existing.id}', "
                        f"new_id='{_ability.id}'. "
                        f"Keep existing ability and skip new one."
                    )
                    return AddAbilityResult(
                        name=_ability.name,
                        added=False,
                        reason="duplicate_workflow",
                    )
                self._workflows[_ability.name] = _ability
                return AddAbilityResult(
                    name=_ability.name,
                    added=True,
                    reason="added_workflow",
                )

            elif isinstance(_ability, AgentCard):
                existing = self._agents.get(_ability.name)
                if existing is not None:
                    logger.warning(
                        f"Duplicate agent ability detected: "
                        f"name='{_ability.name}', "
                        f"existing_id='{existing.id}', "
                        f"new_id='{_ability.id}'. "
                        f"Keep existing ability and skip new one."
                    )
                    return AddAbilityResult(
                        name=_ability.name,
                        added=False,
                        reason="duplicate_agent",
                    )
                self._agents[_ability.name] = _ability
                return AddAbilityResult(
                    name=_ability.name,
                    added=True,
                    reason="added_agent",
                )

            elif isinstance(_ability, McpServerConfig):
                existing = self._mcp_servers.get(_ability.server_name)
                if existing is not None:
                    logger.warning(
                        f"Duplicate MCP server ability detected: "
                        f"name='{_ability.server_name}', "
                        f"existing_id='{existing.server_id}', "
                        f"new_id='{_ability.server_id}'. "
                        f"Keep existing ability and skip new one."
                    )
                    return AddAbilityResult(
                        name=_ability.server_name,
                        added=False,
                        reason="duplicate_mcp_server",
                    )
                self._mcp_servers[_ability.server_name] = _ability
                return AddAbilityResult(
                    name=_ability.server_name,
                    added=True,
                    reason="added_mcp_server",
                )

            logger.warning(f"Unknown ability type: {type(_ability)}")
            return AddAbilityResult(
                name=getattr(_ability, "name", str(type(_ability))),
                added=False,
                reason="unknown_ability_type",
            )

        if isinstance(ability, list):
            return [add_single_ability(item) for item in ability]

        return add_single_ability(ability)

    def remove(self, name: Union[str, List[str]]) -> Union[None, Ability, List[Ability]]:
        """Remove an ability by name

        Args:
            name: Ability name to remove

        Returns:
            Removed ability Card, or None if not found
        """
        if isinstance(name, str):
            removed = None
            if name in self._tools:
                removed = self._tools.pop(name, None)
            if name in self._workflows:
                removed = self._workflows.pop(name, None)
            if name in self._agents:
                removed = self._agents.pop(name, None)
            if name in self._mcp_servers:
                # Remove MCP server and its tools
                mcp_server = self._mcp_servers.pop(name, None)
                if mcp_server:
                    # Remove all tools belonging to this MCP server
                    server_id = mcp_server.server_id
                    tools_to_remove = [
                        tool_name for tool_name, tool_card in self._tools.items()
                        if tool_card.id and tool_card.id.startswith(f"{server_id}.")
                    ]
                    for tool_name in tools_to_remove:
                        self._tools.pop(tool_name, None)
                removed = mcp_server
            return removed
        elif isinstance(name, list):
            result = []
            for item in name:
                removed = None
                if item in self._tools:
                    removed = self._tools.pop(item, None)
                if item in self._workflows:
                    removed = self._workflows.pop(item, None)
                if item in self._agents:
                    removed = self._agents.pop(item, None)
                if item in self._mcp_servers:
                    # Remove MCP server and its tools
                    mcp_server = self._mcp_servers.pop(item, None)
                    if mcp_server:
                        # Remove all tools belonging to this MCP server
                        server_id = mcp_server.server_id
                        tools_to_remove = [
                            tool_name for tool_name, tool_card in self._tools.items()
                            if tool_card.id and tool_card.id.startswith(f"{server_id}.")
                        ]
                        for tool_name in tools_to_remove:
                            self._tools.pop(tool_name, None)
                    removed = mcp_server
                result.append(removed)
            return result
        else:
            return None

    def get(self, name: str) -> Optional[Ability]:
        """Get an ability Card by name

        Args:
            name: Ability name

        Returns:
            Ability Card, or None if not found
        """
        if name in self._tools:
            return self._tools[name]
        if name in self._workflows:
            return self._workflows[name]
        if name in self._agents:
            return self._agents[name]
        if name in self._mcp_servers:
            return self._mcp_servers[name]
        return None

    def list(self) -> List[Ability]:
        """List all ability Cards

        Returns:
            List of all ability Cards
        """
        abilities: List[Ability] = []
        abilities.extend(self._tools.values())
        abilities.extend(self._workflows.values())
        abilities.extend(self._agents.values())
        abilities.extend(self._mcp_servers.values())
        return abilities

    async def list_tool_info(
            self,
            names: Optional[List[str]] = None,
            mcp_server_name: Optional[str] = None
    ) -> List[ToolInfo]:
        """Get ToolInfo list (for LLM usage)

        Args:
            names: Filter by ability names (optional)
            mcp_server_name: Filter by MCP server name (optional)

        Returns:
            List of ToolInfo objects for LLM
        """
        tool_infos: List[ToolInfo] = []

        # Convert ToolCards to ToolInfo
        for name, tool_card in self._tools.items():
            if names is None or name in names:
                id_in_tool_card = tool_card.id
                if not self._is_tool_in_mcp_server(id_in_tool_card):
                    tool_info = ToolInfo(
                        name=tool_card.name,
                        description=tool_card.description or "",
                        parameters=tool_card.input_params or {}
                    )
                    tool_infos.append(tool_info)

        # Convert WorkflowCards to ToolInfo
        for name, workflow_card in self._workflows.items():
            if names is None or name in names:
                tool_info = ToolInfo(
                    name=workflow_card.name,
                    description=workflow_card.description or "",
                    parameters=workflow_card.input_params or {}
                )
                tool_infos.append(tool_info)

        # Convert AgentCards to ToolInfo
        for name, agent_card in self._agents.items():
            if names is None or name in names:
                # Build parameters from input_params
                # input_params can be: None, dict (JSON Schema), or Type[BaseModel]
                if agent_card.input_params is None:
                    params = {"type": "object", "properties": {}, "required": []}
                elif isinstance(agent_card.input_params, dict):
                    # Already a JSON Schema dict, use directly
                    params = agent_card.input_params
                elif isinstance(agent_card.input_params, type) and issubclass(agent_card.input_params, BaseModel):
                    # BaseModel type, convert to JSON Schema
                    params = agent_card.input_params.model_json_schema()
                else:
                    # Fallback to default JSON Schema for unknown types
                    params = {"type": "object", "properties": {}, "required": []}

                tool_info = ToolInfo(
                    name=agent_card.name,
                    description=agent_card.description or "",
                    parameters=params
                )
                tool_infos.append(tool_info)

        # Handle MCP servers if needed
        for mcp_server_name, mcp_server in self._mcp_servers.items():
            mcp_server_id = mcp_server.server_id
            from openjiuwen.core.runner import Runner
            if names is None:
                mcp_tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_id=mcp_server_id)
                for mcp_tool in mcp_tool_infos:
                    mcp_tool_name = mcp_tool.name
                    mcp_tool_id = f'{mcp_server_id}.{mcp_server_name}.{mcp_tool_name}'
                    self._tools[mcp_tool.name] = ToolCard(id=mcp_tool_id, name=mcp_tool_name,
                                                          description=mcp_tool.description,
                                                          input_params=mcp_tool.parameters or {})
                    tool_infos.append(mcp_tool)

        return tool_infos

    async def execute(
            self,
            ctx: AgentCallbackContext,
            tool_call: Union[ToolCall, List[ToolCall]],
            session: Session,
            tag=None
    ) -> List[Tuple[Any, ToolMessage]]:
        """Execute ability call(s) with per-tool rail hooks.

        Get instance from Runner.resource_mgr by card info, execute and return

        Args:
            ctx: Shared callback context for tool-call lifecycle
            tool_call: Single tool call or list of tool calls
            session: Session instance

        Returns:
            List of (result, ToolMessage) tuples
        """
        tool_calls = self._normalize_tool_calls(tool_call)
        if not tool_calls:
            return []

        # Each tool call gets an isolated callback context to avoid races
        # between concurrent BEFORE/AFTER_TOOL_CALL hooks.
        tool_contexts: List[AgentCallbackContext] = []
        tasks = []
        for single_tool_call in tool_calls:
            tool_ctx = AgentCallbackContext(
                agent=ctx.agent,
                inputs=ToolCallInputs(
                    tool_call=single_tool_call,
                    tool_name=single_tool_call.name,
                    tool_args=single_tool_call.arguments,
                ),
                config=ctx.config,
                session=session,
                context=ctx.context,
                extra=ctx.extra,
            )
            tool_contexts.append(tool_ctx)
            tasks.append(
                self._railed_execute_single_tool_call(
                    ctx=tool_ctx,
                    tool_call=single_tool_call,
                    session=session,
                    tag=tag,
                )
            )

        # Execute all tool calls in parallel.
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        final_results: List[Tuple[Any, ToolMessage]] = []
        for i, result in enumerate(results):
            tool_ctx = tool_contexts[i]
            if isinstance(result, Exception):
                # Handle exception
                error_msg = f"Ability execution error: {str(result)}"
                logger.error(error_msg)
                tool_result = None
                tool_message = None
                if isinstance(tool_ctx.inputs, ToolCallInputs):
                    tool_result = tool_ctx.inputs.tool_result
                    tool_message = tool_ctx.inputs.tool_msg

                if (
                        tool_message is None
                        and isinstance(result, AbilityExecutionError)
                ):
                    tool_message = result.tool_message

                if tool_message is None:
                    tool_message = ToolMessage(
                        content=error_msg,
                        tool_call_id=tool_calls[i].id
                    )

                final_results.append((tool_result, tool_message))
                continue

            # AFTER_TOOL_CALL rails can rewrite tool_result/tool_msg in ctx.inputs.
            if isinstance(tool_ctx.inputs, ToolCallInputs):
                tool_result = (
                    tool_ctx.inputs.tool_result
                    if tool_ctx.inputs.tool_result is not None
                    else result[0]
                )
                tool_msg = (
                    tool_ctx.inputs.tool_msg
                    if tool_ctx.inputs.tool_msg is not None
                    else result[1]
                )
                final_results.append((tool_result, tool_msg))
                continue

            final_results.append(result)

        # Propagate force_finish signal from any tool_ctx back to the parent ctx.
        for tool_ctx in tool_contexts:
            ff = tool_ctx.consume_force_finish()
            if ff is not None:
                ctx.request_force_finish(ff.result)
                break

        return final_results

    @rail(
        before=AgentCallbackEvent.BEFORE_TOOL_CALL,
        after=AgentCallbackEvent.AFTER_TOOL_CALL,
        on_exception=AgentCallbackEvent.ON_TOOL_EXCEPTION,
    )
    async def _railed_execute_single_tool_call(
            self,
            ctx: AgentCallbackContext,
            tool_call: ToolCall,
            session: Session,
            tag=None,
    ) -> Tuple[Any, ToolMessage]:
        """Execute one tool call under rail lifecycle events."""
        if isinstance(ctx.inputs, ToolCallInputs):
            if ctx.inputs.tool_name:
                tool_call.name = ctx.inputs.tool_name
            if ctx.inputs.tool_args is not None:
                tool_call.arguments = ctx.inputs.tool_args

        result, tool_msg = await self._execute_single_tool_call(
            tool_call=tool_call,
            session=session,
            tag=tag,
        )

        if isinstance(ctx.inputs, ToolCallInputs):
            ctx.inputs.tool_call = tool_call
            ctx.inputs.tool_name = tool_call.name
            ctx.inputs.tool_args = tool_call.arguments
            ctx.inputs.tool_result = result
            ctx.inputs.tool_msg = tool_msg

        return result, tool_msg

    async def _run_workflow(
            self,
            workflow: Any,
            workflow_id: str,
            tool_args: Any,
            session: Session,
            tool_call: ToolCall,
    ) -> Tuple[Any, Optional[ToolMessage]]:
        """Run a workflow and return (result, tool_message).

        Returns (WorkflowOutput, None) when INPUT_REQUIRED (interruption).
        Returns (result, ToolMessage) on successful completion.
        Raises AbilityExecutionError on failure (caller wraps in try/except).
        """
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState

        workflow_session = session.create_workflow_session() if session is not None else None
        workflow_context = (
            await self._context_engine.create_context(context_id=workflow_id, session=session)
            if self._context_engine is not None
            else None
        )
        workflow_output = await Runner.run_workflow(
            workflow,
            inputs=tool_args,
            session=workflow_session,
            context=workflow_context,
        )
        if (
            isinstance(workflow_output, WorkflowOutput)
            and workflow_output.state == WorkflowExecutionState.INPUT_REQUIRED
        ):
            return workflow_output, None

        result = workflow_output.result if isinstance(workflow_output, WorkflowOutput) else workflow_output
        return result, ToolMessage(content=str(result), tool_call_id=tool_call.id)

    async def _execute_single_tool_call(self, tool_call: ToolCall, session: Session,
                                        tag=None) -> Tuple[Any, ToolMessage]:
        tool_name = tool_call.name

        # Parse arguments
        try:
            tool_args = (
                json.loads(tool_call.arguments)
                if isinstance(tool_call.arguments, str)
                else tool_call.arguments
            )
        except (json.JSONDecodeError, AttributeError):
            tool_args = {}

        # Check ability type and execute accordingly
        if tool_name in self._tools:
            # Execute Tool - get instance from Runner.resource_mgr
            tool_card = self._tools[tool_name]
            tool_id = tool_card.id or tool_card.name
            from openjiuwen.core.runner import Runner
            tool = Runner.resource_mgr.get_tool(tool_id=tool_id, tag=tag, session=session)
            if not tool:
                raise self._build_execution_error(
                    tool_call,
                    f"Tool instance not found in resource_mgr: {tool_id}",
                )
            try:
                result = await tool.invoke(tool_args, session=session)
            except Exception as e:
                error_msg = f"Tool execution error: {str(e)}"
                logger.error(error_msg)
                raise self._build_execution_error(
                    tool_call,
                    error_msg,
                ) from e
        elif tool_name in self._workflows:
            workflow_card = self._workflows[tool_name]
            workflow_id = workflow_card.id or workflow_card.name
            from openjiuwen.core.runner import Runner
            workflow = await Runner.resource_mgr.get_workflow(workflow_id=workflow_id, tag=tag, session=session)
            if not workflow:
                raise self._build_execution_error(
                    tool_call,
                    f"Workflow instance not found in resource_mgr: {workflow_id}"
                )
            try:
                return await self._run_workflow(workflow, workflow_id, tool_args, session, tool_call)
            except Exception as e:
                error_msg = f"Workflow execution error: {str(e)}"
                logger.error(error_msg)
                raise self._build_execution_error(tool_call, error_msg) from e
        elif tool_name in self._agents:
            # Execute sub-Agent - get instance from Runner.resource_mgr
            agent_card = self._agents[tool_name]
            agent_id = agent_card.id or agent_card.name
            from openjiuwen.core.runner import Runner
            agent = await Runner.resource_mgr.get_agent(agent_id=agent_id, session=session)
            if not agent:
                raise self._build_execution_error(
                    tool_call,
                    f"Agent instance not found in resource_mgr: {agent_id}"
                )
            try:
                result = await agent.invoke(tool_args)
            except Exception as e:
                error_msg = f"Agent execution error: {str(e)}"
                logger.error(error_msg)
                raise self._build_execution_error(
                    tool_call,
                    error_msg,
                ) from e
        elif tool_name in self._mcp_servers:
            # Execute MCP tool
            raise self._build_execution_error(
                tool_call,
                f"MCP tool execution not yet implemented: {tool_name}",
            )
        else:
            # Fallback: try to get tool from Runner.resource_mgr by name
            from openjiuwen.core.runner import Runner
            tool = Runner.resource_mgr.get_tool(tool_id=tool_name, tag=tag, session=session)
            if not tool:
                raise self._build_execution_error(
                    tool_call,
                    f"Ability not found in resource_mgr: {tool_name}",
                )
            try:
                result = await tool.invoke(tool_args, session=session)
            except Exception as e:
                error_msg = f"Tool execution error: {str(e)}"
                logger.error(error_msg)
                raise self._build_execution_error(
                    tool_call,
                    error_msg,
                ) from e

        # Build ToolMessage for successful execution.
        content = str(result)
        tool_message = ToolMessage(
            content=content,
            tool_call_id=tool_call.id
        )

        return result, tool_message

    def _is_tool_in_mcp_server(self, id_in_tool_card):
        mcp_server_id = [mcp_server.server_id for _, mcp_server in self._mcp_servers.items()]
        return any([id_in_tool_card.startswith(f"{mid}.") for mid in mcp_server_id])
