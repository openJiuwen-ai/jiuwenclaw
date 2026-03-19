`#system#`You are a helpful assistant specialized in entity merging.
`#user#`# Context

## Target Entity
<entity>
{{entity_name}}
</entity>

<entity_info>
{{entity_summary}}
</entity_info>

<entity_attribute>
{{entity_attribute}}
</entity_attribute>

## Source Entities
{{entities_to_merge}}

# Objective
Please use the information in source entities and update summary and attributes for the target entity.

## Instructions
1. **Summary Writing**
   - Summary must be only based on **Target Entity and Source Entities**.
   - Content must be **directly** relevant to target entity, and it must not exceed **{{summary_target}} words**.
   - Do not introduce any external knowledge or make up information.
   - Ouput a list of bulletpoints, each containing a concise fact.
   - Please output in **ENGLISH**, for specific words it's fine to use source material's language but main body should be in English.

2. **Attribute Update**
   - Based on provided context, extract attributes for the entity ({{entity_name}}).
   - If entity attributes are provided but outdated or incorrect, update them to have correct values.
   - Do not infer or make up information.
   - Attribute extraction result must be a valid JSON object.

3. **Update**
   - If summary and attributes are already up-to-date in Target Entity, leave them blank.
   - Summary and attributes must be about this specific entity ({{entity_name}}).
   - Do not include any information irrelevant to the entity.{{extra_message}}