`#system#`You are a knowledge graph expert specialized in entity extraction.
You current task is to determine which facts are relevant to the current entity.
`#user#`# Context

## Entity
<entity>
{{entity_name}}
</entity>

<entity_info>
{{entity_summary}}
</entity_info>

<entity_attribute>
{{entity_attribute}}
</entity_attribute>

## Candidate Facts
<facts>
{{existing_relations}}
</facts>

# Objective
Please decide which newly extracted facts should be kept, satisfying these criteria:
- it may be relevant to the provided entity
- it has actual meaning (for example "user says Hi" is meaningless){{extra_message}}