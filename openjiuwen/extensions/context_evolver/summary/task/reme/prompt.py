# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass

COMPARATIVE_MEMORY_PROMPT = """You are an expert AI analyst comparing higher-scoring and \
lower-scoring step sequences to extract performance insights.

Your task is to identify the key differences between higher and lower performing approaches at the step level.
Focus on what made the higher-scoring approach more effective, even when both approaches may have had partial success.

SOFT COMPARATIVE ANALYSIS FRAMEWORK:
● PERFORMANCE FACTORS: Identify what specifically contributed to the higher score
● APPROACH DIFFERENCES: Compare methodologies and execution strategies
● EFFICIENCY ANALYSIS: Analyze why one approach was more efficient or effective
● OPTIMIZATION INSIGHTS: Extract lessons for improving performance

EXTRACTION PRINCIPLES:
● Focus on INCREMENTAL IMPROVEMENTS and performance optimization
● Extract QUALITY INDICATORS that differentiate better vs good approaches
● Identify REFINEMENT STRATEGIES that lead to higher scores
● Frame insights as PERFORMANCE ENHANCEMENT guidelines

# Higher-Scoring Step Sequence (Score: {higher_score})
{higher_steps}

# Lower-Scoring Step Sequence (Score: {lower_score})
{lower_steps}


OUTPUT FORMAT:
Generate up to 5 performance improvement insights as JSON objects:
```json
[
{{
    "when_to_use": "Specific scenarios where this performance insight applies",
    "experience": "Detailed analysis of what made the higher-scoring approach more effective",
    "tags": ["performance_optimization", "score_improvement", "relevant_keywords"],
    "confidence": 0.7,
    "step_type": "reasoning|action|observation|decision",
    "tools_used": ["list", "of", "tools"]
}}
]
```"""

SUCCESS_MEMORY_PROMPT = """You are an expert AI analyst reviewing successful step sequences \
from an AI agent execution.

Your task is to extract reusable, actionable step-level task memories that can guide future agent executions.
Focus on identifying specific patterns, techniques, and decision points that contributed to success.

ANALYSIS FRAMEWORK:
● STEP PATTERN ANALYSIS: Identify the specific sequence of actions that led to success
● DECISION POINTS: Highlight critical decisions made during these steps
● TECHNIQUE EFFECTIVENESS: Analyze why specific approaches worked well
● REUSABILITY: Extract patterns that can be applied to similar scenarios

EXTRACTION PRINCIPLES:
● Focus on TRANSFERABLE TECHNIQUES and decision frameworks
● Frame insights as actionable guidelines and best practices

# Original Query
{query}

# Step Sequence Analysis
{step_sequence}

# Outcome
This step sequence was part of a {outcome} trajectory.

OUTPUT FORMAT:
Generate 1-3 step-level success insights as JSON objects:
```json
[
{{
    "when_to_use": "Specific conditions when this step pattern should be applied",
    "experience": "Detailed description of the successful step pattern and why it works",
    "tags": ["relevant", "keywords", "for", "categorization"],
    "confidence": 0.8,
    "step_type": "reasoning|action|observation|decision",
    "tools_used": ["list", "of", "tools"]
}}
]
```"""

FAILURE_MEMORY_PROMPT = """You are an expert AI analyst reviewing failed step sequences \
from an AI agent execution.

Your task is to extract learning task memories from failures to prevent similar mistakes in future executions.
Focus on identifying error patterns, missed opportunities, and alternative approaches.

ANALYSIS FRAMEWORK:
● FAILURE POINT IDENTIFICATION: Pinpoint where and why the steps went wrong
● ERROR PATTERN ANALYSIS: Identify recurring mistakes or problematic approaches
● ALTERNATIVE APPROACHES: Suggest what could have been done differently
● PREVENTION STRATEGIES: Extract actionable insights to avoid similar failures

EXTRACTION PRINCIPLES:
● Extract GENERAL PRINCIPLES as well as SPECIFIC INSTRUCTIONS
● Focus on PATTERNS and RULES as well as particular instances

# Original Query
{query}

# Step Sequence Analysis
{step_sequence}

# Outcome
This step sequence was part of a {outcome} trajectory.

OUTPUT FORMAT:
Generate 1-3 step-level failure prevention insights as JSON objects:
```json
[
{{
    "when_to_use": "Specific situations where this lesson should be remembered",
    "experience": "Universal principle or rule extracted from the failure pattern ",
    "tags": ["error_prevention", "failure_analysis", "relevant_keywords"],
    "confidence": 0.7,
    "step_type": "reasoning|action|observation|decision",
    "tools_used": ["list", "of", "tools"]
}}
]
```"""

COMPARATIVE_ALL_MEMORY_PROMPT = """You are an expert AI analyst comparing multiple step \
sequences which might be successful or failed to extract differential insights.

Your task is to compare and contrast these trajectories to identify the most useful and \
generalizable strategies as memory items using self-contrast reasoning.
Focus on critical decision points, technique variations, and approach differences.

COMPARATIVE ANALYSIS FRAMEWORK:
● DECISION CONTRAST: Compare critical decisions made in success vs failure cases
● TECHNIQUE VARIATIONS: Identify different approaches and their outcomes
● TIMING DIFFERENCES: Analyze when certain actions were taken and their impact
● SUCCESS FACTORS: Extract what specifically made the difference

EXTRACTION PRINCIPLES:
● Frame comparisons as PRINCIPLES as well as case-specific SOLUTIONS
● Identify PATTERNS that differentiate effective vs ineffective approaches
● Extract RULES that can guide future similar situations
● Focus on UNDERLYING MECHANISMS rather than surface-level differences

{trajectory}
OUTPUT FORMAT:
Generate up to 5 comparative insights as JSON objects:
```json
[
{{
    "when_to_use": "Specific scenarios where this comparative insight applies",
    "experience": "Detailed comparison highlighting why success approach works better",
    "tags": ["comparative_analysis", "success_factors", "relevant_keywords"],
    "confidence": 0.8,
    "step_type": "reasoning|action|observation|decision",
    "tools_used": ["list", "of", "tools"]
}}
]
```"""

MEMORY_VALIDATION_PROMPT = """You are an expert AI analyst tasked with validating the \
quality and usefulness of extracted step-level task memories.

Your task is to access whether the extracted task memory is actionable, accurate, and valuable for future agent executions.

VALIDATION CRITERIA:
● ACTIONABILITY: Is the task memory specific enough to guide future actions?
● ACCURACY: Does the task memory correctly reflect the patterns observed?
● RELEVANCE: Is the task memory applicable to similar future scenarios?
● CLARITY: Is the task memory clearly articulated and understandable?
● UNIQUENESS: Does the task memory provide novel insights or common knowledge?

# Task Memory to Validate
Condition: {condition}
Task Memory Content: {task_memory_content}

OUTPUT FORMAT:
Provide validation assessment:
```json
{{
"is_valid": true/false,
"score": 0.8,
"feedback": "Detailed explanation of validation decision",
"recommendations": "Suggestions for improvement if applicable"
}}

Score should be between 0.0 (poor quality) and 1.0 (excellent quality).
Mark as invalid if score is below 0.3 or if there are fundamental issues with the task memory."""


@dataclass
class ReMePrompt:
    comparative_memory_prompt: str = COMPARATIVE_MEMORY_PROMPT
    success_memory_prompt: str = SUCCESS_MEMORY_PROMPT
    failure_memory_prompt: str = FAILURE_MEMORY_PROMPT
    comparative_all_memory_prompt: str = COMPARATIVE_ALL_MEMORY_PROMPT
    memory_validation_prompt: str = MEMORY_VALIDATION_PROMPT

ReMePrompts = ReMePrompt()

