# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.foundation.prompt import PromptTemplate


LLM_METRIC_TEMPLATE = PromptTemplate(content="""
You are an answer verification expert responsible for checking the semantic and
conclusion consistency between the given model response and the expected answer.
Please determine if the model response is consistent with the expected answer
based on the following criteria:

- If the model response and expected answer have consistent meaning, return `true`.
- If the model response and expected answer have inconsistent meaning, return `false`.
- Pay special attention to distinguish between dialogues and tool calls, as they
  usually cannot be judged as consistent based on semantics.
- Briefly analyze the reasons why the model response and expected answer are
  inconsistent, combining with the user question and expected answer.

The following are custom verification rules added by the user. If they conflict
with the above rules, the user's custom rules should take precedence. Please
strictly follow them:
{{user_metrics}}

Output JSON format:
```json
{
"result": true/false,
"reason": "Verification reason"
}
```

[Question]: {{question}}

The following are the model response and expected answer to be compared:
[Expected Answer]: {{expected_answer}}

[Model Response]: {{model_answer}}

Please verify and return the result:
"""
)

LLM_METRIC_RETRY_TEMPLATE = PromptTemplate(content="""
You are an answer verification expert responsible for fixing non-standard evaluation results.

## Original Evaluation Result to Assess
[Question]: {{question}}
The following are the model response and expected answer to be compared:
[Expected Answer]: {{expected_answer}}
[Model Response]: {{model_answer}}

## Non-standard Evaluation Result Received
However, a non-standard evaluation result has been received, which cannot be correctly parsed into JSON format:
<EVALUATED_RESULT>
{{nonstandard_evaluated_result}}
</EVALUATED_RESULT>

## Format Correction
Please correct the format of the current evaluation result, reason why the
above evaluation result could not be parsed by JSON, correct it, and return
the correct evaluation format as follows:
Output JSON format:
```json
{
"result": true/false,
"reason": "Verification reason"
}
```

## Requirements
- The generated JSON must be wrapped with ```json```
- Pay attention to whether there are non-standard quotation marks in the
  evaluation result, such as incorrect use of double and single quotes,
  nested quotes, etc.

Please verify and return the result:
""")
