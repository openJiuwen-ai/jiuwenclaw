# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Centralized management of common PromptTemplates for LLM optimizer.
"""
from openjiuwen.core.foundation.prompt import PromptTemplate

# ========== Instruction Optimization Templates ==========
PROMPT_INSTRUCTION_OPTIMIZE_TEMPLATE = PromptTemplate(content="""
You are a prompt optimization expert. Your task is to optimize the prompt
based on the provided information. The specific information is as follows:
First, please read the following prompt:
<prompt_base>
{{prompt_instruction}}
</prompt_base> 

Your available tools and API descriptions are as follows:
<tools_description>
{{tools_description}}
</tools_description> 

The error cases that occurred during the application of the prompt are as follows:
<bad_cases>
{{bad_cases}}
</bad_cases> 

Reflections on these error cases:
<reflections_on_bad_cases>
{{reflections_on_bad_cases}}
</reflections_on_bad_cases> 

When optimizing the prompt template, please follow these requirements:
1. In the <思考> (THINKING) tag, please conduct an in-depth and comprehensive
   analysis of the parts of the prompt that may have caused errors, based on
   the error examples and their corresponding reflections. The analysis should
   cover: identification of error causes, problems in the original prompt, and
   specific modifications that can effectively avoid these issues.
2. In the <PROMPT_OPTIMIZED> tag, output the optimized version of the prompt
   based on the above analysis.
3. The analysis should focus on the specific causes of problems,
   systematically optimizing based on template structure, semantic expression,
   and format specifications.
4. During optimization, ensure complete information expression, logical rigor,
   and do not omit important content or introduce vague expressions.
5. Do not directly use the given examples, and do not add specific information
   from the examples into the prompt. You can summarize through abstraction
   or rewriting.

Output format:
<思考>
[Here provide your detailed optimization analysis of the prompt]
</思考>
<PROMPT_OPTIMIZED>
[Here output the optimized prompt]
</PROMPT_OPTIMIZED>
Please ensure that the optimized content can effectively avoid the previously occurring error cases.
""")

PROMPT_INSTRUCTION_OPTIMIZE_BOTH_TEMPLATE = PromptTemplate(content="""
You are a prompt optimization expert. Your task is to optimize the prompt
based on the provided information. The specific information is as follows:
First, please read the following system and user prompts:
<system_prompt_base>
{{system_prompt}}
</system_prompt_base> 

<user_prompt_base>
{{user_prompt}}
</user_prompt_base> 

Your available tools and API descriptions are as follows:
<tools_description>
{{tools_description}}
</tools_description> 

The error cases that occurred during the application of the prompt are as follows:
<bad_cases>
{{bad_cases}}
</bad_cases> 

Reflections on these error cases:
<reflections_on_bad_cases>
{{reflections_on_bad_cases}}
</reflections_on_bad_cases> 

When optimizing the prompt template, please follow these requirements:
1. In the <THINKING> tag, please conduct an in-depth and comprehensive
   analysis of the parts of the prompt that may have caused errors, based on
   the error examples and their corresponding reflections. The analysis should
   cover: identification of error causes, problems in the original prompt, and
   specific modifications that can effectively avoid these issues.
2. In the <SYSTEM_PROMPT_OPTIMIZED> and <USER_PROMPT_OPTIMIZED> tags, output
   the optimized system and user prompts based on the above analysis.
3. The analysis should focus on the specific causes of problems,
   systematically optimizing based on template structure, semantic expression,
   and format specifications.
4. During optimization, ensure complete information expression, logical rigor,
   and do not omit important content or introduce vague expressions.
5. Do not directly use the given examples, and do not add specific information
   from the examples into the prompt. You can summarize through abstraction
   or rewriting.

Output format:
<THINKING>
[Here provide your detailed optimization analysis of the prompt]
</THINKING>
<SYSTEM_PROMPT_OPTIMIZED>
[Here output the optimized system prompt]
</SYSTEM_PROMPT_OPTIMIZED>
<USER_PROMPT_OPTIMIZED>
[Here output the optimized user prompt]
</USER_PROMPT_OPTIMIZED>
Please ensure that the optimized content can effectively avoid the previously occurring error cases.
""")

CREATE_PROMPT_TEXTUAL_GRADIENT_TEMPLATE = PromptTemplate(content="""
As a prompt optimization expert, my goal is to help the agent complete tasks efficiently and successfully.
The current system and user prompts are:
<system_prompt_base>
{{system_prompt}}
</system_prompt_base> 

<user_prompt_base>
{{user_prompt}}
</user_prompt_base> 

The available tools involved in the prompt are as follows:
<tools_description>
{{tools_description}}
</tools_description> 

However, this prompt failed to generate correct results in the following instances:
<bad_cases>
{{bad_cases}}
</bad_cases> 

Please provide detailed feedback analyzing why the instruction may have failed.
For each instance, specifically explain the problems in the instruction, explain
why the agent may have misunderstood the instruction, and suggest how to make
the instruction clearer and more precise.
For failure cases caused by model invocation errors, you do not need to analyze them.
Please wrap each piece of feedback with <INS> and </INS>.
""")

# ========== Common Templates ==========
CREATE_BAD_CASE_TEMPLATE = PromptTemplate(content="""
[question]: {{question}}
[expected answer]: {{label}}
[assistant answer]: {{answer}}
[reason]: {{reason}}
=== 
""")

PLACEHOLDER_RESTORE_TEMPLATE = PromptTemplate(content="""
As a prompt optimization expert, your task is to complete the placeholders in the prompt based on the given information.
Original prompt:
<original_prompt>
{{original_prompt}}
</original_prompt> 

Revised prompt:
<revised_prompt>
{{revised_prompt}}
</revised_prompt> 

The complete set of placeholders in the original prompt is:
<all_placeholders>
{{all_placeholders}}
</all_placeholders> 

After comparison, the revised prompt is missing the following placeholders compared to the original prompt:
<missing_placeholders>
{{missing_placeholders}}
</missing_placeholders> 

Your goals are:
1. Restore all missing placeholders to the revised prompt <revised_prompt>,
   reference the original prompt and add placeholders to appropriate positions.
2. Placeholders should be added to the prompt in double curly brace format, e.g., "{{placeholder_name}}".
3. Except for necessary modifications to placeholders, do not modify the prompt content.
4. Directly return the prompt with placeholders added, without adding thought process or any other additional content.
""")
