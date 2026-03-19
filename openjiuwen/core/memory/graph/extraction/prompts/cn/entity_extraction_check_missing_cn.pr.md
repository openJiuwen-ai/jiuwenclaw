`#system#`你是一个专门检查上下文信息中是否还有未提取实体（Entity）的可靠助手。
你的任务是识别、消歧并分类当前消息中的对话参与者及其他重要实体。
`#user#`# 上下文信息{{source_description}}

{{context}}

<已提取实体>
{{extracted_entities}}
</已提取实体>

# 你的任务
请基于上述上下文信息，确定是否有任何实体尚未被提取，并列出所有未提取实体。{{extra_message}}