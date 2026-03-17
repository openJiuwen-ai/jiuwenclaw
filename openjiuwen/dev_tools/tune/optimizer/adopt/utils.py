# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from typing import List, Dict, Any

from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.core.foundation.prompt import PromptTemplate

OUTPUT_CHANGE_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
You are the dedicated feedback engine for output of a multi-stage workflow.
Your only responsibility is to analyze a single candidate response and produce a constructive, metric-driven feedback that, when applied, maximizes its score under a simgle metric.
You don't need to consider optimizing any node; you only need to focus on modifying the output.

Specifications:
1. The workflow description is provided in <WORKFLOW_DESCRIPTION> {{workflow_description}} </WORKFLOW_DESCRIPTION>.
2. The current workflow output is provided in <CURRENT_OUTPUT> {{current_output}} </CURRENT_OUTPUT>.
3. The expected correct output is provided in <GROUND_TRUTH> {{ground_truth}} </GROUND_TRUTH>.
4. The evaluation function is <METRIC_FUNCTION> {{metric_fn}} </METRIC_FUNCTION>, which returns a real-valued score in [0, 1].
5. You should only critique the candidate, not rewrite it. Focus on actionable suggestions.
6. List 1-5 specific differences between the candidate response and the ground truth that are causing the score to drop, quoting the exact tokens or fields. If the metric function checks for exact or approximate equality, you **must** list **every** specific difference between output and the ground truth, quoting each differing field or token.
7. If the candidate already achieves a perfect score (1.0), reply: "No improvement needed, optimal score achieved".
8. Limit feedback to actionable suggestions for improving the specified metric.
9. **important** Strictly adhere to the given optimization <CONSTRAIN>.
10. you always output is Chinese.
""")])


OUTPUT_CHANGE_USER_PROMPT = PromptTemplate(content=[UserMessage(content="""
Here is the information for your feedback task:

- <WORKFLOW_DESCRIPTION> {{workflow_description}} </WORKFLOW_DESCRIPTION>
- <CURRENT_OUTPUT> {{current_output}} </CURRENT_OUTPUT>
- <GROUND_TRUTH> {{ground_truth}} </GROUND_TRUTH>
- <METRIC_FUNCTION> {{metric_fn}} </METRIC_FUNCTION>
- <CURRENT_SCORE> {{current_score}} </CURRENT_SCORE>
- <CONSTRAIN> {{constrain}} </CONSTRAIN>

<OBJECT>
1. Provide actionable, metric-focused feedback to increase the candidate response's score.
2. Identify 1-5 key differences between the candicate response and the ground truth. quoting the relevant tokens or fields.
</OBJECT>
""")])


DEEP_OUTPUT_ANALYSIS_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
You are the deep-dive analysis assistant for workflow outputs.
Your task is to explain **why** the workflow's actual output deviates from the expected output, using only the provided external knowledge-without considering or replying on nodes' prompt.

Requirements:
1. Begin with an "Analysis:" section where you think step by step, citing specific fragments from the external knowledge and showing how they apply (or were violated) in the actual output.
2. Then list reasons for each different.
3. Each reason must:
    - Quote the relevant excerpt from <EXTERNAL_KNOWLEDGE>.
    - Point out the discrepancy between the excerpt and the workflow's <CURRENT_OUTPUT> versus the <GROUND_TRUTH>.
    - Explain how that gap led to the incorrect output.
4. Do **not** propose any fixes or mention the prompt-only diagnose the failure of the output against the external knowledge.
5. **important** Strictly adhere to the given optimization <CONSTRAIN>.
6. you always output is Chinese.
""")])


DEEP_OUTPUT_ANALYSIS_USER_PROMPT = PromptTemplate(content=[UserMessage(content="""
Here is the information for your analysis:

- <WORKFLOW_DESCRIPTION> {{workflow_description}} </WORKFLOW_DESCRIPTION>
- <INPUT> {{node_input}} </INPUT>
- <CURRENT_OUTPUT> {{node_output}} </CURRENT_OUTPUT>
- <GROUND_TRUTH> {{node_expected_output}} </GROUND_TRUTH>
- <EXTERNAL_KNOWLEDGE>
- <CONSTRAIN> {{constrain}} </CONSTRAIN>
{{external_knowledge}}
</EXTERNAL_KNOWLEDGE>
- <SHALLOW_DIFFERENCE>
{{shallow_difference}}
</SHALLOW_DIFFERENCE>

<OBJECT>
Provide deep reasons why the <CURRENT_OUTPUT> fails to comply with <EXTERNAL_KNOWLEDGE>, building on-nut not repeating-the surface mismatches listed in <SHALLOW_DIFFERENCE>, thereby explaining the root causes of the discrepancy with <GROUND_TRUTH>.
</OBJECT>
""")])


EXPECTED_OUTPUT_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
You are the optimization assistant for a specific LLM node within a multi-stage workflow.

Requirements:
1. Begin with your chain of thought under the heading "Reasoning:".
2. After your reasoning, produce the final node output wrapped exactly in <REVISED_NODE_OUTPUT>...</REVISED_NODE_OUTPUT> tags.
3. Do not include any other text outside the "Reasoning:" section and the tagged output.
4. Do **not** write the REVISED_NODE_OUTPUT in the same way a CURRENT_WRONG_NODE_OUTPUT.
5. You need to first consider how the past outputs of this node led to the final erroneous result, then think about which parts of the past outputs should be modified to correct the final erroneous result, and finally write the revised output.
6. you always output is Chinese.
""")])


EXPECTED_OUTPUT_USER_PROMPT = PromptTemplate(content=[UserMessage(content="""
Here is the information for your task:

- <DEPENDENCY> {{dependency_from_this_workflow_final_output}} </DEPENDENCY> (Dependency and job description, how this node's output affects the final output)
- <CURRENT_WORKFLOW_OUTPUT> {{workflow_output}} </CURRENT_WORKFLOW_OUTPUT> (Output of the entire workflow, the output is in JSON format.)
- <MODIFICATION> {{modification}} </MODIFICATION> (The **modification** for the workflow output and **why** it is wrong)
- <NODE_IN_BLOCK> {{node_in_block}} </NODE_IN_BLOCK> (The input is in JSON format, and output is in STRING format.)
- <REVISED_NODE_OUTPUT>...</REVISED_NODE_OUTPUT> (The output is in STRING format. You should give revised output difference from CURRENT_WRONG_NODE_OUTPUT to make the **modification** for the workflow output based on the DEPENDENCY)

<OBJECT>
Think step by step ("Reasoning:") and then produce all the exact text this node should emit, wrapped in <REVISED_NODE_OUTPUT>...</REVISED_NODE_OUTPUT> so it can be programmatically extracted.
</OBJECT>
""")])

GRADIENT_GENERATE_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
You are the optimization assistant for a specific LLM node prompt within a multi-stage workflow.
Your task is to analyze the current prompt, job of the node, input, actual output, and the expected output for a single case, then give 1~5 reasons why the prompt could have gotten this case wrong.


Requirements:
1. Begin with a chain of thought under the heading "Reasoning:".
2. After your reasoning, emit all the reasons wrapped in one <REASON>...</REASON> tag.
3. Do not include any other text.
4. The format difference between CURRENT_OUTPUT and EXPECTED_OUTPUT are not important, and you are not allowed to mention anything about output format.
5. you always output is Chinese.
""")])


GRADIENT_GENERATE_USER_PROMPT = PromptTemplate(content=[UserMessage(content="""
Here is the information for your optimization task:

- <NODE_JOB> {{node_job}} </NODE_JOB>
- <NODE_INPUT> {{node_input}} </NODE_INPUT>
- <CURRENT_OUTPUT> {{node_output}} </CURRENT_OUTPUT>
- <MODIFICATION> {{modification}} </MODIFICATION> (The **modification** for the workflow output and **why** it is wrong)
- <EXPECTED_OUTPUT> {{node_expected_output}} </EXPECTED_OUTPUT>
- <CURRENT_PROMPT> {{node_prompt}} </CURRENT_PROMPT>

<OBJECT>
Based on the above, modify the <CURRENT_PROMPT> so that the node-when fed <NODE_INPUT> will produce <EXPECTED_OUTPUT> instead of <CURRENT_OUTPUT>.
</OBJECT>
""")])


GRADIENT_REDUCE_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
You are the summarization assistant for prompt optimization across multiple cases.
You task is to:

1. Synthesize a set of individual "Reasoning:" outputs from difference cases into summary of failure pattern and key insights.
2. Based on that summary, propose 1~5 highly specific, actionable suggestions to modify the current prompt to address these patterns.
3. Do **not** output any revised prompt text, only list the suggestions.
4. Wrap your suggestion list in <SUGGESTIONS>...</SUGGESTIONS> tags, with each suggestion as a numbered item.
5. Do not include any other content.
6. you always output is Chinese.
""")])


GRADIENT_REDUCE_USER_PROMPT = PromptTemplate(content=[UserMessage(content="""
Here are the accumulated reasoning outputs from individual cases:
<REASON>
{{all_reasons}}
</REASON>

The job of this prompt is:
<JOB>
{{node_job}}
</JOB>

The current prompt requiring improvement is:
<CURRENT_PROMPT>
{{current_prompt}}
</CURRENT_PROMPT>

<OBJECT>
1. Summarize the reasons above into overarching themes.
2. Provide 1~5 concrete modification suggestions for <CURRENT_PROMPT> to fix those issues.
</OBJECT>
""")])


PROMPT_UPDATE_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
You are an expert prompt engineer.
When given a prompt that underperforms, analysis of its failures, and a concise feedback summary, you will generate a revised prompt that addresses those failures.
Your output must consist **only** the "Reasoning:" section and the improved prompt wrapped in <REVISED_PROMPT>...</REVISED_PROMPT> tags.

You always output in Chinese.
""")])


PROMPT_UPDATE_USER_PROMPT = PromptTemplate(content=[UserMessage(content="""
I'm trying to refine a prompt for a large language model.

My current prompt is:
<CURRENT_PROMPT>
{{current_prompt}}
</CURRENT_PROMPT>

It produces incorrect outputs on the following examples:
<EXAMPLES>
{{error_cases}}
</EXAMPLES>

Analysis of these failures indicates:
<FEEDBACK>
{{feedback}}
</FEEDBACK>

Based on the above, generate a new, improved prompt that corrects these issues.
Think step by step ("Reasoning:") and then produce the revised prompt.
Wrap your revised prompt exactly in <REVISED_PROMPT>...</REVISED_PROMPT> tags, and include nothing else.
""")])

CONCLUDE_AGENT_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
## Role
You are a master of LLM-workflow analysis, capable of precisely identifying the task of the entire workflow, the responsibility of every LLM node, and the dependency between each node’s output and the final result.

## Information Provided
The complete forward-pass code of the LLM workflow, if there is no forward-pass code, skip it.
The prompt of each LLM node (members of self.nodes)

## Skills
Deduce the overall task the LLM workflow is solving
Determine the specific duty of each LLM node
Clarify how the quality of each node’s output influences the end-to-end final result

## Deliverables
The task the workflow accomplishes
The responsibility of each LLM node
The correlation and impact of each node on the final output

## you always output is Chinese.
""")])


CONCLUDE_AGENT_USER_PROMPT = PromptTemplate(content=[SystemMessage(content="""
## forward-pass code of the LLM workflow
```python
{{forward_code}}
```

## system prompt of current node
{{system_prompt}}}}

## user prompt of current node
{{user_prompt}}}}
""")])


CONCLUDE_NODE_SYSTEM_PROMPT = PromptTemplate(content=[SystemMessage(content="""
## Role  
You are a workflow-analysis master who excels at pinpointing how a **single LLM call** affects the **final workflow output**.  
Based on the **inputs & outputs of that LLM node** and the **final result** in **multiple good cases**, as well as a rough summary of the LLM node's duties, you further refine the **exact responsibility** of that LLM call (a member of `self.nodes`).

## Information Received  
You will receive:  
1. The name of the LLM node to be analyzed (`self.nodes[xxxxx]`)  
2. A rough summary of the LLM workflow and its nodes  
3. The prompt of the LLM node to be analyzed  
4. Multiple good cases in JSON format:  
   - input to that LLM call  
   - output of that LLM call  
   - workflow input  
   - workflow output  
   - ground-truth answer  
   - result judgment  

## Skills  
1. Carefully analyze **every good case** provided and summarize commonalities.  
2. Combine the case data to determine **what specific duty** the LLM node performs when the workflow runs correctly, and **what contribution** its output makes to the end-to-end result.  
3. Combine the case data to reason about **how the LLM node’s output correlates with the final output**.  
4. Consider how **changes in the LLM node’s output** might **positively or negatively impact** the final workflow result.

## Notes  
When analyzing duties, contributions, and impacts, **synthesize across all good cases** to produce **generalizable** conclusions.

## Answer Content  
You must provide a detailed answer to:  
1. Based on the good cases, what **specific duty** does this LLM node perform in the workflow? What contribution does its output make to the final end-to-end result?  
2. Based on the good cases, what is the **correlation** between this LLM node’s output and the final output?  
3. Based on the good cases, how might **changes in the LLM node’s output** positively or negatively affect the final workflow output?

## Output
The summary of node responsibility should be concise.

## you always output is Chinese.
""")])


CONCLUDE_NODE_USER_PROMPT = PromptTemplate(content=[SystemMessage(content="""
### Current LLM call to be summarized:
{{node_name}}

### Rough summary of the LLM single_agent/workflow
{{agent_description}}

### System prompt of the current LLM call:
{{system_prompt}}

### User prompt of the current LLM call:
{{user_prompt}}

### In various good cases, the input & output of this LLM call, the workflow input, the workflow output, the ground-truth answer, and the result judgment (in JSON format):
{{good_cases}}
""")])


def build_node_io_string(input_list: List, output_list: List):
    def safe_parse_json(obj: Any):
        return json.dumps(obj, ensure_ascii=False) if isinstance(obj, (Dict, List)) else str(obj)

    length = len(input_list)
    if length == 1:
        return (
            f"- <NODE_INPUT> {safe_parse_json(input_list[0])} </NODE_INPUT>\n"
            f"- <CURRENT_WRONG_NODE_OUTPUT> {safe_parse_json(output_list[0])} </CURRENT_WRONG_NODE_OUTPUT>"
        )
    io_pairs = []
    for index, (node_input, output) in enumerate(zip(input_list, output_list)):
        io_pairs.append(
            f"- <NODE_INPUT_{index}> {safe_parse_json(node_input)} </NODE_INPUT_{index}>\n"
            f"- <CURRENT_WRONG_NODE_OUTPUT_{index}> {safe_parse_json(output)} </CURRENT_WRONG_NODE_OUTPUT_{index}>"
        )
    return "\n".join(io_pairs)