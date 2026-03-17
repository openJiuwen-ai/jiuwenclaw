# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ReActAgent with integrated TaskMemoryService for memory-augmented reasoning.

This module provides a ReActAgent subclass that automatically retrieves
relevant memories before processing queries, enhancing the agent's responses
with learned knowledge.

Example usage:
    from ContextEvolvingReActAgent import (
        ContextEvolvingReActAgent,
        create_memory_agent_config,
        MemoryAgentConfigInput,
    )
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    # Create agent card
    agent_card = AgentCard(
        id="memory-agent",
        name="memory-agent",
        description="Agent with memory capabilities"
    )

    # Create and configure agent
    agent = ContextEvolvingReActAgent(
        card=agent_card,
        user_id="user123",
    )

    # Configure model
    config = create_memory_agent_config(
        MemoryAgentConfigInput(
            model_provider="OpenAI",
            api_key="...",
            api_base="https://api.openai.com/v1",
            model_name="gpt-4",
        )
    )
    agent.configure(config)

    result = await agent.invoke({"query": "How do I implement caching?"})
"""

import os
import json
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Union
from openjiuwen.core.common.logging import context_engine_logger as logger

from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig, AgentCard

from .service import TaskMemoryService
from .core.file_connector import JSONFileConnector, safe_model_dump
from .core import config as memory_config


@dataclass
class SummarizeTrajectoriesInput:
    """Input parameters for summarize_trajectories method."""
    query: str
    trajectory: Union[str, List[str]]
    matts_mode: str
    feedback: Any = None
    scores: Optional[List[int]] = None


@dataclass
class MemoryAgentConfigInput:
    """Input parameters for create_memory_agent_config function."""
    model_provider: str
    api_key: str
    api_base: str
    model_name: str
    system_prompt: Optional[str] = None
    max_iterations: int = 5


class ContextEvolvingReActAgent(ReActAgent):
    """ReActAgent with integrated memory retrieval capabilities.

    This agent automatically retrieves relevant memories before invoking
    the base ReActAgent, augmenting the input with contextual knowledge.

    Attributes:
        memory_service: TaskMemoryService instance for memory operations
        user_id: User identifier for memory retrieval
        inject_memories_in_context: Whether to inject memories into system prompt
    """

    def __init__(
        self,
        card: AgentCard,
        user_id: str,
        memory_service: Optional[TaskMemoryService] = None,
        inject_memories_in_context: bool = True,
        memory_dir: str = "memory_files",
    ):
        """Initialize ContextEvolvingReActAgent.

        Args:
            card: Agent card (required)
            user_id: User identifier for memory retrieval
            memory_service: Optional pre-configured TaskMemoryService.
                           If not provided, a new instance will be created.
            inject_memories_in_context: If True, inject retrieved memories
                                       into the system context. If False,
                                       include them in the query augmentation.
            memory_dir: Directory for memory persistence files (default: "memory_files")
        """
        super().__init__(card)

        self.user_id = user_id
        self.memory_service = memory_service or TaskMemoryService()
        self.inject_memories_in_context = inject_memories_in_context

        # Initialize file connector for memory persistence
        self.file_connector = JSONFileConnector(indent=2)
        self.memory_dir = memory_dir

        # Ensure directories exist
        os.makedirs(self.memory_dir, exist_ok=True)

        # Attempt to load existing memories
        self._load_existing_memories()

        # Simple cache for memory retrieval to support efficient parallel/repeated calls
        self._last_retrieved_query: Optional[str] = None
        self._last_retrieval_result: Optional[Dict[str, Any]] = None

        logger.info(
            "ContextEvolvingReActAgent initialized for user=%s, inject_in_context=%s",
            user_id, inject_memories_in_context
        )

    def _load_existing_memories(self):
        """Load existing memories from file if available."""
        from .core.schema import VectorNode
        
        try:
            summary_algo = memory_config.get("SUMMARY_ALGO", "RB")
            filename = f"memory_{summary_algo}_{self.user_id}.json"
            file_path = os.path.join(self.memory_dir, filename)

            if os.path.exists(file_path):
                logger.info("Found existing memory file: %s", file_path)
                data = self.file_connector.load_from_file(file_path)
                
                # Load into vector store if supported
                if hasattr(self.memory_service, 'vector_store'):
                    count = 0
                    for node_id, node_data in data.items():
                        try:
                            node = VectorNode.from_dict(node_data)
                            # Use public load_node method for deserialization
                            if hasattr(self.memory_service.vector_store, 'load_node'):
                                self.memory_service.vector_store.load_node(node_id, node)
                                count += 1
                        except Exception as nfe:
                            logger.warning("Failed to load node %s: %s", node_id, nfe)
                            
                    logger.info("Loaded %s memories into vector store from %s", count, filename)
                else:
                    logger.warning("Memory service does not expose vector_store, cannot load memories.")
                    
        except Exception as e:
            logger.error("Failed to load existing memories: %s", e)

    async def invoke(self, inputs: Any, session=None) -> Dict[str, Any]:
        """Invoke the agent with memory-augmented input.

        This method:
        1. Extracts the query from inputs
        2. Retrieves relevant memories using TaskMemoryService
        3. Augments the input with retrieved memories
        4. Calls the parent ReActAgent.invoke()

        Args:
            inputs: Input dictionary containing at minimum:
                - query: The user's query string
                - conversation_id: Optional conversation identifier
            session: Optional session object

        Returns:
            Dictionary containing the agent's response with:
                - output: The generated response
                - memories_used: Number of memories retrieved
                - Additional fields from parent invoke
        """
        # Normalize inputs
        if isinstance(inputs, dict):
            query = inputs.get("query", "")
        elif isinstance(inputs, str):
            query = inputs
            inputs = {"query": query}
        else:
            query = ""

        if not query:
            logger.warning("No query provided in inputs")
            return await super().invoke(inputs, session)

        # Determine retrieval query
        retrieval_query = inputs.get("retrieval_query", query) if isinstance(inputs, dict) else query

        # Retrieve relevant memories
        logger.debug("Retrieving memories for query: %s", retrieval_query)

        try:
            # Check cache first
            if self._last_retrieved_query == retrieval_query and self._last_retrieval_result:
                memory_result = self._last_retrieval_result
                logger.info("Reusing cached memory retrieval result: %s", memory_result.get('memory_string', ''))
            else:
                memory_result = await self.memory_service.retrieve(
                    user_id=self.user_id,
                    query=retrieval_query,
                )
                # Update cache
                self._last_retrieved_query = retrieval_query
                self._last_retrieval_result = memory_result
                logger.info("Retrieved %s memories for query", len(memory_result.get('retrieved_memory', [])))

            # New response format: memory_string and retrieved_memory
            memory_string = memory_result.get("memory_string", "")
            retrieved_memory = memory_result.get("retrieved_memory", [])
            memories_used = len(retrieved_memory)

        except Exception as e:
            logger.error("Failed to retrieve memories: %s", e)
            memory_string = ""
            memories_used = 0

        # Build augmented input
        augmented_input = inputs.copy() if isinstance(inputs, dict) else {"query": inputs}

        if memories_used > 0 and memory_string:
            if self.inject_memories_in_context:
                # Add memories as context to the query
                memory_context = (
                    f"Some Related Experience to help you complete the task:\n"
                    f"{memory_string}\n"
                )
                augmented_input["query"] = f"Task:\n{query}\n\n{memory_context}"
            else:
                # Store memories separately for the agent to use
                augmented_input["memory_context"] = memory_string
                augmented_input["memories_used"] = memories_used

        # Call parent invoke
        result = await super().invoke(augmented_input, session)

        # Add memory metadata to result
        if isinstance(result, dict):
            result["memories_used"] = memories_used

        return result


    def format_trajectory(self, messages: list) -> str:
        """Format a list of messages into a clean trajectory string.
        
        Removes injected prompts and formats user/assistant/tool messages.
        """
        from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, ToolMessage
        
        transcript = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                content = msg.content
                # Remove injected sequential prompt
                if "Let's carefully re-examine the previous trajectory" in content:
                    content = content.split("Let's carefully re-examine the previous trajectory")[0]

                # Remove injected memory context
                if "Some Related Experience to help you complete the task" in content:
                    content = content.split("Some Related Experience to help you complete the task")[0]
                
                # Remove Task: prefix if present
                if content.startswith("Task:\n"):
                    content = content.replace("Task:\n", "", 1)
                    
                transcript.append(f"USER: {content.strip()}")
            elif isinstance(msg, AssistantMessage):
                if msg.content:
                    transcript.append(f"THOUGHT: {msg.content}")
                if msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        transcript.append(f"ACTION: {tool_call.name}({tool_call.arguments})")
            elif isinstance(msg, ToolMessage):
                transcript.append(f"OBSERVATION: {msg.content}")
        return "\n".join(transcript)


    def add_tool(self, tool):
        """Add a tool to this agent.

        Args:
            tool: A LocalFunction tool to add
        """
        from openjiuwen.core.runner import Runner
        self.ability_manager.add(tool.card)
        Runner.resource_mgr.add_tool(tool)

    def add_tools(self, tools):
        """Add multiple tools to this agent.

        Args:
            tools: List of LocalFunction tools to add
        """
        for tool in tools:
            self.add_tool(tool)

    async def summarize_trajectories(
        self,
        params: SummarizeTrajectoriesInput
    ) -> Optional[Dict[str, Any]]:
        """
        Summarize trajectory based on feedback and save to JSON.
        Supports single or multiple trajectories for MATTS.

        Args:
            params: SummarizeTrajectoriesInput dataclass containing:
                - query: The original query
                - trajectory: Trajectory string or list of trajectories
                - matts_mode: MATTS mode ("none", "parallel", "sequential")
                - feedback: Optional feedback on the response (string, bool, or list)
                - scores: Optional list of scores for each trajectory

        Returns:
            Summary result dictionary or None if failed
        """
        # Handle trajectory(ies)
        if hasattr(params.trajectory, '__iter__') and not isinstance(params.trajectory, str):
            trajectories = list(params.trajectory)
        else:
            trajectories = [params.trajectory]

        # Handle feedback/labels
        feedbacks = []
        if params.feedback is not None:
            if hasattr(params.feedback, '__iter__') and not isinstance(params.feedback, str):
                feedbacks = list(params.feedback)
            else:
                feedbacks = [params.feedback]

        # Extract scores from input
        scores = params.scores

        # For sequential mode, we only use the LAST trajectory/feedback for summarization
        if params.matts_mode == "sequential":
            trajectories = [trajectories[-1]] if trajectories else []
            if feedbacks:
                feedbacks = [feedbacks[-1]]
            if scores and len(scores) > 0:
                scores = [scores[-1]]

        # Convert feedback strings/booleans to boolean labels
        labels = []
        if feedbacks:
            for f in feedbacks:
                if isinstance(f, bool):
                    labels.append(f)
                else:
                    f_lower = str(f).lower()
                    success_keywords = ("success", "helpful", "positive", "good")
                    is_success = f_lower in success_keywords or "success" in f_lower
                    labels.append(is_success)
        
        # Ensure scores has the same length as trajectories if not provided
        if (scores is None or len(scores) == 0) and labels:
            scores = [1 if label else 0 for label in labels]
        elif scores is None:
            scores = []

        try:
            summary_result = await self.memory_service.summarize(
                user_id=self.user_id,
                matts=params.matts_mode,
                query=params.query,
                trajectories=trajectories,
                label=labels,
                score=scores
            )

            memories = summary_result.get('memory', [])

            # Format memory string based on keys
            formatted_memories = []
            if memories:
                # Check first item to determine format
                first_mem = memories[0]
                # ReasoningBank format (Nested)
                if isinstance(first_mem, dict) and 'memory' in first_mem and isinstance(first_mem['memory'], list):
                    for item in memories:
                        inner_memories = item.get('memory', [])
                        for mem in inner_memories:
                            formatted_memories.append(
                                f"Title: {mem.get('title', '')}\n"
                                f"Description: {mem.get('description', '')}\n"
                                f"Content: {mem.get('content', '')}"
                            )
                # ReasoningBank format (Flat)
                elif isinstance(first_mem, dict) and 'title' in first_mem and 'description' in first_mem:
                    for mem in memories:
                        formatted_memories.append(
                            f"Title: {mem.get('title', '')}\n"
                            f"Description: {mem.get('description', '')}\n"
                            f"Content: {mem.get('content', '')}"
                        )
                # ACE format
                elif isinstance(first_mem, dict) and 'section' in first_mem:
                    for mem in memories:
                        formatted_memories.append(
                            f"Section: {mem.get('section', '')}\n"
                            f"Content: {mem.get('content', '')}"
                        )
                # ReMe format
                elif isinstance(first_mem, dict) and 'when_to_use' in first_mem:
                    for mem in memories:
                        formatted_memories.append(
                            f"When to use: {mem.get('when_to_use', '')}\n"
                            f"Content: {mem.get('content', '')}"
                        )
                # Fallback
                else:
                    formatted_memories.append(json.dumps(memories, default=str))

            memory_log_str = "\n\n".join(formatted_memories) if formatted_memories else "No new memories."
            logger.info("New memory:\n%s", memory_log_str)

            try:
                # Save all memories to a single file from vector store
                summary_algo = memory_config.get("SUMMARY_ALGO", "RB")
                
                filename = f"memory_{summary_algo}_{self.user_id}.json"
                file_path = os.path.join(self.memory_dir, filename)

                # Access vector store from memory service
                if hasattr(self.memory_service, 'vector_store'):
                    # Get all nodes
                    all_nodes = self.memory_service.vector_store.get_all()

                    # Convert to dict format (handling serialization)
                    all_memories_data = {}
                    for node in all_nodes:
                        # Use node.id as key
                        try:
                            # Ensure node data is serializable
                            node_data = node.to_dict() if hasattr(node, 'to_dict') else safe_model_dump(node)
                            all_memories_data[node.id] = node_data
                        except Exception as e:
                            logger.warning("Skipping node %s serialization: %s", node.id, e)

                    # Save to single file
                    self.file_connector.save_to_file(file_path, all_memories_data)
                    logger.info("Persisted %s total memories to %s", len(all_memories_data), file_path)
                else:
                    logger.warning("Memory service does not expose vector_store, cannot save all memories.")

            except Exception as e:
                logger.error("Failed to save full memory store: %s", e)

            return summary_result

        except Exception as e:
            logger.error("Failed to learn from feedback: %s", e)
            return None


def create_memory_agent_config(params: MemoryAgentConfigInput) -> ReActAgentConfig:
    """Create a configuration for ContextEvolvingReActAgent.

    Args:
        params: MemoryAgentConfigInput dataclass containing:
            - model_provider: Model provider name (e.g., "OpenAI")
            - api_key: API key for the model
            - api_base: API base URL
            - model_name: Model name (e.g., "gpt-4")
            - system_prompt: Optional custom system prompt. If not provided,
                            uses a default memory-aware prompt.
            - max_iterations: Maximum ReAct loop iterations (default: 5)

    Returns:
        ReActAgentConfig configured for memory-augmented agent
    """
    default_system_prompt = (
        "You are a helpful assistant with access to a memory system. "
        "When relevant memories are provided in your context, use them to inform "
        "your responses. Always provide accurate, helpful answers based on both "
        "your knowledge and any retrieved memories."
    )

    prompt_template = [{
        "role": "system",
        "content": params.system_prompt or default_system_prompt
    }]

    config = ReActAgentConfig()
    config.configure_model_client(
        provider=params.model_provider,
        api_key=params.api_key,
        api_base=params.api_base,
        model_name=params.model_name,
    )
    config.configure_prompt_template(prompt_template)
    config.configure_max_iterations(params.max_iterations)

    return config
