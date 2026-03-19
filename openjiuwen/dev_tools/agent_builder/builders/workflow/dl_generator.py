# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, List, Any, Callable

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model, SystemMessage, UserMessage
from openjiuwen.core.foundation.prompt import PromptTemplate

from openjiuwen.dev_tools.agent_builder.builders.workflow.prompts import EMPTY_RESOURCE_CONTENT
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_assets import (
    COMPONENTS_INFO,
    SCHEMA_INFO,
    EXAMPLES,
)

logger = LogManager.get_logger("agent_builder")

# DL 生成提示词（从原文件读取，这里先定义常量）
DL_GENERATE_SYSTEM_PROMPT_TEMPLATE: str = """## 人设
你是一名工作流大师，你可以基于给定的任务描述思考并创建由节点连接组成的具体流程图。

## 任务描述
- 你的任务是根据给定的工作流设计文档，使用所提供的节点信息及其 schema，生成一个字符串 json 来表征工作流。

## 节点信息
{{components}}

## 节点schema
schema中会出现的所有字段说明：
```json
{
  "id": "节点在工作流中的唯一标识符，用于被其他节点引用",
  "type": "节点类型",
  "description": "对节点用途的文字说明",
  "next": "节点执行完毕后默认跳转的下一个节点ID（仅部分节点使用）",
  "parameters": {
    "inputs": [{"name": "输入参数的名称", "value": "输入参数的值或来源"}],
    "outputs": [{"name": "输出参数的名称", "description": "对该输出参数的含义或用途的说明"}],
    "configs": {
      "system_prompt": "LLM 节点使用的系统提示词",
      "user_prompt": "用户提示词模板",
      "template": "用于 Output、End 等节点的模板",
      "prompt": "用于 IntentDetection、Questioner 等节点的文本提示配置",
      "code": "Code 节点中实际执行的 Python 代码字符串",
      "tool_id": "插件节点使用的工具唯一标识",
      "tool_name": "插件的名称"
    },
    "conditions": [{
      "branch": "分支标识符",
      "description": "对该分支适用场景的说明",
      "expression": "用于判断是否进入该条件分支的逻辑表达式",
      "next": "当前条件命中后将跳转到的下一个节点 ID"
    }]
  }
}
```

各节点schema使用说明：
{{schema}}

## 可以使用的插件信息
{{plugins}}

## 规则限制
1. 绝对遵守各节点的schema格式和限制
2. parameters的inputs中的元素为引用赋值时，只能引用其他节点中parameters中outputs中的变量
3. 输出字符串形式的json，模仿示例的字符串形式的json进行输出

## 示例（示例内容均遵循标准schema）
{{examples}}
"""

DL_REFINE_USER_PROMPT_TEMPLATE: str = """需要你按照用户输入的要求，基于已有流程图和工作流内容，进行修改和完善，确保其符合要求并且没有错误。
## 用户输入
{{user_input}}

## 已有流程图内容
{{exist_mermaid}}

## 已有工作流内容
{{exist_dl}}
"""

DL_GENERATE_SYSTEM_TEMPLATE: PromptTemplate = PromptTemplate(
    content=[UserMessage(content=DL_GENERATE_SYSTEM_PROMPT_TEMPLATE)]
)
DL_REFINE_USER_TEMPLATE: PromptTemplate = PromptTemplate(
    content=[UserMessage(content=DL_REFINE_USER_PROMPT_TEMPLATE)]
)


class DLGenerator:
    """DL generator.

    Generates workflow definition language (DL) from workflow design documents, 
    supports both generation and refinement modes.

    Example:
        ```python
        generator = DLGenerator(llm_service)
        dl = generator.generate(query, resource)
        dl = generator.refine(query, resource, exist_dl, exist_mermaid)
        ```
    """

    _RESOURCE_PROMPT_PLACEMENTS: List[str] = ["plugins"]

    def __init__(self, llm: Model) -> None:
        """
        Initialize DL generator.

        Args:
            llm: LLM service instance
        """
        self.llm: Model = llm
        self.reflect_prompts: List[Any] = []
        self.components_info: str
        self.schema_info: str
        self.examples: str
        self.components_info, self.schema_info, self.examples = (
            self.load_schema_and_examples()
        )

    @staticmethod
    def load_schema_and_examples() -> tuple[str, str, str]:
        """
        Load schema and examples.

        Returns:
            Tuple[str, str, str]: (components_info, schema_info, examples)
        """
        return COMPONENTS_INFO, SCHEMA_INFO, EXAMPLES

    def generate(
            self,
            query: str,
            resource: Dict[str, List[Dict[str, Any]]]
    ) -> str:
        """
        Generate DL.

        Args:
            query: Workflow design content or query
            resource: Resource dictionary

        Returns:
            Generated DL string
        """
        system_prompt = self._update_prompt(resource)
        return self._execute(query, system_prompt)

    def refine(
            self,
            query: str,
            resource: Dict[str, List[Dict[str, Any]]],
            exist_dl: str,
            exist_mermaid: str
    ) -> str:
        """
        Refine DL.

        Args:
            query: User refinement requirements
            resource: Resource dictionary
            exist_dl: Existing DL
            exist_mermaid: Existing Mermaid code

        Returns:
            Refined DL string
        """
        system_prompt = self._update_prompt(resource)
        user_content = DL_REFINE_USER_TEMPLATE.format({
            "user_input": query,
            "exist_dl": exist_dl,
            "exist_mermaid": exist_mermaid,
        }).to_messages()[0].content
        return self._execute(user_content, system_prompt)

    def _execute(self, query: str, system_prompt: str) -> str:
        """
        Execute DL generation.

        Args:
            query: User query
            system_prompt: System prompt

        Returns:
            Generated DL string
        """
        prompts = [
                      SystemMessage(content=system_prompt),
                      UserMessage(content=query)
                  ] + self.reflect_prompts

        generated_dl = asyncio.run(self.llm.invoke(prompts)).content
        logger.debug("DL generation completed", output_length=len(generated_dl))
        return generated_dl

    def _update_prompt(
            self,
            resource: Dict[str, List[Dict[str, Any]]]
    ) -> str:
        """
        Update prompt, insert resource information.

        Args:
            resource: Resource dictionary

        Returns:
            Updated system prompt
        """
        plugins_value = (resource or {}).get("plugins")
        plugins_str = (
            EMPTY_RESOURCE_CONTENT
            if not plugins_value
            else "\n".join(str(item) for item in plugins_value)
        )
        format_dict = {
            "components": self.components_info,
            "schema": self.schema_info,
            "examples": self.examples,
            "plugins": plugins_str,
        }
        system_content = DL_GENERATE_SYSTEM_TEMPLATE.format(
            format_dict
        ).to_messages()[0].content
        return system_content
