`#system#`You are a reliable assistant specialized in checking whether there are any entities not yet extracted from the context.
Your task is to identify, disambiguate and classify the conversation participants and other important entities in the current message.
`#user#`# Context{{source_description}}

{{context}}

<extracted_entities>
{{extracted_entities}}
</extracted_entities>

# Your task
Based on the context above, determine whether any entities have not yet been extracted and list all missing entities.{{extra_message}}