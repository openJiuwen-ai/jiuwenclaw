`#system#`You are a helpful assistant specialized in merging duplicate entities based on given context.
`#user#`# Context

{{context}}

<existing_entities>
{{entities}}
</existing_entities>

<candidate_entities>
{{candidate_entities}}
</candidate_entities>

# Objective
Please check all entites in candidate_entities list, then confirm whether some of the candidates are duplicate of existing entities. If two existing entites are duplicate with each other, also merge them together.

## Instructions
Only consider an entity to be a duplicate when it points to the *same object or concept in the real world*.
Candidate entities are extracted from the current context, they might be duplicates of existing entities sharing the same name, especially ones that represent AI assistants or human users / speakers.

DO NOT mark entities as duplicates when:
- They are relevant but different.
- They have similar names or attributes, but pointing to different objects or concepts.{{extra_message}}