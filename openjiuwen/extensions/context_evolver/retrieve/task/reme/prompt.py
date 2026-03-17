# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass

MEMORY_RERANK_PROMPT = """You are an expert AI analyst tasked with reranking retrieved \
experiences based on their relevance to a specific query.

Your task is to analyze the candidates and rank them by relevance, considering:
● DIRECT RELEVANCE: How directly applicable the experience is to the current query
● SITUATION SIMILARITY: How similar the experience context is to the current situation
● ACTIONABILITY: How actionable and specific the experience is
● QUALITY: The overall quality and clarity of the experience

# Current Query
{query}

# Candidate Experiences (Total: {num_candidates})
{candidates}

OUTPUT FORMAT:
Provide a ranked list of candidate indices (0-based) from most relevant to least relevant:
```json
{{
"ranked_indices": [2, 0, 4, 1, 3],
"reasoning": "Brief explanation of ranking rationale"
}}
```

Note: Include ALL candidate indices in the ranking, even if some are less relevant."""

MEMORY_REWRITE_PROMPT = """You are an expert AI assistant tasked with rewriting and \
reorganizing context content to make it more relevant and actionable for the current task.

Your task is to take the original context (containing multiple experiences) and rewrite it as a cohesive, task-specific guidance that directly addresses the current situation.

REWRITING GUIDELINES:
● RELEVANCE FOCUS: Emphasize the most relevant aspects of each experience. Prioritize the most relevant experiences. Use clear, direct language.
● ACTIONABLE INSIGHTS: Extract specific, actionable guidance. Make the context immediately actionable
● COHERENT NARRATIVE: Create a flowing narrative rather than disconnected tips
● SITUATIONAL AWARENESS: Adapt the guidance to the current situation

# Current Task/Query
{current_query}

# Original Context Content (Multiple Experiences)
{original_context}

OUTPUT FORMAT:
Provide the rewritten context:
```json
{{
"rewritten_context": "A cohesive, task-specific context message that reorganizes and adapts the original experiences for the current task. This should be written as a unified guidance rather than separate experience items.",
}}
```

Guidelines:
- Rewrite as a unified, flowing guidance
- Adapt terminology and examples to match the current task domain
- Consolidate overlapping insights into coherent recommendations
- Prioritize experiences most relevant to the current situation
- Make the guidance feel custom-written for this specific task"""


@dataclass
class ReMePrompt:
    rerank_prompt: str = MEMORY_RERANK_PROMPT
    rewrite_prompt: str = MEMORY_REWRITE_PROMPT


ReMePrompts = ReMePrompt()
