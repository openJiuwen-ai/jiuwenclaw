# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""MaTTS (Memory-aware Test-Time Scaling) operations for ReasoningBank.

This module implements test-time scaling strategies that work synergistically
with ReasoningBank memory to generate diverse experiences and extract
higher-quality reasoning strategies.
"""

from typing import List, Dict, Any, Optional
from openjiuwen.core.common.logging import context_engine_logger as logger

from ....core.op import BaseOp
from ....core.context import RuntimeContext



class ParallelScalingOp(BaseOp):
    """Parallel scaling: Generate multiple trajectories and select best via Best-of-N.

    This operation generates k diverse trajectories for the same query,
    allowing the agent to explore different solution paths. The best
    trajectory is selected using an LLM-based evaluator.

    Args:
        k: Number of parallel trajectories to generate (scaling factor)
        temperature: Sampling temperature for diversity (default: 0.9)
    """

    def __init__(self, k: int = 3, temperature: float = 0.9):
        super().__init__()
        self.k = k
        self.temperature = temperature

    async def async_execute(self, context: RuntimeContext) -> RuntimeContext:
        """Execute parallel scaling by generating k trajectories.

        Args:
            context: Runtime context containing query and agent configuration

        Returns:
            Updated context with multiple trajectories and best selection
        """
        logger.info("Executing parallel scaling with k=%s", self.k)

        query = context.query
        user_id = context.user_id

        # Get LLM from service context
        llm = self.llm

        # Store original temperature
        original_temp = getattr(llm, 'temperature', 0.7)

        # Generate k diverse trajectories
        trajectories = []
        try:
            # Set higher temperature for diversity
            if hasattr(llm, 'temperature'):
                llm.temperature = self.temperature

            for i in range(self.k):
                logger.info("Generating trajectory %s/%s", i + 1, self.k)

                # Create a copy of context for this trajectory
                traj_context = RuntimeContext()
                traj_context.query = query
                traj_context.user_id = user_id

                # Copy retrieved memories if available
                if hasattr(context, 'retrieved_memories'):
                    traj_context.retrieved_memories = context.retrieved_memories

                # Execute the agent flow (should be set in context)
                if hasattr(context, 'agent_flow') and context.agent_flow:
                    await context.agent_flow(traj_context)
                    trajectories.append({
                        'index': i,
                        'context': traj_context,
                        'answer': traj_context.get('answer', ''),
                        'steps': traj_context.get('steps', []),
                        'success': traj_context.get('success', False)
                    })
                else:
                    logger.warning("No agent_flow found in context, skipping trajectory generation")

        finally:
            # Restore original temperature
            if hasattr(llm, 'temperature'):
                llm.temperature = original_temp

        # Store all trajectories
        context.parallel_trajectories = trajectories
        context.scaling_factor = self.k

        logger.info("Generated %s trajectories", len(trajectories))

        # Select best trajectory will be done by BestOfNOp
        return context


class SequentialScalingOp(BaseOp):
    """Sequential scaling: Iteratively refine a single trajectory with self-checking.

    This operation performs k rounds of self-refinement on a trajectory,
    where the agent re-examines and corrects its reasoning at each step.

    Args:
        k: Number of refinement rounds (scaling factor)
    """

    def __init__(self, k: int = 3):
        super().__init__()
        self.k = k

    async def async_execute(self, context: RuntimeContext) -> RuntimeContext:
        """Execute sequential scaling through iterative refinement.

        Args:
            context: Runtime context containing initial trajectory

        Returns:
            Updated context with refined trajectory
        """
        logger.info("Executing sequential scaling with k=%s refinement rounds", self.k)

        query = context.query
        user_id = context.user_id

        # Get LLM from service context
        llm = self.llm

        refinement_history = []
        current_answer = context.get('answer', '')
        current_trajectory = context.get('trajectory', '')

        for round_idx in range(self.k):
            logger.info("Refinement round %s/%s", round_idx + 1, self.k)

            # Build refinement prompt
            if round_idx == 0:
                # First-time check
                refinement_prompt = f"""Important: Let's carefully re-examine the previous trajectory,
including your reasoning steps and actions taken.

Pay special attention to whether you used the correct approach, and whether your response addresses
the user query. If you find inconsistencies, correct them. If everything seems correct, confirm your final answer.

Previous answer: {current_answer}

Query: {query}"""
            else:
                # Follow-up check
                refinement_prompt = f"""Let's check again.

Previous answer: {current_answer}

Query: {query}"""

            # Get refined response
            refined_response = await llm.async_generate(refinement_prompt)

            refinement_history.append({
                'round': round_idx + 1,
                'prompt': refinement_prompt,
                'response': refined_response
            })

            # Update current answer
            current_answer = refined_response

        # Store refinement history and final answer
        context.refinement_history = refinement_history
        context.refined_answer = current_answer
        context.answer = current_answer
        context.scaling_factor = self.k

        logger.info("Completed %s refinement rounds", self.k)

        return context


class BestOfNOp(BaseOp):
    """Select the best trajectory from N candidates using LLM-based evaluation.

    This operation evaluates multiple trajectories and selects the one that
    most effectively solves the task based on progress, efficiency, error
    severity, and overall quality.
    """

    async def async_execute(self, context: RuntimeContext) -> RuntimeContext:
        """Select best trajectory from parallel generations.

        Args:
            context: Runtime context containing multiple trajectories

        Returns:
            Updated context with best trajectory selected
        """
        if not hasattr(context, 'parallel_trajectories') or not context.parallel_trajectories:
            logger.warning("No parallel trajectories found, skipping Best-of-N")
            return context

        logger.info("Selecting best trajectory from %s candidates", len(context.parallel_trajectories))

        trajectories = context.parallel_trajectories
        query = context.query

        # Get LLM from service context
        llm = self.llm

        # Build evaluation prompt
        traj_descriptions = []
        for traj in trajectories:
            traj_desc = f"""Trajectory {traj['index'] + 1}:
Answer: {traj['answer']}
Success: {traj.get('success', 'Unknown')}
Steps: {len(traj.get('steps', []))}
"""
            traj_descriptions.append(traj_desc)

        num_trajectories = len(trajectories)
        eval_prompt = f"""You are an expert in evaluating agent trajectories. \
You will be given the user query and {num_trajectories} candidate trajectories.
Your job is to select the single best trajectory that most effectively \
and efficiently solves the task.

Query: {query}

{chr(10).join(traj_descriptions)}

## Evaluation Criteria:
1. Progress Toward Goal: How well the trajectory advances toward completing the task
2. Trajectory Efficiency: How efficiently progress is achieved given number of steps
3. Error Severity: Assess fatal, significant, or minor errors
4. Overall Quality: Logical flow, coherence, and closeness to goal

Return ONLY the index (0-{len(trajectories)-1}) of the best trajectory."""

        # Get LLM evaluation
        try:
            response = await llm.async_generate(eval_prompt, temperature=0.0)

            # Extract index from response
            best_idx = 0
            for i in range(len(trajectories)):
                if str(i) in response:
                    best_idx = i
                    break

            logger.info("Selected trajectory %s as best", best_idx)

            # Set the best trajectory as the result
            best_traj = trajectories[best_idx]
            context.answer = best_traj['answer']
            context.best_trajectory_index = best_idx
            context.best_trajectory = best_traj

            # Calculate Pass@k - count how many trajectories succeeded
            success_count = sum(1 for t in trajectories if t.get('success', False))
            context.pass_at_k = success_count / len(trajectories) if trajectories else 0.0

        except Exception as e:
            logger.error("Error in Best-of-N selection: %s", e)
            # Fallback: use first trajectory
            context.answer = trajectories[0]['answer']
            context.best_trajectory_index = 0
            context.best_trajectory = trajectories[0]

        return context


class SelfContrastMemoryOp(BaseOp):
    """Extract memory from multiple trajectories using self-contrast reasoning.

    This operation compares successful and failed trajectories to identify
    patterns that lead to success and mistakes that cause failure, enabling
    more reliable memory curation from contrastive signals.
    """

    async def async_execute(self, context: RuntimeContext) -> RuntimeContext:
        """Extract contrastive memories from parallel trajectories.

        Args:
            context: Runtime context containing multiple trajectories

        Returns:
            Updated context with extracted memories
        """
        if not hasattr(context, 'parallel_trajectories') or not context.parallel_trajectories:
            logger.warning("No parallel trajectories for contrastive extraction")
            return context

        logger.info("Extracting memories using self-contrast")

        trajectories = context.parallel_trajectories
        query = context.query

        # Get LLM from service context
        llm = self.llm

        # Separate successful and failed trajectories
        successful = [t for t in trajectories if t.get('success', False)]
        failed = [t for t in trajectories if not t.get('success', False)]

        logger.info("Found %s successful and %s failed trajectories", len(successful), len(failed))

        # Build contrastive extraction prompt
        extraction_prompt = f"""You are an expert in extracting reasoning strategies. \
You will be given a user query and multiple trajectories showing \
how an agent attempted the task. Some trajectories may be successful, \
and others may have failed.

## Guidelines
Your goal is to compare and contrast these trajectories to identify the most useful and generalizable strategies as memory items.

Use self-contrast reasoning:
- Identify patterns and strategies that consistently led to success
- Identify mistakes or inefficiencies from failed trajectories and formulate preventative strategies
- Prefer strategies that generalize beyond specific pages or exact wording

## Important notes
- Think first: Why did some trajectories succeed while others failed?
- You can extract at most 5 memory items from all trajectories combined
- Do not repeat similar or overlapping items
- Do not mention specific websites, queries, or string contents — focus on generalizable behaviors and reasoning patterns
- Make sure each memory item captures actionable and transferable insights

## Output Format
Your output must strictly follow the Markdown format shown below:
```
# Memory Item 1
## Title <the title of the memory item>
## Description <one sentence summary of the memory item>
## Content <1-5 sentences describing the insights learned to successfully accomplishing the task>

# Memory Item 2
...
```

Query: {query}

Successful Trajectories ({len(successful)}):
{chr(10).join([f"Trajectory {t['index']}: {t['answer'][:200]}..." for t in successful])}

Failed Trajectories ({len(failed)}):
{chr(10).join([f"Trajectory {t['index']}: {t['answer'][:200]}..." for t in failed])}
"""

        try:
            # Extract memories using contrastive analysis
            response = await llm.async_generate(extraction_prompt, temperature=1.0)

            # Parse memory items from response (simplified parsing)
            from schema.memory import ReasoningBankMemory

            memories = []
            # Simple parsing - look for ## Title, ## Description, ## Content patterns
            lines = response.split('\n')
            current_memory = {}

            for line in lines:
                line = line.strip()
                if line.startswith('## Title'):
                    if current_memory and all(k in current_memory for k in ['title', 'description', 'content']):
                        memories.append(current_memory)
                    current_memory = {'title': line.replace('## Title', '').strip()}
                elif line.startswith('## Description'):
                    current_memory['description'] = line.replace('## Description', '').strip()
                elif line.startswith('## Content'):
                    current_memory['content'] = line.replace('## Content', '').strip()
                elif line and 'title' in current_memory and 'content' in current_memory:
                    # Continue content if we're in a content block
                    if 'content' in current_memory:
                        current_memory['content'] += ' ' + line

            # Add last memory
            if current_memory and all(k in current_memory for k in ['title', 'description', 'content']):
                memories.append(current_memory)

            # Create ReasoningBankMemory objects
            memory_objects = []
            for mem in memories:
                memory_objects.append(ReasoningBankMemory(
                    workspace_id=context.user_id,
                    title=mem['title'],
                    description=mem['description'],
                    content=mem['content'],
                    source_type='comparative'  # From contrastive analysis
                ))

            context.contrastive_memories = memory_objects
            logger.info("Extracted %s contrastive memories", len(memory_objects))

        except Exception as e:
            logger.error("Error in contrastive memory extraction: %s", e)
            context.contrastive_memories = []

        return context
