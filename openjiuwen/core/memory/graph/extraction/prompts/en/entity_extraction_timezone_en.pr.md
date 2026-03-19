`#system#`You are an expert in predicting possible timezones from text.
1. You must predict one or more likely timezones from provided context.
2. If there is insufficient information in context, use `UTC+00`.
`#user#`# Context{{source_description}}

{{context}}

# Objective
Please predict 1-3 possible timezones based on provided context

# Timezone Prediction Instructions
- Language and location names can be used to predict timezones (for example: events in China Shenzhen / speaking Chinese → `+08:00`).
- Output should be as concise as possible, only include 1-2 sentences in reasoning.
- When there is not sufficient information, you can predict UTC+00 and note `insufficient info` in reasoning.{{extra_message}}