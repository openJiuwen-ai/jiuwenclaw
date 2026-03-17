# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Callable, Optional, Tuple, List, Type, Union
from pydantic import BaseModel

from openjiuwen.core.common import BaseCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError, build_error
from openjiuwen.core.common.logging import LogEventType, runner_logger as logger

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.tool import Tool, ToolInfo, ToolCard
from openjiuwen.core.multi_agent import BaseGroup, GroupCard
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.resources_manager.base import (
    AgentGroupProvider,
    Error,
    Ok,
    Tag,
    GLOBAL,
    Result,
    TagUpdateStrategy,
    TagMatchStrategy,
    AgentProvider,
    WorkflowProvider,
    ModelProvider)
from openjiuwen.core.runner.resources_manager.resource_registry import ResourceRegistry

from openjiuwen.core.runner.resources_manager.tag_manager import TagMgr
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.sys_operation import SysOperationCard, SysOperation
from openjiuwen.core.sys_operation.tool_adapter import SysOperationToolAdapter
from openjiuwen.core.sys_operation.registry import OperationRegistry
from openjiuwen.core.workflow.workflow import Workflow
from openjiuwen.core.workflow import WorkflowCard


class ResourceMgr:
    """
    Resource Manager for Model, Workflow, Prompt, Tool, Agent, AgentGroup
    """

    def __init__(self, ) -> None:
        self._resource_registry = ResourceRegistry()
        self._tag_mgr = TagMgr()
        self._id_to_card: dict[str, BaseCard] = {}

    async def add_agent_group(self,
                              card: GroupCard,
                              agent_group: AgentGroupProvider,
                              *,
                              tag: Optional[Tag | list[Tag]] = None,
                              ) -> Result[GroupCard, Exception]:
        """
        Add a single agent group to the resource manager.

        Args:
            card: The agent group's metadata card containing configuration and identification.
            agent_group: Callable provider that creates or returns the agent group instance.
            tag: Optional tag(s) for categorizing and filtering the agent group.
                 If None, no tags will be added or updated.

        Returns:
            Result[GroupCard, Exception]: Result object containing the added group card or an exception.
        """
        self._inner_validate_resource_card(card, "group", GroupCard)
        self._inner_validate_resource_id(card.id, "group")
        self._inner_validate_provider(agent_group, "group")
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=card.id,
                                        resource=agent_group,
                                        resource_card=card,
                                        tag=tag,
                                        resource_type="group")

    async def remove_agent_group(self,
                                 *,
                                 group_id: Optional[str | list[str]] = None,
                                 tag: Optional[Tag | list[Tag]] = None,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 skip_if_tag_not_exists: bool = False,
                                 ) -> Result[Optional[GroupCard], Exception] | list[
        Result[Optional[GroupCard], Exception]]:
        """
        Remove agent group(s) by ID or tag.

        Args:
            group_id: Single ID or list of IDs of agent groups to remove.
                Cannot be used together with tag parameter.
            tag: Single tag or list of tags; removes all agent groups with matching tags.
                Cannot be used together with id parameter.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
                ALL - Resource must have all specified tags.
                ANY - Resource must have at least one of the specified tags.
            skip_if_tag_not_exists: If True, silently skip non-existent resources.
        Returns:
            Result[Optional[GroupCard], Exception] or list[Result[Optional[GroupCard], Exception]]:
                Result object(s) containing the removed group card(s) or exception.
        """
        return self._inner_remove_resources(resource_id=group_id,
                                            tag=tag,
                                            tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists,
                                            resource_type="group")

    async def get_agent_group(self,
                              group_id: str = None,
                              *,
                              tag: Optional[Tag | list[Tag]] = None,
                              tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                              session: Optional["Session"] = None
                              ) -> Optional[BaseGroup] | list[Optional[BaseGroup]]:
        """
        Get an agent group instance by ID or tag.

        Args:
            group_id: Unique identifier of the agent group. Either id or tag must be provided.
            tag: Optional tag for filtering when id is provided,
                 or main lookup criteria when id is not provided.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the agent group.
                If provided, the agent group will be initialized with this session.

        Returns:
            BaseGroup instance if found, None otherwise.

        Raises:
            ValueError: When neither id nor tag is provided.
        """
        return await self._inner_get_resources_by_provider(resource_id=group_id,
                                                           tag=tag,
                                                           tag_match_strategy=tag_match_strategy,
                                                           session=session,
                                                           resource_type="group")

    def add_agent(self,
                  card: AgentCard,
                  agent: AgentProvider | RemoteAgent,
                  *,
                  tag: Optional[Tag | list[Tag]] = None
                  ) -> Result[AgentCard, Exception]:
        """
        Add a single agent to the resource manager.

        Args:
            card: The agent's metadata card containing configuration and identification.
            agent: Callable provider that creates or returns the agent instance.
            tag: Optional tag(s) for categorizing and filtering the agent.

        Returns:
            Result[AgentCard, Exception]: Result object containing the added agent card or an exception.
        """
        self._inner_validate_resource_card(card, "agent", AgentCard)
        self._inner_validate_resource_id(card.id, "agent")
        self._inner_validate_provider(agent, "agent")

        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=card.id if card else None,
                                        resource=agent,
                                        resource_card=card,
                                        tag=tag,
                                        resource_type="agent")

    def add_agents(self,
                   agents: list[Tuple[AgentCard, AgentProvider]],
                   *,
                   tag: Optional[Tag | list[Tag]] = None
                   ) -> Result[AgentCard, Exception] | list[Result[AgentCard, Exception]]:
        """
        Add multiple agents in bulk.

        Args:
            agents: List of tuples, each containing (AgentCard, AgentProvider).
            tag: Optional tag(s) to apply to all agents being added.
                Applied in addition to any tags on individual AgentCards.

        Returns:
            Result[AgentCard, Exception] or list[Result[AgentCard, Exception]]:
                Result object(s) containing the added agent card(s) or exception(s).
        """
        self._inner_validate_providers(agents, "agent", AgentCard)
        if tag is not None:
            self._inner_validate_tag(tag)
        results = []
        for card, agent in agents:
            results.append(self._inner_add_resource(resource_id=card.id if card else None,
                                                    resource=agent,
                                                    resource_card=card,
                                                    tag=tag,
                                                    resource_type="agent"))
        return results

    def remove_agent(self,
                     agent_id: str | list[str] = None,
                     *,
                     tag: Optional[Tag | list[Tag]] = GLOBAL,
                     tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                     skip_if_tag_not_exists: bool = False,
                     ) -> Result[Optional[AgentCard], Exception] | list[Result[Optional[AgentCard], Exception]]:
        """
        Remove agent(s) by ID or tag.

        Args:
            agent_id: Single ID or list of IDs of agents to remove.
            tag: Single tag or list of tags; removes all agents with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent resources.

        Returns:
            Result[Optional[AgentCard], Exception] or list[Result[Optional[AgentCard], Exception]]:
                Result object(s) containing the removed agent card(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=agent_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="agent")

    async def get_agent(self,
                        agent_id: str | list[str] = None,
                        *,
                        tag: Optional[Tag | list[Tag]] = None,
                        tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                        session: Optional["Session"] = None
                        ) -> Optional[BaseAgent] | list[Optional[BaseAgent]]:
        """
        Get agent instance(s) by ID or tag.

        Args:
            agent_id: Single ID or list of IDs of agents to retrieve.
            tag: Single tag or list of tags; returns all agents with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the agents.

        Returns:
            BaseAgent or list[BaseAgent]: Agent instance(s) if found, None otherwise.
        """
        return await self._inner_get_resources_by_provider(resource_id=agent_id, tag=tag,
                                                           tag_match_strategy=tag_match_strategy, session=session,
                                                           resource_type="agent")

    def add_workflow(self,
                     card: WorkflowCard,
                     workflow: WorkflowProvider,
                     *,
                     tag: Optional[Tag | list[Tag]] = None
                     ) -> Result[WorkflowCard, Exception]:
        """
        Add a single workflow to the resource manager.

        Args:
            card: The workflow's metadata card containing configuration and identification.
            workflow: Callable provider that creates or returns the workflow instance.
            tag: Optional tag(s) for categorizing and filtering the workflow.

        Returns:
            Result[WorkflowCard, Exception]: Result object containing the added workflow card or an exception.
        """
        self._inner_validate_resource_card(card, "workflow", WorkflowCard)
        self._inner_validate_resource_id(card.id, "workflow")
        self._inner_validate_provider(workflow, "workflow")
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=card.id if card else None,
                                        resource=workflow,
                                        resource_card=card,
                                        tag=tag,
                                        resource_type="workflow")

    def add_workflows(self,
                      workflows: list[Tuple[WorkflowCard, WorkflowProvider]],
                      *,
                      tag: Optional[Tag | list[Tag]] = None
                      ) -> Result[WorkflowCard, Exception] | list[Result[WorkflowCard, Exception]]:
        """
        Add multiple workflows in bulk.

        Args:
            workflows: List of tuples, each containing (WorkflowCard, WorkflowProvider).
            tag: Optional tag(s) to apply to all workflows being added.

        Returns:
            Result[WorkflowCard, Exception] or list[Result[WorkflowCard, Exception]]:
                Result object(s) containing the added workflow card(s) or exception(s).
        """
        self._inner_validate_providers(workflows, "workflow", WorkflowCard)
        if tag is not None:
            self._inner_validate_tag(tag)
        results = []
        for card, workflow in workflows:
            results.append(self._inner_add_resource(resource_id=card.id if card else None,
                                                    resource=workflow,
                                                    resource_card=card,
                                                    tag=tag,
                                                    resource_type="workflow"))
        return results

    def remove_workflow(self,
                        workflow_id: str | list[str] = None,
                        *,
                        tag: Optional[Tag | list[Tag]] = None,
                        tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                        skip_if_tag_not_exists: bool = False,
                        ) -> Result[Optional[WorkflowCard], Exception] | list[
        Result[Optional[WorkflowCard], Exception]]:
        """
        Remove workflow(s) by ID or tag.

        Args:
            workflow_id: Single ID or list of IDs of workflows to remove.
            tag: Single tag or list of tags; removes all workflows with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent workflows.

        Returns:
            Result[Optional[WorkflowCard], Exception] or list[Result[Optional[WorkflowCard], Exception]]:
                Result object(s) containing the removed workflow card(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=workflow_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="workflow")

    async def get_workflow(self,
                           workflow_id: str | list[str] = None,
                           *,
                           tag: Optional[Tag | list[Tag]] = None,
                           tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                           session: Optional["Session"] = None
                           ) -> Optional[Workflow] | list[Optional[Workflow]]:
        """
        Get workflow instance(s) by ID or tag.

        Args:
            workflow_id: Single ID or list of IDs of workflows to retrieve.
            tag: Single tag or list of tags; returns all workflows with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the workflows.

        Returns:
            Workflow or list[Workflow]: Workflow instance(s) if found, None otherwise.
        """
        return await self._inner_get_resources_by_provider(resource_id=workflow_id, tag=tag,
                                                           tag_match_strategy=tag_match_strategy, session=session,
                                                           resource_type="workflow")

    def add_tool(self,
                 tool: Tool | list[Tool],
                 *,
                 tag: Optional[Tag | list[Tag]] = None
                 ) -> Result[ToolCard, Exception] | list[Result[ToolCard, Exception]]:
        """
        Add tool(s) to the resource manager.

        Args:
            tool: Single Tool instance or list of Tool instances to add.
            tag: Optional tag(s) for categorizing and filtering the tool(s).

        Returns:
            Result[ToolCard, Exception] or list[Result[ToolCard, Exception]]:
                Result object(s) containing the added tool card(s) or exception(s).
        """
        self._inner_validate_resource(tool, "tool", Tool)
        if tag is not None:
            self._inner_validate_tag(tag)
        if isinstance(tool, Tool):
            return self._inner_add_resource(resource_id=tool.card.id if tool.card else None,
                                            resource=tool,
                                            resource_card=tool.card,
                                            tag=tag,
                                            resource_type="tool")
        results = []
        for item in tool:
            results.append(self._inner_add_resource(resource_id=item.card.id,
                                                    resource=item,
                                                    resource_card=item.card,
                                                    tag=tag,
                                                    resource_type="tool"))
        return results

    def get_tool(self,
                 tool_id: str | list[str] = None,
                 *,
                 tag: Optional[Tag | list[Tag]] = None,
                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                 session: Optional["Session"] = None
                 ) -> Optional[Tool] | list[Optional[Tool]]:
        """
        Get tool(s) by ID or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to retrieve.
            tag: Single tag or list of tags; returns all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the tools.

        Returns:
            Tool or list[Tool]: Tool instance(s) if found, None otherwise.
        """
        return self._inner_get_resources(resource_id=tool_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                         session=session, resource_type="tool")

    def remove_tool(self,
                    tool_id: str | list[str] = None,
                    *,
                    tag: Optional[Tag | list[Tag]] = None,
                    tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                    skip_if_tag_not_exists: bool = False,
                    ) -> Result[Optional[ToolCard], Exception] | list[Result[Optional[ToolCard], Exception]]:
        """
        Remove tool(s) by ID or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to remove.
            tag: Single tag or list of tags; removes all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent tag.

        Returns:
            Result[Optional[ToolCard], Exception] or list[Result[Optional[ToolCard], Exception]]:
                Result object(s) containing the removed tool card(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=tool_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="tool")

    def add_model(self,
                  model_id: str,
                  model: ModelProvider,
                  *,
                  tag: Optional[Tag | list[Tag]] = None
                  ) -> Result[str, Exception]:
        """
        Add a model to the resource manager.

        Args:
            model_id: Unique identifier for the model.
            model: Callable provider that creates or returns the model instance.
            tag: Optional tag(s) for categorizing and filtering the model.

        Returns:
            Result[str, Exception]: Result object containing the model ID or an exception.
        """
        self._inner_validate_resource_id(model_id, "model")
        self._inner_validate_provider(model, "model")
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=model_id,
                                        resource=model,
                                        tag=tag,
                                        resource_type="model")

    def add_models(self,
                   models: list[Tuple[str, ModelProvider]],
                   *,
                   tag: Optional[Tag | list[Tag]] = None
                   ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add multiple models in bulk.

        Args:
            models: List of tuples, each containing (model_id, ModelProvider).
            tag: Optional tag(s) to apply to all models being added.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the model ID(s) or exception(s).
        """
        self._inner_validate_providers(models, "model")
        if tag is not None:
            self._inner_validate_tag(tag)
        results = []
        for model_id, model in models:
            results.append(self._inner_add_resource(resource_id=model_id,
                                                    resource=model,
                                                    tag=tag,
                                                    resource_type="model"))
        return results

    def remove_model(self,
                     *,
                     model_id: str | list[str] = None,
                     tag: Optional[Tag | list[Tag]] = None,
                     tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                     skip_if_tag_not_exists: bool = False,
                     ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove model(s) by ID or tag.

        Args:
            model_id: Single ID or list of IDs of models to remove.
            tag: Single tag or list of tags; removes all models with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent models.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the model ID(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=model_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="model")

    async def get_model(self,
                        model_id: str | list[str] = None,
                        *,
                        tag: Optional[Tag | list[Tag]] = None,
                        tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                        session: Optional["Session"] = None) \
            -> Optional[BaseModel] | list[Optional[BaseModel]]:
        """
        Get model instance(s) by ID or tag.

        Args:
            model_id: Single ID or list of IDs of models to retrieve.
            tag: Single tag or list of tags; returns all models with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the models.

        Returns:
            Model or list[Model]: Model instance(s) if found, None otherwise.
        """
        return await self._inner_get_resources_by_provider(resource_id=model_id, tag=tag,
                                                           tag_match_strategy=tag_match_strategy, session=session,
                                                           resource_type="model")

    def add_prompt(self,
                   prompt_id: str,
                   template: PromptTemplate,
                   *,
                   tag: Optional[Tag | list[Tag]] = None,
                   ) -> Result[str, Exception]:
        """
        Add a prompt template to the resource manager.

        Args:
            prompt_id: Unique identifier for the prompt template.
            template: PromptTemplate instance containing the prompt content and configuration.
            tag: Optional tag(s) for categorizing and filtering the prompt.

        Returns:
            Result[str, Exception]: Result object containing the prompt ID or an exception.
        """
        self._inner_validate_resource_id(prompt_id, "prompt")
        self._inner_validate_resource(template, "prompt", PromptTemplate)
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=prompt_id, resource=template, tag=tag, resource_type="prompt")

    def add_prompts(self,
                    prompts: list[Tuple[str, PromptTemplate]],
                    *,
                    tag: Optional[Tag | list[Tag]] = None
                    ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add multiple prompt templates in bulk.

        Args:
            prompts: List of tuples, each containing (prompt_id, PromptTemplate).
            tag: Optional tag(s) to apply to all prompts being added.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the prompt ID(s) or exception(s).
        """
        self._inner_validate_resource(prompts, "prompt", PromptTemplate)
        if tag is not None:
            self._inner_validate_tag(tag)
        result = []
        for prompt_id, prompt in prompts:
            result.append(
                self._inner_add_resource(resource_id=prompt_id, resource=prompt, tag=tag, resource_type="prompt"))
        return result

    def remove_prompt(self,
                      prompt_id: str | list[str] = None,
                      *,
                      tag: Optional[Tag | list[Tag]] = None,
                      tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                      skip_if_tag_not_exists: bool = False,
                      ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove prompt template(s) by ID or tag.

        Args:
            prompt_id: Single ID or list of IDs of prompts to remove.
            tag: Single tag or list of tags; removes all prompts with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent prompts.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the prompt ID(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=prompt_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists,
                                            resource_type="prompt")

    def get_prompt(self,
                   prompt_id: str | list[str] = None,
                   *,
                   tag: Optional[Tag | list[Tag]] = None,
                   tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                   ) -> Optional[PromptTemplate] | list[Optional[PromptTemplate]]:
        """
        Get prompt template(s) by ID or tag.

        Args:
            prompt_id: Single ID or list of IDs of prompts to retrieve.
            tag: Single tag or list of tags; returns all prompts with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.

        Returns:
            PromptTemplate or list[PromptTemplate]: Prompt template instance(s) if found, None otherwise.
        """
        return self._inner_get_resources(resource_id=prompt_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                         resource_type="prompt")

    def add_sys_operation(self,
                          card: SysOperationCard | List[SysOperationCard],
                          *,
                          tag: Optional[Tag | List[Tag]] = None
                          ) -> Union[Result[SysOperationCard, Exception], List[Result[SysOperationCard, Exception]]]:
        """Add sys operation via SysOperationCard (with optional tags).

        Args:
            card: SysOperationCard with valid `id` (required)
            tag: Optional single/tag list for classification

        Returns:
            Result[SysOperationCard, Exception] or List[Result[SysOperationCard, Exception]]]:
             Success card or error
        """
        cards = [card] if isinstance(card, SysOperationCard) else (card or [])

        results = []
        for single_card in cards:
            self._inner_validate_resource_card(single_card, "sys_operation", SysOperationCard)
            if tag is not None:
                self._inner_validate_tag(tag)
            instance = SysOperation(single_card)
            res = self._inner_add_resource(resource_id=single_card.id,
                                           resource=instance,
                                           resource_card=single_card,
                                           tag=tag,
                                           resource_type="sys_operation")
            if res.is_ok():
                self._register_sys_operation_tools(single_card, instance, tag=tag)
            results.append(res)

        return results[0] if isinstance(card, SysOperationCard) else results

    def remove_sys_operation(self,
                             sys_operation_id: str | List[str],
                             *,
                             tag: Optional[Tag | List[Tag]] = GLOBAL,
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                             skip_if_tag_not_exists: bool = False,
                             ) -> Union[Result[Optional[SysOperationCard], Exception],
    List[Result[Optional[SysOperationCard], Exception]]]:
        """Remove sys operation(s) by ID/tag (supports batch).

        Args:
            sys_operation_id: Single ID or list of IDs of sys_operation to remove
            tag: Optional single/tag list filter (if no ID)
            tag_match_strategy: ALL/ANY for tag matching (default: ALL)
            skip_if_tag_not_exists: Ignore missing tags (default: False)

        Returns:
            Result/Result list: Removed card(s) or error
        """
        results = self._inner_remove_resources(resource_id=sys_operation_id,
                                               tag=tag,
                                               tag_match_strategy=tag_match_strategy,
                                               skip_if_tag_not_exists=skip_if_tag_not_exists,
                                               resource_type="sys_operation")
        sys_op_ids = [sys_operation_id] if isinstance(sys_operation_id, str) else (sys_operation_id or [])

        tool_ids_to_remove = []
        for op_id in sys_op_ids:
            ids = self._resource_registry.tool().remove_sys_operation_tools(op_id)
            if ids:
                tool_ids_to_remove.extend(ids)

        if tool_ids_to_remove:
            self._inner_remove_resources(resource_id=tool_ids_to_remove,
                                         tag=tag,
                                         resource_type="tool", skip_if_tag_not_exists=skip_if_tag_not_exists)
        return results

    def _register_sys_operation_tools(self, card: SysOperationCard, instance: SysOperation,
                                      tag: Optional[Tag | List[Tag]] = None):
        """Automatically register operation methods as tools."""
        tools = SysOperationToolAdapter.extract_tools(card, instance)

        tool_ids = []
        # Register each tool
        for tool_id, local_func in tools:
            self._inner_add_resource(resource_id=tool_id,
                                     resource=local_func,
                                     resource_card=local_func.card,
                                     tag=tag,
                                     resource_type="tool")
            tool_ids.append(tool_id)

        self._resource_registry.tool().add_sys_operation_tools(card.id, tool_ids)

    def get_sys_operation(self,
                          sys_operation_id: str | List[str] = None,
                          *,
                          tag: Optional[Tag | List[Tag]] = None,
                          tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                          session: Optional["Session"] = None
                          ) -> Union[Optional[SysOperation], List[Optional[SysOperation]]]:
        """Get sys operation(s) by ID/tag.

        Args:
            sys_operation_id: Single ID or list of IDs of sys_operation
            tag: Optional single/tag list filter (if no ID)
            tag_match_strategy: ALL/ANY for tag matching (default: ALL)
            session: Optional context session

        Returns:
            SysOperation/List[SysOperation]: Matching operation(s) or None
        """
        return self._inner_get_resources(resource_id=sys_operation_id,
                                         tag=tag,
                                         tag_match_strategy=tag_match_strategy,
                                         session=session,
                                         resource_type="sys_operation")

    def get_sys_op_tool_cards(self, sys_operation_id: str,
                              *,
                              operation_name: str | List[str] = None,
                              tool_name: str | List[str] = None) -> ToolCard | List[ToolCard] | None:
        """
        Get tool cards from a system operation.

        Args:
            sys_operation_id: The ID of the system operation.
            operation_name: Single operation name or list of operation names (e.g., "fs", ["fs", "shell"]).
                If None, returns all tool cards from all operations.
            tool_name: Single tool name or list of tool names (e.g., "read_file", ["read_file", "write_file"]).
                Only valid when operation_name is a single string. Cannot be used when operation_name is a list.
                If None, returns all tool cards from the specified operation(s).

        Returns:
            ToolCard or List[ToolCard] or None: Tool card(s) matching the criteria.

        Raises:
            ValidationError: If operation_name is a list and tool_name is also provided.

        Examples:
            # Scenario 1: Get a single tool card, returns: ToolCard
            tool_card = get_sys_op_tool_cards(sys_operation_id, operation_name="fs", tool_name="read_file")

            # Scenario 2: Get multiple tool cards from the same operation, returns: List[ToolCard]
            tool_cards = get_sys_op_tool_cards(sys_operation_id, operation_name="fs",
                                               tool_name=["read_file", "write_file"])

            # Scenario 3: Get all tool cards from a single operation, returns: List[ToolCard]
            tool_cards = get_sys_op_tool_cards(sys_operation_id, operation_name="fs")

            # Scenario 4: Get all tool cards from multiple operations, returns: List[ToolCard]
            tool_cards = get_sys_op_tool_cards(sys_operation_id, operation_name=["fs", "shell"])

            # Scenario 5: Get all tool cards from all operations, returns: List[ToolCard]
            tool_cards = get_sys_op_tool_cards(sys_operation_id)
        """
        if isinstance(operation_name, list) and tool_name is not None:
            raise build_error(StatusCode.RESOURCE_VALUE_INVALID, resource_type="sys_operation",
                              reason="tool_name cannot be specified when operation_name is a list")

        sys_op = self._resource_registry.sys_operation().get_sys_operation(sys_operation_id)
        if sys_op is None:
            return None

        if operation_name is None:
            operation_names = OperationRegistry.get_supported_operations(sys_op.mode)
        else:
            operation_names = [operation_name] if isinstance(operation_name, str) else operation_name

        result = []
        tool_names = [tool_name] if isinstance(tool_name, str) else tool_name

        for op_name in operation_names:
            if tool_names is None:
                tool_ids = self._resource_registry.tool().get_sys_operation_tool_ids(sys_operation_id)
                for tool_id in tool_ids:
                    if tool_id.startswith(f"{sys_operation_id}.{op_name}."):
                        card = self._id_to_card.get(tool_id)
                        if card:
                            result.append(card)
            else:
                for t_name in tool_names:
                    tool_id = SysOperationCard.generate_tool_id(sys_operation_id, op_name, t_name)
                    card = self._id_to_card.get(tool_id)
                    if card:
                        result.append(card)

        if tool_name is not None and isinstance(tool_name, str):
            return result[0] if result else None
        return result

    async def get_tool_infos(self,
                             tool_id: str | list[str] = None,
                             *,
                             tool_type: str | list[str] = None,
                             tag: Optional[Tag | list[Tag]] = None,
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                             ignore_exception: bool = False,
                             ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]:
        """
        Get tool information/metadata by ID, type, or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to get info for.
            type: Single type or list of types to filter tools by.
                Common types: ["function", "mcp", "workflow", "agent", "group"].
            tag: Single tag or list of tags; returns info for all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions and return None for failing items.

        Returns:
            ToolInfo or list[ToolInfo]: Tool information instance(s) if found, None otherwise.
        """
        ids_to_get, exact_match = self._inner_find_resource_ids(resource_id=tool_id, tag=tag,
                                                                tag_match_strategy=tag_match_strategy)
        results = []
        if not ids_to_get:
            return results
        types = []
        if tool_type is not None:
            types = [tool_type] if isinstance(tool_type, str) else tool_type
        for resource_id in ids_to_get:
            card = self._id_to_card.get(resource_id)
            if types and (self._get_card_type(card) not in types):
                continue
            if card and hasattr(card, "tool_info"):
                results.append(card.tool_info())
                continue
            if exact_match:
                results.append(None)
        return results

    async def add_mcp_server(self,
                             server_config: McpServerConfig | list[McpServerConfig],
                             *,
                             tag: Optional[Tag | list[Tag]] = None,
                             expiry_time: Optional[float] = None
                             ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add MCP (Model Context Protocol) server configuration(s).

        Args:
            server_config: Single or list of McpServerConfig instances.
            tag: Optional tag(s) for categorizing the server(s).
            expiry_time: Optional Unix timestamp when the server configuration expires.
                If None, the configuration does not expire.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        self._inner_validate_server_config(server_config)
        if tag is not None:
            self._inner_validate_tag(tag)
        if expiry_time is not None and expiry_time <= 0:
            raise build_error(StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID, param='expire_time',
                              reason='expire time <= 0')
        add_results = []
        server_configs = [server_config] if isinstance(server_config, McpServerConfig) else server_config
        for config in server_configs:
            try:
                # atomic process
                cards = await self._resource_registry.tool().add_tool_server(config, expiry_time=expiry_time)
                for card in cards:
                    self._id_to_card[card.id] = card
                    self._tag_mgr.tag_resource(card.id, tag if tag else GLOBAL)
                self._tag_mgr.tag_resource(config.server_id, tag if tag else GLOBAL)
                add_results.append(Ok(config.server_id))
                tool_names = [card.name for card in cards]
                logger.info("add mcp server succeed", event_type=LogEventType.RESOURCE_MGR_ADD_RESOURCE_SERVER,
                            resource_id=config.server_id,
                            resource_type="mcp server",
                            tag=tag if tag else GLOBAL,
                            metadata={"tools": tool_names, "server_name": config.server_name})
            except Exception as e:
                add_results.append(Error(e))
                logger.error("add mcp server failed", event_type=LogEventType.RESOURCE_MGR_ADD_RESOURCE_SERVER,
                             resource_id=config.server_id,
                             resource_type="mcp server",
                             tag=tag if tag else GLOBAL,
                             exception=e,
                             metadata={"server_name": config.server_name})

        return add_results if isinstance(server_config, list) else add_results[0]

    async def refresh_mcp_server(self,
                                 server_id: Optional[str | list[str]] = None,
                                 *,
                                 server_name: Optional[str | list[str]] = None,
                                 tag: Optional[Tag | list[Tag]] = None,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 ignore_exception: bool = False,
                                 skip_if_tag_not_exists: bool = False,
                                 ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Refresh MCP server tool card(s) by name.

        Args:
            server_id: Single or list of MCP server ids to refresh
            server_name: Single or list of MCP server names to refresh.
            tag: Optional tag to filter servers to refresh.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, continue refreshing other servers if one fails.
            skip_if_tag_not_exists: If True, skip non-existent servers.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        return []

    async def remove_mcp_server(self,
                                server_id: Optional[str | list[str]] = None,
                                *,
                                server_name: Optional[str | list[str]] = None,
                                tag: Optional[Tag | list[Tag]] = None,
                                tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                skip_if_tag_not_exists: bool = False,
                                ignore_exception: bool = False,
                                ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove MCP server(s) by name or tag.

        Args:
            server_name: Single or list of MCP server names to remove.
            tag: Single tag or list of tags; removes all servers with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent servers.
            ignore_exception: If True, continue removing other servers if one fails.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        server_ids_to_remove, exact_match = self._inner_get_server_ids(server_id, server_name, tag, tag_match_strategy,
                                                                       skip_if_tag_not_exists,
                                                                       StatusCode.RESOURCE_MCP_SERVER_REMOVE_ERROR)
        results = []
        for mcp_server_id in server_ids_to_remove:
            try:
                self._tag_mgr.remove_resource(mcp_server_id)
                tool_ids = await self._resource_registry.tool().remove_tool_server(mcp_server_id)
                if tool_ids:
                    self.remove_tool(tool_id=tool_ids)
                logger.info("remove mcp server succeed", event_type=LogEventType.RESOURCE_MGR_REMOVE_RESOURCE_SERVER,
                            resource_id=mcp_server_id,
                            resource_type="mcp server",
                            tag=tag if tag else GLOBAL,
                            metadata={"tools": tool_ids})
                results.append(Ok(mcp_server_id))
            except Exception as e:
                logger.error("remove mcp server failed", event_type=LogEventType.RESOURCE_MGR_REMOVE_RESOURCE_SERVER,
                             resource_id=mcp_server_id,
                             resource_type="mcp server",
                             tag=tag if tag else GLOBAL,
                             exception=e)
                if not ignore_exception:
                    raise e
                results.append(Error(e))
        return results

    async def get_mcp_tool(self,
                           name: str | list[str] = None,
                           server_id: str | list[str] = None,
                           *,
                           server_name: str | list[str] = None,
                           tag: Optional[Tag | list[Tag]] = None,
                           tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                           skip_if_tag_not_exists: bool = False,
                           ignore_exception: bool = False,
                           session: Optional["Session"] = None
                           ) -> Optional[Tool] | list[Optional[Tool]]:
        """
        Get MCP tool(s) by name and server.

        Args:
            name: Single or list of MCP tool names to retrieve.
            server_name: Single or list of MCP server names containing the tools.
            server_id: Single or list of MCP server IDs containing the tools.
            tag: Optional tag to filter servers/tools.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions for refresh mcp server if needed.
            session: Optional session context for the tools.

        Returns:
            Tool or list[Tool]: MCP tool instance(s) if found, None otherwise.
        """
        server_ids_to_get, exact_match = self._inner_get_server_ids(server_id, server_name, tag, tag_match_strategy,
                                                                    skip_if_tag_not_exists,
                                                                    StatusCode.RESOURCE_MCP_TOOL_GET_ERROR)
        results = []
        tool_names = [name] if isinstance(name, str) else name
        for mcp_server_id in server_ids_to_get:
            try:
                await self._resource_registry.tool().refresh_tool_server(mcp_server_id, skip_not_exist=True)
            except Exception as e:
                if not ignore_exception:
                    raise e
            if tool_names is None:
                results.extend(self._resource_registry.tool().get_mcp_tools(mcp_server_id, session))
                continue
            for tool_name in tool_names:
                tool = self._resource_registry.tool().get_mcp_tool(tool_name, mcp_server_id, session)
                if exact_match:
                    results.append(tool)
                elif tool:
                    results.append(tool)
        return results

    async def get_mcp_tool_infos(self,
                                 name: str | list[str] = None,
                                 server_id: str | list[str] = None,
                                 *,
                                 server_name: str | list[str] = None,
                                 tag: Optional[Tag | list[Tag]] = None,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 skip_if_tag_not_exists: bool = False,
                                 ignore_exception: bool = False,
                                 ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]:
        """
        Get MCP tool information/metadata by name and server.

        Args:
            name: Single or list of MCP tool names to get info for.
                If None, returns info for all tools in specified servers.
            server_name: Single or list of MCP server names containing the tools.
                Must be provided if name is None.
            server_id: Single or list of MCP server IDs containing the tools.
            tag: Optional tag to filter servers/tools.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions for refresh mcp server if needed.

        Returns:
            ToolInfo or list[ToolInfo]: MCP tool information instance(s) if found, None otherwise.
        """
        server_ids_to_get, exact_match = self._inner_get_server_ids(server_id, server_name, tag, tag_match_strategy,
                                                                    skip_if_tag_not_exists,
                                                                    StatusCode.RESOURCE_MCP_TOOL_GET_ERROR)
        tool_names = [name] if isinstance(name, str) else name
        results = []
        for mcp_server_id in server_ids_to_get:
            try:
                await self._resource_registry.tool().refresh_tool_server(mcp_server_id, skip_not_exist=True)
            except Exception as e:
                if not ignore_exception:
                    raise e
            tool_ids = []
            if tool_names is None:
                tool_ids = self._resource_registry.tool().get_mcp_tool_id(mcp_server_id)
            else:
                for tool_name in tool_names:
                    tool_ids.append(self._resource_registry.tool().get_mcp_tool_id(mcp_server_id, tool_name))
            for tool_id in tool_ids:
                tool_card = self._id_to_card.get(tool_id) if tool_id else None
                if exact_match:
                    results.append(tool_card.tool_info() if tool_card else None)
                elif tool_card:
                    results.append(tool_card.tool_info())
        return results

    def get_resource_by_tag(self,
                            tag: Tag) -> Optional[list[BaseCard]]:
        """
        Retrieve all resources associated with a specific tag.

        Args:
            tag: Tag to search for.

        Returns:
            List of BaseCard instances representing resources with the specified tag,
            or None if no resources found.
        """
        self._inner_validate_tag(tag)
        resource_ids = self._tag_mgr.get_tag_resources(tag)
        if not resource_ids:
            return [] if resource_ids is not None else None
        return [self._id_to_card.get(resource_id) for resource_id in resource_ids]

    def list_tags(self) -> list[Tag]:
        """
        List all tags currently in use across all resources.

        Returns:
            List of unique tag strings.
        """
        return self._tag_mgr.list_tags()

    def has_tag(self, tag: str) -> bool:
        """
        Check if the specified tag exists in the resource_mgr.

        Args:
            tag: Tag to check for existence.

        Returns:
            True if tag exists, False otherwise.
        """
        self._inner_validate_tag(tag)
        return self._tag_mgr.has_tag(tag)

    async def remove_tag(self,
                         tag: Tag | list[Tag],
                         *,
                         skip_if_tag_not_exists: bool = False,
                         ) -> Result[Tag, Exception] | list[Result[Tag, Exception]]:
        """
        Remove tag(s) from all resources.

        Args:
            tag: Single tag or list of tags to remove from all resources.
            skip_if_tag_not_exists: If True, ignore non-existent tags.

        Returns:
            Result[Tag, Exception] or list[Result[Tag, Exception]]:
                Result object(s) containing the tag(s) or exception(s).
        """
        self._inner_validate_tag(tag)
        tags_to_remove = tag if isinstance(tag, list) else [tag]
        results = []
        for single_tag in tags_to_remove:
            resource_to_removal = self._tag_mgr.remove_tag(tag=single_tag,
                                                           skip_if_not_exists=skip_if_tag_not_exists)
            for resource_id in resource_to_removal:
                self._resource_registry.remove_by_id(resource_id)
            logger.info(f"remove tag succeed",
                        event_type=LogEventType.RESOURCE_MGR_REMOVE_TAG,
                        tag=single_tag,
                        metadata={"removal_resource_ids": resource_to_removal})
            results.append(Ok(single_tag))
        return results[0] if results and isinstance(tag, Tag) else results

    def update_resource_tag(self,
                            resource_id: str,
                            tag: Tag | list[Tag]
                            ) -> Result[list[Tag], Exception]:
        """
        Replace all tags on a resource with new tag(s).

        Args:
            resource_id: Resource identifier.
            tag: New tag(s) to set on the resource.

        Returns:
            Result[list[Tag], Exception]: Result object containing the new tag list or an exception.
        """
        self._inner_validate_resource_id(resource_id)
        self._inner_validate_tag(tag)
        try:
            results = self._tag_mgr.update_resource_tags(resource_id, tags=tag,
                                                         tag_update_strategy=TagUpdateStrategy.REPLACE)
            return Ok(results)
        except Exception as e:
            return Error(e)

    def add_resource_tag(self,
                         resource_id: str,
                         tag: Tag | list[Tag]
                         ) -> Result[list[Tag], Exception]:
        """
        Add tag(s) to a resource.

        Args:
            resource_id: Resource identifier.
            tag: Tag(s) to add to the resource.

        Returns:
            Result[list[Tag], Exception]: Result object containing all tags now associated with the resource.
        """
        self._inner_validate_resource_id(resource_id)
        self._inner_validate_tag(tag)
        try:
            now_tags = self._tag_mgr.tag_resource(resource_id, tag)
            return Ok(now_tags)
        except Exception as e:
            return Error(e)

    def remove_resource_tag(self,
                            resource_id: str,
                            tag: Tag | list[Tag],
                            *,
                            skip_if_tag_not_exists: bool = False
                            ) -> Result[list[Tag], Exception]:
        """
        Remove specific tag(s) from a resource.

        Args:
            resource_id: Resource identifier.
            tag: Tag(s) to remove from the resource.
            skip_if_tag_not_exists: If True, ignore non-existent tags.

        Returns:
            Result[list[Tag], Exception]: Result object containing remaining tags on the resource.
        """
        self._inner_validate_resource_id(resource_id)
        self._inner_validate_tag(tag)
        try:
            remain_tags = self._tag_mgr.remove_resource_tags(resource_id, tags=tag,
                                                             skip_if_not_exists=skip_if_tag_not_exists)
            return Ok(remain_tags)
        except Exception as e:
            return Error(e)

    def get_resource_tag(self, resource_id: str) -> Optional[list[Tag]]:
        """
        Get all tags associated with a resource.

        Args:
            resource_id: Resource identifier.

        Returns:
            List of tags associated with the resource, or None if resource not found.
        """
        resource_tag = self._tag_mgr.get_resources_tags(resource_id)
        return resource_tag if resource_tag else None

    def resource_has_tag(self, resource_id: str, tag: Tag) -> bool:
        """
        Check if a specific resource is associated with the given tag.

        Args:
            resource_id: The unique identifier of the resource to check.
            tag: The tag to verify association with the resource.

        Returns:
            True if the resource has the specified tag, False otherwise.
        """
        self._inner_validate_tag(tag)
        self._inner_validate_resource_id(resource_id)
        return self._tag_mgr.has_resource_tag(resource_id, tag)

    async def release(self):
        """
        Release all resources and clean up.

        This method should be called when the ResourceMgr is no longer needed.
        """
        await self._resource_registry.tool().release()

    def _inner_add_resource(self, *, resource_id: str, resource_type: str, resource: Any,
                            resource_card: Optional[BaseCard] = None, tag: Optional[Tag | list[Tag]] = None):
        """
        Internal method to add a resource.

        Args:
            resource_id: Resource identifier.
            resource_type: Type of resource ("agent", "group", "workflow", "tool", "prompt", "model").
            resource: Resource instance or provider.
            resource_card: Optional card associated with the resource.
            tag: Optional tag(s) for the resource.

        Returns:
            Result object indicating success or failure.
        """
        try:
            if self._tag_mgr.has_resource(resource_id):
                raise build_error(StatusCode.RESOURCE_ADD_ERROR, card=resource_card if resource_card else resource_id,
                                  reason=f'resource already exist')
            # add resource
            if resource_type == "workflow":
                self._resource_registry.workflow().add_workflow(resource_id, resource)
            elif resource_type == "agent":
                self._resource_registry.agent().add_agent(resource_id, resource)
            elif resource_type == "group":
                self._resource_registry.agent_group().add_agent_group(resource_id, resource)
            elif resource_type == "tool":
                self._resource_registry.tool().add_tool(resource_id, resource)
            elif resource_type == "prompt":
                self._resource_registry.prompt().add_prompt(resource_id, resource)
            elif resource_type == "model":
                self._resource_registry.model().add_model(resource_id, resource)
            elif resource_type == "sys_operation":
                self._resource_registry.sys_operation().add_sys_operation(resource_id, resource)
            else:
                ...
            if resource_card:
                self._id_to_card[resource_id] = resource_card
            self._tag_mgr.tag_resource(resource_id, tag if tag else GLOBAL)
            logger.info(f"add resource succeed",
                        event_type=LogEventType.RESOURCE_MGR_ADD_RESOURCE,
                        resource_id=resource_id,
                        resource_type=resource_type,
                        tag=tag if tag else GLOBAL,
                        card=resource_card.str() if resource_card else None)
            return Ok(resource_card if resource_card else resource_id)
        except Exception as e:
            logger.error(f"add resource failed",
                         event_type=LogEventType.RESOURCE_MGR_ADD_RESOURCE,
                         resource_id=resource_id,
                         resource_type=resource_type,
                         exception=e,
                         tag=tag if tag else GLOBAL,
                         card=resource_card.str() if resource_card else None)

            return Error(e)

    def _inner_remove_resources(self, *, resource_id: Optional[str | list[str]], resource_type: str,
                                tag: Tag | list[Tag],
                                tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                skip_if_tag_not_exists: bool = False):
        """
        Internal method to remove resources.

        Args:
            resource_id: Resource identifier(s) to remove.
            resource_type: Type of resource.
            tag: Tag(s) to filter resources.
            tag_match_strategy: Strategy for matching tags.
            skip_if_tag_not_exists: Whether to skip non-existent tags.

        Returns:
            Result object(s) indicating success or failure.
        """
        ids_to_remove = []
        if resource_id is not None:
            self._inner_validate_resource_ids(resource_id, resource_type)
            ids_to_remove = resource_id if isinstance(resource_id, list) else [resource_id]
        remove_by_tag = False
        results: list[Result] = []
        if not ids_to_remove:
            self._inner_validate_tag(tag)
            ids_to_remove = self._tag_mgr.find_resources_by_tags(tag, tag_match_strategy=tag_match_strategy,
                                                                 skip_if_not_exists=skip_if_tag_not_exists)
            remove_by_tag = True
            if not ids_to_remove:
                return results
        for remove_id in ids_to_remove:
            error = None
            try:
                self._tag_mgr.remove_resource(remove_id)
                # add resource
                if resource_type == "workflow":
                    self._resource_registry.workflow().remove_workflow(remove_id)
                elif resource_type == "agent":
                    self._resource_registry.agent().remove_agent(remove_id)
                elif resource_type == "group":
                    self._resource_registry.agent_group().remove_agent_group(remove_id)
                elif resource_type == "model":
                    self._resource_registry.model().remove_model(remove_id)
                elif resource_type == "tool":
                    self._resource_registry.tool().remove_tool(remove_id)
                elif resource_type == "prompt":
                    self._resource_registry.prompt().remove_prompt(remove_id)
                elif resource_type == "sys_operation":
                    self._resource_registry.sys_operation().remove_sys_operation(remove_id)
                else:
                    ...
            except Exception as e:
                if not remove_by_tag:
                    error = e
            removed_card = self._id_to_card.pop(remove_id, None)
            if error:
                logger.error(f"remove resource failed",
                             event_type=LogEventType.RESOURCE_MGR_REMOVE_RESOURCE,
                             resource_id=remove_id,
                             resource_type=resource_type,
                             exception=error,
                             tag=tag if tag else GLOBAL,
                             card=removed_card.str() if removed_card else None)
                results.append(Error(error))
            elif resource_type in ["tool", "prompt"]:
                results.append(Ok(remove_id))
            else:
                if removed_card or not remove_by_tag:
                    results.append(Ok(removed_card))
            if not error:
                logger.error(f"remove resource failed",
                             event_type=LogEventType.RESOURCE_MGR_REMOVE_RESOURCE,
                             resource_id=remove_id,
                             resource_type=resource_type,
                             exception=error,
                             tag=tag if tag else GLOBAL,
                             card=removed_card.str() if removed_card else None)
        return results if not isinstance(resource_id, str) else results[0]

    def _inner_find_resource_ids(self, *, resource_id: Optional[str | list[str]], tag: Tag | list[Tag],
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL):
        """
        Internal method to find resource IDs by ID or tag.

        Args:
            resource_id: Resource identifier(s).
            tag: Tag(s) to search.
            tag_match_strategy: Strategy for matching tags.

        Returns:
            Tuple of (list of resource IDs, whether it was an exact match by ID).
        """
        ids_to_get = None
        exact_match = False
        if resource_id is not None:
            self._inner_validate_resource_ids(resource_id)
            ids_to_get = resource_id if isinstance(resource_id, list) else [resource_id]
            exact_match = True
        if not ids_to_get:
            ids_to_get = self._tag_mgr.find_resources_by_tags(tag if tag else GLOBAL,
                                                              tag_match_strategy=tag_match_strategy)
            exact_match = False
        return ids_to_get, exact_match

    def _inner_get_resources(self, *, resource_id: Optional[str | list[str]], resource_type: str, tag: Tag | list[Tag],
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL, session=None):
        """
        Internal method to get resources by ID or tag.

        Args:
            resource_id: Resource identifier(s).
            resource_type: Type of resource.
            tag: Tag(s) to filter.
            tag_match_strategy: Strategy for matching tags.
            session: Optional session context.

        Returns:
            Resource instance(s) or None.
        """
        ids_to_get, exact_match = self._inner_find_resource_ids(resource_id=resource_id, tag=tag,
                                                                tag_match_strategy=tag_match_strategy)
        results = []
        for get_id in ids_to_get:
            resource = None
            try:
                if self._tag_mgr.has_resource(get_id):
                    if resource_type == "tool":
                        resource = self._resource_registry.tool().get_tool(get_id, session=session)
                    elif resource_type == "prompt":
                        resource = self._resource_registry.prompt().get_prompt(get_id)
                    elif resource_type == "sys_operation":
                        resource = self._resource_registry.sys_operation().get_sys_operation(get_id)
                    else:
                        ...
            except Exception as e:
                ...
            finally:
                if resource or exact_match:
                    results.append(resource)

        return results[0] if results and isinstance(resource_id, str) else results

    async def _inner_get_resources_by_provider(self, *, resource_id: Optional[str | list[str]], resource_type: str,
                                               tag: Tag | list[Tag],
                                               tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                               session=None):
        """
        Internal method to get resources by provider.

        Args:
            resource_id: Resource identifier(s).
            resource_type: Type of resource.
            tag: Tag(s) to filter.
            tag_match_strategy: Strategy for matching tags.
            session: Optional session context.

        Returns:
            Resource instance(s) or None.
        """
        ids_to_get, exact_match = self._inner_find_resource_ids(resource_id=resource_id, tag=tag,
                                                                tag_match_strategy=tag_match_strategy)
        results: list = []
        if not ids_to_get:
            return results
        for get_id in ids_to_get:
            resource = None
            try:
                if self._tag_mgr.has_resource(get_id):
                    # add resource
                    if resource_type == "workflow":
                        resource = await self._resource_registry.workflow().get_workflow(get_id, session=session)
                    elif resource_type == "agent":
                        resource = await self._resource_registry.agent().get_agent(get_id)
                    elif resource_type == "group":
                        resource = await self._resource_registry.agent_group().get_agent_group(get_id)
                    elif resource_type == "model":
                        resource = await self._resource_registry.model().get_model(get_id, session=session)
                    else:
                        ...
            except Exception as e:
                ...
            finally:
                if resource or exact_match:
                    results.append(resource)

        return results[0] if results and isinstance(resource_id, str) else results

    @staticmethod
    def _inner_validate_tag(tag: Tag | list[Tag]):
        """
        Validate tag(s).

        Args:
            tag: Tag or list of tags to validate.

        Raises:
            Error if tag is invalid.
        """
        if not tag:
            raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag, reason="is None or empty value")
        tmp_tags = []
        if isinstance(tag, list):
            if GLOBAL in tag and len(tag) > 1:
                raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag,
                                  reason="The GLOBAL tag already exists and cannot be assigned additional tags.")
            for single_tag in tag:
                if not single_tag:
                    raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag, reason="has None or empty value")
                if single_tag in tmp_tags:
                    raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag,
                                      reason=f"has duplicate tag '{single_tag}' item")
                tmp_tags.append(single_tag)

    @staticmethod
    def _inner_validate_resource_card(card: BaseCard, resource_type: str, card_class_type: Type):
        """
        Validate resource card.

        Args:
            card: Resource card to validate.

        Raises:
            Error if card is invalid.
        """
        if not isinstance(card, card_class_type):
            expected_type = card_class_type.__name__
            raise build_error(
                StatusCode.RESOURCE_CARD_VALUE_INVALID,
                resource_type=resource_type,
                reason=(
                    f"cannot be None, must be an instance of {expected_type}"
                )
            )

    @staticmethod
    def _inner_validate_server_config(server_config):
        """
        Validate MCP server configuration.

        Args:
            server_config: Server configuration to validate.

        Raises:
            Error if configuration is invalid.
        """
        if not server_config:
            raise build_error(
                StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                server_config=server_config,
                reason="MCP server configuration cannot be empty or None"
            )

        if isinstance(server_config, McpServerConfig):
            if not server_config.server_id:
                raise build_error(
                    StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                    server_config=server_config,
                    reason="MCP server configuration is missing server_id"
                )
            if not isinstance(server_config.server_id, str):
                raise build_error(
                    StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                    server_config=server_config,
                    reason=(
                        f"invalid MCP server_id type: "
                        f"expected string, got {type(server_config.server_id).__name__}"
                    )
                )
            if not server_config.server_id.strip():
                raise build_error(
                    StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                    server_config=server_config,
                    reason="MCP server_id cannot be empty or whitespace only"
                )

        if isinstance(server_config, list):
            if not server_config:
                raise build_error(
                    StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                    server_config=server_config,
                    reason="MCP server configuration list is empty"
                )

            seen_ids = []
            for idx, config in enumerate(server_config):
                if not config:
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                        server_config=server_config,
                        reason=(
                            f"invalid MCP server configuration at idx {idx}: "
                            f"configuration cannot be None"
                        )
                    )

                if not isinstance(config, McpServerConfig):
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                        server_config=server_config,
                        reason=(
                            f"invalid MCP server configuration type at idx {idx}: "
                            f"expected McpServerConfig, got {type(config).__name__}"
                        )
                    )

                if not config.server_id:
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                        server_config=server_config,
                        reason=(
                            f"invalid MCP server configuration at idx {idx}: "
                            f"server_id is missing"
                        )
                    )

                if not isinstance(config.server_id, str):
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                        server_config=server_config,
                        reason=(
                            f"invalid MCP server_id type at idx {idx}: "
                            f"expected string, got {type(config.server_id).__name__}"
                        )
                    )

                if not config.server_id.strip():
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                        server_config=server_config,
                        reason=(
                            f"invalid MCP server_id at idx {idx}: "
                            f"server_id cannot be empty or whitespace only"
                        )
                    )

                if config.server_id in seen_ids:
                    duplicate_idx = seen_ids.index(config.server_id)
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                        server_config=server_config,
                        reason=(
                            f"duplicate MCP server_id found: "
                            f"'{config.server_id}' appears at idx {duplicate_idx} and {idx}"
                        )
                    )

                seen_ids.append(config.server_id)

        # 检查既不是McpServerConfig也不是list的情况
        elif not isinstance(server_config, McpServerConfig):
            raise build_error(
                StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID,
                server_config=server_config,
                reason=(
                    f"Invalid MCP server configuration type: "
                    f"expected McpServerConfig or list[McpServerConfig], "
                    f"got {type(server_config).__name__}"
                )
            )

    @staticmethod
    def _inner_validate_resource_id(resource_id: str, resource_type="resource"):
        if not resource_id:
            raise build_error(
                StatusCode.RESOURCE_ID_VALUE_INVALID,
                resource_type=resource_type,
                reason=f"cannot be empty or None"
            )

        if not isinstance(resource_id, str):
            raise build_error(
                StatusCode.RESOURCE_ID_VALUE_INVALID,
                resource_type=resource_type,
                reason=(
                    f"invalid id type: "
                    f"expected string, got {type(resource_id).__name__}"
                )
            )

        if not resource_id.strip():
            raise build_error(
                StatusCode.RESOURCE_ID_VALUE_INVALID,
                resource_type=resource_type,
                reason=f"string id cannot be empty or whitespace only"
            )

    @staticmethod
    def _inner_validate_resource_ids(resource_id: list[str] | str, resource_type="resource"):
        """
        Validate resource ID(s).

        Args:
            resource_id: Resource ID(s) to validate.

        Raises:
            Error if ID(s) are invalid.
        """
        if not resource_id:
            raise build_error(
                StatusCode.RESOURCE_ID_VALUE_INVALID,
                resource_id=resource_id,
                reason=f"{resource_type} id list cannot be empty or None"
            )
        if isinstance(resource_id, str):
            ResourceMgr._inner_validate_resource_id(resource_id, resource_type)

        if isinstance(resource_id, list):
            tmp_ids = []
            for idx, rid in enumerate(resource_id):
                try:
                    ResourceMgr._inner_validate_resource_id(rid, resource_type)
                except ValidationError as e:
                    raise build_error(
                        StatusCode.RESOURCE_ID_VALUE_INVALID,
                        resource_id=resource_id,
                        reason=(
                            f"invalid {resource_type} id at idx {idx}: {e.message}"
                        )
                    )

                if rid in tmp_ids:
                    raise build_error(
                        StatusCode.RESOURCE_ID_VALUE_INVALID,
                        resource_id=resource_id,
                        reason=(
                            f"duplicate {resource_type} id found: "
                            f"'{rid}' appears multiple times in the list"
                        )
                    )
                tmp_ids.append(rid)

    @staticmethod
    def _inner_validate_providers(
            providers: list,
            resource_type: str,
            card_class_type: Type = None
    ):
        if not providers:
            raise build_error(
                StatusCode.RESOURCE_PROVIDER_INVALID,
                resource_type=resource_type,
                reason=f" cannot be empty: expected a non-empty list of (card, callable) pairs"
            )

        for idx, item in enumerate(providers):
            if not isinstance(item, tuple) or len(item) != 2:
                expected_type = f"tuple[{card_class_type.__name__ if card_class_type else 'str'}, Callable]"
                raise build_error(
                    StatusCode.RESOURCE_PROVIDER_INVALID,
                    resource_type=resource_type,
                    reason=(
                        f"invalid provider format at idx {idx}: "
                        f"expected {expected_type}, got {type(item).__name__} "
                        f"(length={len(item) if isinstance(item, (tuple, list)) else 'N/A'})"
                    )
                )

            resource_item, provider = item

            if resource_item is None:
                expected_type = card_class_type.__name__ if card_class_type else "str"
                raise build_error(
                    StatusCode.RESOURCE_PROVIDER_INVALID,
                    resource_type=resource_type,
                    reason=(
                        f"invalid {'card' if card_class_type else 'id'} at idx {idx}: "
                        f"{'card' if card_class_type else 'id'} cannot be None, must be an instance of {expected_type}"
                    )
                )
            try:
                ResourceMgr._inner_validate_resource_id(
                    resource_item if isinstance(resource_item, str) else resource_item.id, resource_type)
            except ValidationError as e:
                raise build_error(StatusCode.RESOURCE_PROVIDER_INVALID,
                                  resource_type=resource_type,
                                  reason=(
                                      f"invalid {resource_type} id at idx {idx}: "
                                      f"{str(e)}"
                                  ))

            if provider is None:
                raise build_error(
                    StatusCode.RESOURCE_PROVIDER_INVALID,
                    resource_type=resource_type,
                    reason=(
                        f"invalid provider at idx {idx}: "
                        f"provider cannot be None, must be a callable function"
                    )
                )

            if card_class_type:
                if not isinstance(resource_item, card_class_type):
                    raise build_error(
                        StatusCode.RESOURCE_PROVIDER_INVALID,
                        resource_type=resource_type,
                        reason=(
                            f"invalid {resource_type} card type at idx {idx}: "
                            f"expected {card_class_type.__name__}, got {type(resource_item).__name__}"
                        )
                    )
            else:
                if not isinstance(resource_item, str):
                    raise build_error(
                        StatusCode.RESOURCE_PROVIDER_INVALID,
                        resource_type=resource_type,
                        reason=(
                            f"invalid {resource_type} card type at idx {idx}: "
                            f"expected str, got {type(resource_item).__name__}"
                        )
                    )

            if not (resource_type == "agent" and isinstance(provider, RemoteAgent)):
                if not isinstance(provider, Callable):
                    raise build_error(
                        StatusCode.RESOURCE_PROVIDER_INVALID,
                        resource_type=resource_type,
                        reason=(
                            f"invalid {resource_type} provider type at idx {idx}: "
                            f"expected callable, got {type(provider).__name__}"
                        )
                    )

    @staticmethod
    def _inner_validate_resource(instance: Any | list, resource_type, resource_class_type: Type):
        if instance is None:
            raise build_error(
                StatusCode.RESOURCE_VALUE_INVALID,
                resource_type=resource_type,
                reason=(
                    f"{resource_type} cannot be None: "
                    f"expected an instance or list of {resource_class_type.__name__}"
                )
            )

        if isinstance(instance, list):
            if not instance:
                raise build_error(
                    StatusCode.RESOURCE_VALUE_INVALID,
                    resource_type=resource_type,
                    reason=(
                        f"{resource_type} list cannot be empty: "
                        f"expected a non-empty list of {resource_class_type.__name__}"
                    )
                )

            for idx, item in enumerate(instance):
                if item is None:
                    raise build_error(
                        StatusCode.RESOURCE_VALUE_INVALID,
                        resource_type=resource_type,
                        reason=(
                            f"{resource_type} at index {idx} cannot be None: "
                            f"expected an instance of {resource_class_type.__name__}"
                        )
                    )

                if not isinstance(item, resource_class_type):
                    raise build_error(
                        StatusCode.RESOURCE_VALUE_INVALID,
                        resource_type=resource_type,
                        reason=(
                            f"invalid {resource_type} type at index {idx}: "
                            f"expected {resource_class_type.__name__}, "
                            f"got {type(item).__name__}"
                        )
                    )

                try:
                    if resource_type == "tool":
                        ResourceMgr._inner_validate_resource_card(item.card, resource_type, ToolCard)
                        ResourceMgr._inner_validate_resource_id(item.card.id, resource_type)
                except ValidationError as e:
                    raise build_error(
                        StatusCode.RESOURCE_VALUE_INVALID,
                        resource_type=resource_type,
                        reason=f"{resource_type} at index {idx} has invalid card: {e.message}"
                    )
        else:
            if not isinstance(instance, resource_class_type):
                raise build_error(
                    StatusCode.RESOURCE_VALUE_INVALID,
                    resource_type=resource_type,
                    reason=(
                        f"invalid {resource_type} type: "
                        f"expected {resource_class_type.__name__}, "
                        f"got {type(instance).__name__}"
                    )
                )

            try:
                if resource_type == "tool":
                    ResourceMgr._inner_validate_resource_card(instance.card, resource_type, ToolCard)
                    ResourceMgr._inner_validate_resource_id(instance.card.id, resource_type)
            except ValidationError as e:
                raise build_error(
                    StatusCode.RESOURCE_VALUE_INVALID,
                    resource_type=resource_type,
                    reason=f"{resource_type} has invalid card: {e.message}"
                )

    @staticmethod
    def _inner_validate_provider(provider, resource_type):
        """
        Validate resource provider.

        Args:
            card: Resource card (for error context).
            provider: Provider to validate.

        Raises:
            Error if provider is invalid.
        """
        if provider is None:
            raise build_error(
                StatusCode.RESOURCE_PROVIDER_INVALID,
                resource_type=resource_type,
                reason=(
                    f"provider cannot be None, must be a callable function"
                )
            )
        if not (resource_type == "agent" and isinstance(provider, RemoteAgent)):
            if not isinstance(provider, Callable):
                raise build_error(
                    StatusCode.RESOURCE_PROVIDER_INVALID,
                    resource_type=resource_type,
                    reason=(
                        f"invalid provider type, "
                        f"expected callable, got {type(provider).__name__}"
                    )
                )

    def _inner_get_server_ids(self, server_id, server_name, tag, tag_match_strategy, skip_if_tag_not_exists,
                              error_code):
        """
        Get MCP server IDs by various criteria.

        Args:
            server_id: Server ID(s).
            server_name: Server name(s).
            tag: Tag(s) for filtering.
            tag_match_strategy: Strategy for matching tags.
            skip_if_tag_not_exists: Whether to skip non-existent tags.
            error_code: Error code to use for exceptions.

        Returns:
            Tuple of (list of server IDs, whether it was an exact match).
        """
        server_ids_to_refresh = []
        exact_match = False
        if server_id is not None:
            if not server_id:
                raise build_error(error_code, server_config=server_id,
                                  reason="server_id is empty")
            server_ids_to_refresh.append(server_id)
            exact_match = True
        else:
            if server_name is None:
                server_ids_to_refresh.extend(
                    self._tag_mgr.find_resources_by_tags(tag if tag else GLOBAL, tag_match_strategy,
                                                         skip_if_tag_not_exists))
            else:
                if len(server_name) == 0:
                    raise build_error(error_code, server_id=server_id,
                                      reason="server_name is empty")
                server_names = server_name if isinstance(server_name, list) else [server_name]
                for s_name in server_names:
                    server_ids_to_refresh.extend(self._resource_registry.tool().get_mcp_server_ids(s_name))
        return server_ids_to_refresh, exact_match

    @staticmethod
    def _get_card_type(card):
        """
        Get the type of a card.

        Args:
            card: Card to check.

        Returns:
            Card type as string, or None if unknown.
        """
        if type(card).__name__(card) == "GroupCard":
            return "group"
        elif type(card).__name__(card) == "WorkflowCard":
            return "workflow"
        elif type(card).__name__(card) == "AgentCard":
            return "agent"
        elif type(card).__name__(card) == "RestfulCard":
            return "restfulapi"
        elif type(card).__name__(card) == "McpToolCard":
            return "mcp"
        elif type(card).__name__(card) == "ToolCard":
            return "function"
        else:
            return None
