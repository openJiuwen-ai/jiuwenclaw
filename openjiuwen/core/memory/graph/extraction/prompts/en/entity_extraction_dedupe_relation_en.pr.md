`#system#`You are a helpful assistant specialized in merging duplicate relations based on given context.
`#user#`# Context{{source_description}}

{{context}}

<entities>
{{entities}}
</entities>

<existing_relations>
{{existing_relations}}
</existing_relations>

<new_relation>
{{new_relation}}
</new_relation>

# Objective
Please check all relations in existing_relations list, then according to provided context, confirm whether the new relation is a duplicate of any of the existing relations between the two entities. If so, output the merged relation according to output format instructions.

## Instructions
Only consider a relation to be duplicate when it has the same meaning or is an updated state compared to existing ones.
DO NOT merge relations when they are relevant but different.

1. If the new relation is unique, set `need_merging` to false and leave the other fields blank.
2. `combined_content` must be a direct copy or concise rephrase coming from original context and/or existing relations.
3. **DO NOT** guess any temporal information not presented in provided context!

Temporal extraction rules:
- Use **ISO 8601 format** (like `2025-09-15T13:49:08+08:00` or `2025-09-15T05:49:08`).
- Use original relations' `valid_since` and `valid_until` data as reference when possible.
- When applicable, predict the time zone (like Chinese event → `+08:00`).
- If factual relation has clear start date → set `valid_since`.
- If factual relation has clear end date → set `valid_until`.
- No valid temporal information → set `null` to both fields.{{extra_message}}