# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass

EXTRACT_SUCCESS_TRAJ_SYSTEM_PROMPT = """You are an expert in generating reusable memories. \
You will be given a user query, the corresponding trajectory that represents how an agent \
successfully accomplished the task.

## Guidelines
You need to extract and summarize useful insights in the format of memory items \
based on the agent's successful trajectory.
The goal of summarized memory items is to be helpful and generalizable for future similar tasks.

## Important notes
- You must first think why the trajectory is successful, and then summarize the insights.
- You can extract at most 3 memory items from the trajectory.
- You must not repeat similar or overlapping items.
- Do not mention specific queries, or string contents, but rather focus on the generalizable insights.

## Output Format
Your output must strictly follow the Markdown format shown below:
```
# Memory Item i
## Title <the title of the memory item>
## Description <one sentence summary of the memory item>
## Content <1-3 sentences describing the insights learned to successfully accomplishing the task>
```
"""
EXTRACT_FAIL_TRAJ_SYSTEM_PROMPT = """You are an expert in generating reusable memories. \
You will be given a user query, the corresponding trajectory that represents how an agent \
attempted to resolve the task but failed.

## Guidelines
You need to extract and summarize useful insights in the format of memory items based on the agent's failed trajectory.
The goal of summarized memory items is to be helpful and generalizable for future similar tasks.

## Important notes
- You must first reflect and think why the trajectory failed, and then summarize what \
lessons you have learned or strategies to prevent the failure in the future.
- You can extract at most 3 memory items from the trajectory.
- You must not repeat similar or overlapping items.
- Do not mention specific websites, queries, or string contents, but rather focus on the generalizable insights.

## Output Format
Your output must strictly follow the Markdown format shown below:
```
# Memory Item i
## Title <the title of the memory item>
## Description <one sentence summary of the memory item>
## Content <1-3 sentences describing the insights learned to successfully accomplishing the task>
```
"""
EXTRACT_TRAJ_USER_PROMPT = """Query: {query}
Trajectory: {trajectory}
"""
LLM_JUDGE_SYSTEM_PROMPT = """You are an expert in evaluating the performance of an agent. \
The agent is designed to help a human solve problems.
Given the user's query, the agent's action history, and the agent's response to the user, \
your goal is to decide whether the agent's execution is successful or not.

*IMPORTANT*
Format your response into two lines as shown below:
Thoughts: <your thoughts and reasoning process>
Status: "success" or "failure"
"""
LLM_JUDGE_USER_PROMPT = """Query: {query}
Trajectory: {trajectory}
"""

PARALLEL_SCALING_SYSTEM_PROMPT = """
You are an expert in generating reusable memories. You will be given a user query and \
multiple trajectories showing how an agent attempted the task. Some trajectories may be \
successful, and others may have failed.

## Guidelines
Your goal is to compare and contrast these trajectories to identify the most useful and \
generalizable strategies as memory items.
Use self-contrast reasoning:
- Identify patterns and strategies that consistently led to success.
- Identify mistakes or inefficiencies from failed trajectories and formulate preventative strategies.
- Prefer strategies that generalize beyond specific pages or exact wording.

## Important notes
- Think first: Why did some trajectories succeed while others failed?
- You can extract at most 5 memory items from all trajectories combined.
- Do not repeat similar or overlapping items.
- Do not mention specific websites, queries, or string contents — focus on generalizable \
behaviors and reasoning patterns.
- Make sure each memory item captures actionable and transferable insights.

## Output Format
Your output must strictly follow the Markdown format shown below:
``` # Memory Item i
## Title <the title of the memory item>
## Description <one sentence summary of the memory item>
## Content <1-5 sentences describing the insights learned to successfully accomplishing the task> ```
"""

PARALLEL_SCALING_USER_PROMPT = """Query: {query}

Trajectories:
{trajectories}"""


@dataclass
class ReasoningBankPrompt:
    EXTRACT_SUCCESS_TRAJ_SYSTEM_PROMPT = EXTRACT_SUCCESS_TRAJ_SYSTEM_PROMPT
    EXTRACT_FAIL_TRAJ_SYSTEM_PROMPT = EXTRACT_FAIL_TRAJ_SYSTEM_PROMPT
    EXTRACT_TRAJ_USER_PROMPT = EXTRACT_TRAJ_USER_PROMPT
    LLM_JUDGE_SYSTEM_PROMPT = LLM_JUDGE_SYSTEM_PROMPT
    LLM_JUDGE_USER_PROMPT = LLM_JUDGE_USER_PROMPT
    PARALLEL_SCALING_SYSTEM_PROMPT = PARALLEL_SCALING_SYSTEM_PROMPT
    PARALLEL_SCALING_USER_PROMPT = PARALLEL_SCALING_USER_PROMPT

ReasoningBankPrompts = ReasoningBankPrompt()